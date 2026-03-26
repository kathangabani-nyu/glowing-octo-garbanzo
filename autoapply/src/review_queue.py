"""
Review queue backend logic for AutoApply V2.2.

Handles inserting items, fetching pending items with full context,
updating review decisions, and computing approval rate metrics.

Module owner: Claude Code
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from src.db import Database
from src.utils import get_logger

logger = get_logger("review_queue")


@dataclass
class ReviewItem:
    id: int
    job_id: int
    person_id: Optional[int]
    message_id: Optional[int]
    queue_reason: str
    confidence_tier: Optional[str]
    review_status: str
    job_title: str
    job_url: Optional[str]
    company_name: str
    company_domain: str
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_confidence: Optional[str]
    email_subject: Optional[str]
    email_body: Optional[str]
    resume_variant: Optional[str]


def insert_for_review(
    db: Database, *,
    job_id: int,
    person_id: int = None,
    message_id: int = None,
    queue_reason: str,
    confidence_tier: str = None,
) -> int:
    """Insert an item into the review queue. Returns the review item ID."""
    review_id = db.insert_review_item(
        job_id=job_id,
        person_id=person_id,
        message_id=message_id,
        queue_reason=queue_reason,
        confidence_tier=confidence_tier,
    )
    logger.info(f"Queued review #{review_id}: reason={queue_reason}, tier={confidence_tier}")
    return review_id


def get_pending_items(db: Database) -> List[ReviewItem]:
    """Fetch all pending review items with full context."""
    rows = db.get_pending_reviews()
    items = []
    for row in rows:
        items.append(ReviewItem(
            id=row["id"],
            job_id=row["job_id"],
            person_id=row["person_id"],
            message_id=row["message_id"],
            queue_reason=row["queue_reason"],
            confidence_tier=row["confidence_tier"],
            review_status=row["review_status"],
            job_title=row["job_title"],
            job_url=row["job_url"],
            company_name=row["company_name"],
            company_domain=row["company_domain"],
            contact_name=row["contact_name"],
            contact_email=row["contact_email"],
            contact_confidence=row["contact_confidence"],
            email_subject=row["email_subject"],
            email_body=row["email_body"],
            resume_variant=row["resume_variant"],
        ))
    return items


def approve_item(db: Database, review_id: int, notes: str = None):
    """Approve a review item — move its message to 'ready' status."""
    db.update_review_status(review_id, "approved", notes=notes)

    # Get the review item to find the message_id
    rows = db.conn.execute(
        "SELECT message_id FROM review_queue WHERE id = ?", (review_id,)
    ).fetchone()
    if rows and rows["message_id"]:
        db.update_message_status(rows["message_id"], "ready")

    logger.info(f"Review #{review_id} approved")


def skip_item(db: Database, review_id: int, notes: str = None):
    """Skip a review item — mark its message as skipped."""
    db.update_review_status(review_id, "skipped", notes=notes)

    rows = db.conn.execute(
        "SELECT message_id FROM review_queue WHERE id = ?", (review_id,)
    ).fetchone()
    if rows and rows["message_id"]:
        db.update_message_status(rows["message_id"], "skipped")

    logger.info(f"Review #{review_id} skipped")


def suppress_item(db: Database, review_id: int, suppress_type: str = "email",
                  notes: str = None):
    """
    Suppress a review item and add to suppression list.

    Args:
        suppress_type: "email" to suppress the specific address,
                       "company" to suppress the entire company domain
    """
    db.update_review_status(review_id, "suppressed", notes=notes)

    row = db.conn.execute("""
        SELECT rq.message_id, p.email, c.domain, c.name as company_name
        FROM review_queue rq
        LEFT JOIN people p ON rq.person_id = p.id
        LEFT JOIN jobs j ON rq.job_id = j.id
        LEFT JOIN companies c ON j.company_id = c.id
        WHERE rq.id = ?
    """, (review_id,)).fetchone()

    if row:
        if row["message_id"]:
            db.update_message_status(row["message_id"], "suppressed")

        if suppress_type == "email" and row["email"]:
            db.add_suppression("email", row["email"], reason=notes)
            logger.info(f"Suppressed email: {row['email']}")
        elif suppress_type == "company" and row["domain"]:
            db.add_suppression("domain", row["domain"], reason=notes)
            logger.info(f"Suppressed company domain: {row['domain']}")


def get_approval_rate(db: Database, last_n: int = 20) -> Optional[float]:
    """Get the review approval rate over the last N reviewed items."""
    return db.get_review_approval_rate(last_n)


def get_queue_stats(db: Database) -> Dict[str, int]:
    """Get summary statistics for the review queue."""
    rows = db.conn.execute("""
        SELECT review_status, COUNT(*) as cnt
        FROM review_queue
        GROUP BY review_status
    """).fetchall()
    stats = {row["review_status"]: row["cnt"] for row in rows}
    return stats
