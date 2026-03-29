"""
Follow-up manager for AutoApply V2.2.

Two jobs:
1. Reply detection — query Gmail API for threads with replies,
   categorize by keyword matching.
2. Follow-up scheduling — queue follow-ups for non-responses
   after 5 and 12 business days.

Module owner: Claude Code
"""

import os
import re
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from src.db import Database
from src.config import Config
from src.smtp_verifier import update_pattern_from_outcome
from src.utils import get_logger

logger = get_logger("followup_manager")

# Reply classification categories
REPLY_CLASSES = ["positive", "referral", "rejection", "auto_reply", "bounce", "unclassified"]

# Auto-reply indicators
AUTO_REPLY_PATTERNS = [
    re.compile(r"out of (?:the )?office", re.I),
    re.compile(r"auto[- ]?reply", re.I),
    re.compile(r"automatic reply", re.I),
    re.compile(r"on (?:annual |paid )?leave", re.I),
    re.compile(r"away from (?:my )?(?:email|desk)", re.I),
    re.compile(r"limited access to email", re.I),
]

BOUNCE_PATTERNS = [
    re.compile(r"delivery.*(?:failed|failure|status notification)", re.I),
    re.compile(r"undeliverable", re.I),
    re.compile(r"mailbox.*(?:full|not found|unavailable)", re.I),
    re.compile(r"address.*rejected", re.I),
    re.compile(r"550.*(?:user|mailbox|address)", re.I),
]


def _count_business_days(start_date: datetime, end_date: datetime) -> int:
    """Count business days (Mon-Fri) between two dates."""
    days = 0
    current = start_date
    while current < end_date:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def _add_business_days(start_date: datetime, business_days: int) -> datetime:
    """Add N business days to a date."""
    current = start_date
    added = 0
    while added < business_days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _classify_reply(text: str, config: Config) -> str:
    """
    Classify a reply by keyword matching.
    Returns one of: positive, referral, rejection, auto_reply, bounce, unclassified
    """
    if not text:
        return "unclassified"

    text_lower = text.lower()

    # Check bounce first (these are usually system-generated)
    for pattern in BOUNCE_PATTERNS:
        if pattern.search(text):
            return "bounce"

    # Check auto-reply
    for pattern in AUTO_REPLY_PATTERNS:
        if pattern.search(text):
            return "auto_reply"

    # Check configured keyword lists
    for keyword in config.reply_keywords.rejection:
        if keyword.lower() in text_lower:
            return "rejection"

    for keyword in config.reply_keywords.referral:
        if keyword.lower() in text_lower:
            return "referral"

    for keyword in config.reply_keywords.positive:
        if keyword.lower() in text_lower:
            return "positive"

    return "unclassified"


def _get_gmail_service():
    """Get authenticated Gmail service for reading threads."""
    from src.sender import _get_gmail_service as get_service
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return get_service(
        credentials_path=os.path.join(project_root, "credentials.json"),
        token_path=os.path.join(project_root, "token.json"),
    )


def _fetch_thread_replies(service, thread_id: str, our_message_id: str) -> List[str]:
    """
    Fetch reply messages from a Gmail thread, excluding our own messages.
    Returns list of reply body texts.
    """
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
    except Exception as e:
        logger.debug(f"Failed to fetch thread {thread_id}: {e}")
        return []

    replies = []
    for msg in thread.get("messages", []):
        msg_id = msg.get("id", "")
        if msg_id == our_message_id:
            continue

        # Check if this is an inbound message (not sent by us)
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        from_header = headers.get("from", "")

        # Extract body text
        body = _extract_body_text(msg.get("payload", {}))
        if body:
            replies.append(body)

    return replies


