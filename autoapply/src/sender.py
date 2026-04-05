"""
Gmail API sending engine for AutoApply V2.2.

Authenticated sending with full suppression logic:
- Daily limits + warm-up schedule
- Person cooldown (90d), company+job_family cooldown (30d), exact posting (permanent)
- Bounce-based suppression, no-go list
- Random delays (45-90s), business-hour enforcement
- Safety stops: bounce rate > 5%, review skip rate > 40%

Module owner: Claude Code
"""

import base64
import html
import os
import random
import re
import time
from datetime import datetime
from email.utils import formataddr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional

from src.db import Database
from src.config import Config
from src.utils import get_logger

logger = get_logger("sender")
URL_RE = re.compile(r"(https?://[^\s<]+|www\.[^\s<]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s<]*)")

# Gmail API scopes
SCOPES = ["https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"]


def _get_gmail_service(credentials_path: str = "credentials.json",
                       token_path: str = "token.json"):
    """
    Authenticate with Gmail API and return the service object.
    Handles token refresh automatically.
    """
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Gmail credentials not found at {credentials_path}. "
                    "Set up OAuth2 credentials in Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _is_business_hours(config: Config) -> bool:
    """Check if current time is within configured business hours."""
    now = datetime.now()
    # Skip weekends
    if now.weekday() >= 5:
        return False
    return config.sending.business_hours_start <= now.hour < config.sending.business_hours_end


def _get_warm_up_limit(config: Config, db: Database) -> int:
    """
    Calculate today's send limit based on warm-up schedule.
    Ramps from warm_up_initial_limit to max_initial_per_day over warm_up_days.
    """
    # Count distinct days we've sent emails
    row = db.conn.execute("""
        SELECT COUNT(DISTINCT date(sent_at)) as send_days
        FROM messages WHERE status = 'sent'
    """).fetchone()
    send_days = row["send_days"] if row else 0

    if send_days >= config.sending.warm_up_days:
        return config.sending.max_initial_per_day

    # Linear ramp
    ramp_range = config.sending.max_initial_per_day - config.sending.warm_up_initial_limit
    daily_increase = ramp_range / max(1, config.sending.warm_up_days)
    limit = config.sending.warm_up_initial_limit + int(send_days * daily_increase)
    return min(limit, config.sending.max_initial_per_day)


def _check_safety_stops(config: Config, db: Database) -> Optional[str]:
    """
    Check safety stop conditions. Returns reason string if sending should stop,
    or None if safe to continue.
    """
    # Bounce rate check
    bounce_rate = db.get_recent_bounce_rate(config.safety.bounce_window)
    if bounce_rate > config.safety.max_bounce_rate:
        return f"Bounce rate {bounce_rate:.1%} exceeds {config.safety.max_bounce_rate:.1%} threshold"

    # Review skip rate check
    from src.review_queue import get_approval_rate
    approval_rate = get_approval_rate(db, config.safety.review_skip_window)
    if approval_rate is not None:
        skip_rate = 1.0 - approval_rate
        if skip_rate > config.safety.max_review_skip_rate:
            return f"Review skip rate {skip_rate:.1%} exceeds {config.safety.max_review_skip_rate:.1%} threshold"

    return None


