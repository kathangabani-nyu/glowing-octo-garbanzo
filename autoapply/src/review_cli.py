"""
Review queue CLI for AutoApply V2.2.

Supports both:
- non-interactive command mode (list/stats/decide)
- interactive review loop for pending items
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from src.db import Database
from src.review_queue import (
    approve_item,
    get_pending_items,
    get_queue_stats,
    skip_item,
    suppress_item,
)


VALID_ACTIONS = {"approve", "skip", "suppress"}
VALID_SUPPRESS_TYPES = {"email", "company"}


def _resolve_db_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, path)


def print_queue_stats(db: Database) -> None:
    stats = get_queue_stats(db)
    pending = stats.get("pending", 0)
    approved = stats.get("approved", 0)
    skipped = stats.get("skipped", 0)
    suppressed = stats.get("suppressed", 0)
    print(
        f"Review queue stats: pending={pending}, approved={approved}, "
        f"skipped={skipped}, suppressed={suppressed}"
    )


def print_pending_items(db: Database, limit: Optional[int] = None) -> int:
    items = get_pending_items(db)
    if limit is not None:
        items = items[:limit]

    if not items:
        print("No pending review items.")
        return 0

    for item in items:
        print("=" * 72)
        print(f"Review ID: {item.id} | Reason: {item.queue_reason} | Tier: {item.confidence_tier}")
        print(f"Company: {item.company_name} ({item.company_domain})")
        print(f"Role: {item.job_title}")
        print(f"Posting: {item.job_url or 'n/a'}")
        print(f"Contact: {item.contact_name or 'n/a'} <{item.contact_email or 'n/a'}>")
        print(f"Subject: {item.email_subject or '(none)'}")
        print(f"Resume: {item.resume_variant or '(none)'}")
        if item.email_body:
            preview = item.email_body.strip()
            if len(preview) > 700:
                preview = preview[:700] + "... [truncated]"
            print("\n--- Email Preview ---")
            print(preview)
            print("--- End Preview ---")
    print("=" * 72)
    print(f"Total shown: {len(items)}")
    return len(items)


def apply_decision(
    db: Database,
    review_id: int,
    action: str,
    notes: str = "",
    suppress_type: str = "email",
) -> None:
    action = action.strip().lower()
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action '{action}'. Use one of: {sorted(VALID_ACTIONS)}")

    if action == "approve":
        approve_item(db, review_id, notes=notes or None)
        print(f"Approved review item #{review_id}")
        return

    if action == "skip":
        skip_item(db, review_id, notes=notes or None)
        print(f"Skipped review item #{review_id}")
        return

    suppress_type = suppress_type.strip().lower()
    if suppress_type not in VALID_SUPPRESS_TYPES:
        raise ValueError(
            f"Invalid suppress type '{suppress_type}'. Use one of: {sorted(VALID_SUPPRESS_TYPES)}"
        )
    suppress_item(db, review_id, suppress_type=suppress_type, notes=notes or None)
    print(f"Suppressed review item #{review_id} ({suppress_type})")


def interactive_review_loop(db: Database) -> int:
    processed = 0
    while True:
        items = get_pending_items(db)
        if not items:
            print("No pending review items.")
            return processed

        item = items[0]
        print()
        print("=" * 72)
        print(f"[{processed + 1}] Review ID {item.id} | {item.company_name} | {item.job_title}")
        print(f"Reason: {item.queue_reason} | Tier: {item.confidence_tier}")
        print(f"Contact: {item.contact_name or 'n/a'} <{item.contact_email or 'n/a'}>")
        print(f"Subject: {item.email_subject or '(none)'}")
        print("-" * 72)
        print((item.email_body or "(no body)").strip())
        print("-" * 72)
        print("Actions: [a]pprove  [s]kip  suppress [e]mail  suppress [c]ompany  [q]uit")
        choice = input("Select action: ").strip().lower()

        if choice == "q":
            return processed
        if choice not in {"a", "s", "e", "c"}:
            print("Invalid choice, try again.")
            continue

        notes = input("Optional notes (enter to skip): ").strip()
        if choice == "a":
            apply_decision(db, item.id, "approve", notes=notes)
        elif choice == "s":
            apply_decision(db, item.id, "skip", notes=notes)
        elif choice == "e":
            apply_decision(db, item.id, "suppress", notes=notes, suppress_type="email")
        else:
            apply_decision(db, item.id, "suppress", notes=notes, suppress_type="company")
        processed += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review queue CLI for AutoApply")
    parser.add_argument("--db", default="autoapply.db", help="Path to SQLite database file")

    sub = parser.add_subparsers(dest="command")

    list_cmd = sub.add_parser("list", help="List pending review items")
    list_cmd.add_argument("--limit", type=int, default=None, help="Max pending items to show")

    sub.add_parser("stats", help="Show review queue statistics")

    decide = sub.add_parser("decide", help="Apply a decision to a review item")
    decide.add_argument("--id", type=int, required=True, help="Review item ID")
    decide.add_argument("--action", required=True, choices=sorted(VALID_ACTIONS))
    decide.add_argument("--notes", default="", help="Optional review notes")
    decide.add_argument(
        "--suppress-type",
        default="email",
        choices=sorted(VALID_SUPPRESS_TYPES),
        help="Only used for action=suppress",
    )

    return parser


def run(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    db = Database(db_path)
    db.connect()
    db.initialize()

    try:
        if args.command == "list":
            print_pending_items(db, limit=args.limit)
            return 0
        if args.command == "stats":
            print_queue_stats(db)
            return 0
        if args.command == "decide":
            apply_decision(
                db=db,
                review_id=args.id,
                action=args.action,
                notes=args.notes,
                suppress_type=args.suppress_type,
            )
            return 0

        # Default: interactive mode
        print_queue_stats(db)
        processed = interactive_review_loop(db)
        print(f"Processed {processed} review items this session.")
        return 0
    finally:
        db.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