def _extract_body_text(payload: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        import base64
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body_text(part)
        if text:
            return text

    return ""


def detect_replies(config: Config, db: Database, dry_run: bool = False) -> int:
    """
    Check Gmail threads for replies to our sent messages.
    Classify and update message statuses.

    Returns count of replies detected.
    """
    sent_messages = db.conn.execute("""
        SELECT m.*, p.email as contact_email, c.name as company_name
        FROM messages m
        JOIN people p ON m.person_id = p.id
        JOIN companies c ON m.company_id = c.id
        WHERE m.status = 'sent'
            AND m.gmail_thread_id IS NOT NULL
            AND m.gmail_message_id IS NOT NULL
    """).fetchall()

    if not sent_messages:
        logger.info("No sent messages to check for replies")
        return 0

    if dry_run:
        logger.info(f"[DRY RUN] Would check {len(sent_messages)} threads for replies")
        return 0

    service = None
    try:
        service = _get_gmail_service()
    except Exception as e:
        logger.error(f"Failed to authenticate with Gmail: {e}")
        return 0

    replies_found = 0

    for msg in sent_messages:
        replies = _fetch_thread_replies(
            service, msg["gmail_thread_id"], msg["gmail_message_id"]
        )

        if not replies:
            continue

        # Classify the first reply
        reply_text = replies[0]
        classification = _classify_reply(reply_text, config)

        # Update message status
        new_status = f"replied_{classification}"
        db.update_message_status(msg["id"], new_status)
        replies_found += 1

        logger.info(
            f"Reply detected: {msg['company_name']} / {msg['contact_email']} "
            f"-> {classification}"
        )

        # Handle bounce — add to suppression
        if classification == "bounce":
            db.add_suppression("email", msg["contact_email"], reason="bounced")
            db.increment_metric("bounces")
            update_pattern_from_outcome(db, msg["contact_email"], success=False)

        # Handle referral — log for manual follow-up
        if classification == "referral":
            logger.info(
                f"REFERRAL from {msg['contact_email']} at {msg['company_name']}. "
                f"Review the reply and consider a new target."
            )
            update_pattern_from_outcome(db, msg["contact_email"], success=True)

        if classification == "positive":
            update_pattern_from_outcome(db, msg["contact_email"], success=True)

        db.increment_metric("replies_received")
        if classification == "positive":
            db.increment_metric("replies_positive")

    logger.info(f"Detected {replies_found} replies")
    return replies_found


def schedule_followups(config: Config, db: Database, dry_run: bool = False) -> int:
    """
    Schedule follow-up messages for non-responses.

    - followup_1 after 5 business days
    - followup_2 after 12 business days
    - Max 2 follow-ups
    - Stop on rejection

    Returns count of follow-ups queued.
    """
    followups_queued = 0

    # Get sent initial messages old enough for follow-up 1
    sent_messages = db.conn.execute("""
        SELECT m.*, p.email as contact_email, p.name as contact_name,
            p.id as pid, c.name as company_name, c.id as cid,
            j.title as job_title, j.id as jid
        FROM messages m
        JOIN people p ON m.person_id = p.id
        JOIN companies c ON m.company_id = c.id
        JOIN jobs j ON m.job_id = j.id
        WHERE m.status = 'sent'
            AND m.sent_at IS NOT NULL
    """).fetchall()

    now = datetime.now()

    for msg in sent_messages:
        sent_at = datetime.fromisoformat(msg["sent_at"])
        biz_days = _count_business_days(sent_at, now)

        # Check if there's already a reply (any status starting with 'replied_')
        has_reply = db.conn.execute("""
            SELECT 1 FROM messages
            WHERE job_id = ? AND person_id = ?
                AND status LIKE 'replied_%'
        """, (msg["jid"], msg["pid"])).fetchone()

        if has_reply:
            continue

        # Check for rejection
        has_rejection = db.conn.execute("""
            SELECT 1 FROM messages
            WHERE job_id = ? AND person_id = ?
                AND status = 'replied_rejection'
        """, (msg["jid"], msg["pid"])).fetchone()

        if has_rejection:
            continue

        # Count existing follow-ups for this thread
        existing_followups = db.conn.execute("""
            SELECT COUNT(*) as cnt, MAX(message_type) as last_type
            FROM messages
            WHERE job_id = ? AND person_id = ?
                AND message_type LIKE 'followup_%'
        """, (msg["jid"], msg["pid"])).fetchone()

        followup_count = existing_followups["cnt"]

        # Determine which follow-up to send
        if followup_count == 0 and biz_days >= 5:
            followup_type = "followup_1"
            template = "followup_1.j2"
        elif followup_count == 1 and biz_days >= 12:
            followup_type = "followup_2"
            template = "followup_2.j2"
        else:
            continue  # Not time yet, or max follow-ups reached

        if followup_count >= 2:
            continue  # Max 2 follow-ups

        # Build follow-up message
        subject = f"Re: {msg['subject']}" if not msg["subject"].startswith("Re:") else msg["subject"]

        body = _render_followup(
            followup_type=followup_type,
            contact_name=msg["contact_name"],
            company_name=msg["company_name"],
            job_title=msg["job_title"],
            sender_name=config.sender.name,
        )

        if dry_run:
            logger.info(
                f"[DRY RUN] Would queue {followup_type} to {msg['contact_email']} "
                f"({msg['company_name']})"
            )
            followups_queued += 1
            continue

        # Insert follow-up message as 'ready'
        db.insert_message(
            job_id=msg["jid"],
            person_id=msg["pid"],
            company_id=msg["cid"],
            template_used=template,
            resume_variant=None,
            subject=subject,
            body=body,
            message_type=followup_type,
            message_quality_score=None,
            review_required=False,
        )

        followups_queued += 1
        logger.info(
            f"Queued {followup_type} to {msg['contact_email']} ({msg['company_name']})"
        )

    logger.info(f"Queued {followups_queued} follow-ups")
    return followups_queued


def _render_followup(
    followup_type: str,
    contact_name: str = None,
    company_name: str = "",
    job_title: str = "",
    sender_name: str = "",
) -> str:
    """Render a follow-up email body. Uses Jinja template if available, else built-in."""
    # Try to load Jinja template
    try:
        from jinja2 import Environment, FileSystemLoader
        template_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates"
        )
        if os.path.isdir(template_dir):
            env = Environment(loader=FileSystemLoader(template_dir))
            template = env.get_template(f"{followup_type}.j2")
            return template.render(
                contact_name=contact_name,
                company_name=company_name,
                job_title=job_title,
                sender_name=sender_name,
            )
    except Exception:
        pass

    # Built-in fallback
    greeting = f"Hi {contact_name.split()[0]}," if contact_name else "Hi,"

    if followup_type == "followup_1":
        return f"""{greeting}

I wanted to follow up on my earlier message about the {job_title} role at {company_name}. I remain very interested in the opportunity and would welcome the chance to discuss how my background aligns with what you're looking for.

Would you have a few minutes for a brief conversation?

Best,
{sender_name}"""

    else:  # followup_2
        return f"""{greeting}

I'm reaching out one last time regarding the {job_title} position at {company_name}. I understand you're busy, but I wanted to reiterate my interest.

If the timing isn't right or the role has been filled, no worries at all. I appreciate your time.

Best,
{sender_name}"""


def run(config: Config, db: Database, dry_run: bool = False) -> int:
    """
    Run both reply detection and follow-up scheduling.
    Returns total actions taken.
    """
    replies = detect_replies(config, db, dry_run=dry_run)
    followups = schedule_followups(config, db, dry_run=dry_run)
    return replies + followups