def _linkify_line(line: str) -> str:
    """Escape line content and convert URLs to clickable anchors."""
    parts = []
    last = 0
    for match in URL_RE.finditer(line or ""):
        start, end = match.span()
        if start > last:
            parts.append(html.escape(line[last:start]))
        url = match.group(0)
        href = url if url.startswith(("http://", "https://")) else f"https://{url}"
        safe_href = html.escape(href, quote=True)
        safe_text = html.escape(url)
        parts.append(f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">{safe_text}</a>')
        last = end
    if last < len(line or ""):
        parts.append(html.escape(line[last:]))
    return "".join(parts)


def _render_html_body(body: str) -> str:
    """Render a simple HTML version of the plain-text email body."""
    paragraphs = []
    for block in (body or "").strip().split("\n\n"):
        lines = [_linkify_line(line.strip()) for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        paragraphs.append(f"<p>{'<br>'.join(lines)}</p>")

    joined = "\n".join(paragraphs) if paragraphs else "<p></p>"
    return (
        "<html>"
        "<body style=\"font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #222;\">"
        f"{joined}"
        "</body>"
        "</html>"
    )


def _build_mime_message(
    sender_email: str,
    sender_name: str,
    to_email: str,
    subject: str,
    body: str,
    resume_path: str = None,
    in_reply_to: str = None,
    references: str = None,
) -> str:
    """Build a MIME message and return base64url-encoded string."""
    html_body = _render_html_body(body)

    if resume_path and os.path.exists(resume_path):
        msg = MIMEMultipart("mixed")
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(body, "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alternative)

        with open(resume_path, "rb") as f:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            filename = os.path.basename(resume_path)
            attachment.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(attachment)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    msg["to"] = to_email
    msg["from"] = formataddr((sender_name, sender_email)) if sender_name else sender_email
    msg["subject"] = subject

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw


def _send_via_gmail(service, raw_message: str, thread_id: str = None) -> dict:
    """Send a message via Gmail API. Returns the sent message metadata."""
    body = {"raw": raw_message}
    if thread_id:
        body["threadId"] = thread_id

    result = service.users().messages().send(userId="me", body=body).execute()
    return result


def run(config: Config, db: Database, dry_run: bool = False,
        send_limit: int = None) -> int:
    """
    Send all ready messages via Gmail API.

    Args:
        config: Application config
        db: Database connection
        dry_run: If True, skip actual sends
        send_limit: Override max sends for this run

    Returns:
        Count of messages sent
    """
    # Check business hours
    if not dry_run and not _is_business_hours(config):
        logger.info("Outside business hours, skipping sends")
        return 0

    # Check safety stops
    safety_reason = _check_safety_stops(config, db)
    if safety_reason:
        logger.warning(f"SAFETY STOP: {safety_reason}")
        return 0

    # Determine send limit
    warm_up_limit = _get_warm_up_limit(config, db)
    already_sent = db.get_today_send_count()
    max_today = send_limit if send_limit is not None else warm_up_limit
    remaining = max(0, max_today - already_sent)

    if remaining == 0:
        logger.info(f"Daily limit reached ({already_sent}/{max_today} sent today)")
        return 0

    logger.info(f"Send budget: {remaining} remaining ({already_sent}/{max_today} sent today)")

    # Get ready messages
    messages = db.get_ready_messages()
    if not messages:
        logger.info("No messages ready to send")
        return 0

    # Initialize Gmail service (skip in dry run)
    service = None
    if not dry_run:
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            service = _get_gmail_service(
                credentials_path=os.path.join(project_root, "credentials.json"),
                token_path=os.path.join(project_root, "token.json"),
            )
        except Exception as e:
            logger.error(f"Failed to authenticate with Gmail: {e}")
            return 0

    sent_count = 0

    for msg in messages:
        if sent_count >= remaining:
            logger.info(f"Reached send limit ({sent_count}/{remaining})")
            break

        # Build the resume path
        resume_path = None
        if msg["resume_variant"]:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidate_path = os.path.join(project_root, msg["resume_variant"])
            if os.path.exists(candidate_path):
                resume_path = candidate_path

        # Build MIME message
        raw = _build_mime_message(
            sender_email=config.sender.email,
            sender_name=config.sender.name,
            to_email=msg["contact_email"],
            subject=msg["subject"],
            body=msg["body"],
            resume_path=resume_path,
        )

        if dry_run:
            logger.info(
                f"[DRY RUN] Would send to {msg['contact_email']}: {msg['subject']}"
            )
            sent_count += 1
            continue

        # Send
        try:
            result = _send_via_gmail(service, raw)
            gmail_id = result.get("id", "")
            thread_id = result.get("threadId", "")

            db.update_message_status(
                msg["id"], "sent",
                gmail_message_id=gmail_id,
                gmail_thread_id=thread_id,
            )

            logger.info(
                f"Sent to {msg['contact_email']}: {msg['subject']} "
                f"(gmail_id={gmail_id})"
            )
            sent_count += 1

            # Update daily metrics
            db.increment_metric("emails_sent")

            # Random delay between sends
            if sent_count < remaining and sent_count < len(messages):
                delay = random.uniform(
                    config.sending.min_delay_seconds,
                    config.sending.max_delay_seconds,
                )
                logger.debug(f"Waiting {delay:.0f}s before next send")
                time.sleep(delay)

        except Exception as e:
            logger.error(f"Failed to send to {msg['contact_email']}: {e}")
            db.update_message_status(msg["id"], "failed")

    logger.info(f"Sent {sent_count} messages")
    return sent_count
