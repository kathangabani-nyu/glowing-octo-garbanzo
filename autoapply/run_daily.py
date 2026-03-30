#!/usr/bin/env python3
"""
AutoApply V2.2 — Daily Orchestrator

Runs the outreach pipeline stages in sequence.
Each stage is isolated with try/except so a failure in one stage
does not block subsequent stages.

Usage:
    python run_daily.py
    python run_daily.py --dry-run
    python run_daily.py --stage discovery
    python run_daily.py --stage filtering --dry-run
    python run_daily.py --send-limit 3
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure the project root is on the path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from src.config import WatchlistCompany, load_config, load_watchlist
from src.config_authoring import validate_config_authoring
from src.db import Database


STAGES = [
    "discovery",
    "filtering",
    "contacts",
    "assembly",
    "review",
    "sending",
    "followups",
    "reporting",
]


def run_discovery(config, watchlist, db, dry_run):
    """Stage 1: Discover jobs from watchlist companies."""
    # Stub — will be implemented by Codex (job_discoverer.py)
    from src.job_discoverer import run as run_job_discovery
    discovered = run_job_discovery(watchlist, db, config, dry_run=dry_run)
    print(f"  [discovery] Discovered {discovered} new jobs.")
    return discovered


def run_filtering(config, watchlist, db, dry_run):
    """Stage 2: Score and filter discovered jobs."""
    # Stub — will be implemented by Codex (job_filter.py)
    from src.job_filter import run as run_job_filter
    processed = run_job_filter(config, db, dry_run=dry_run)
    print(f"  [filtering] Scored {processed} jobs.")
    return processed


def run_contacts(config, watchlist, db, dry_run):
    """Stage 3: Resolve contacts for qualified jobs."""
    from src.contact_discoverer import run as run_contact_discovery
    resolved = run_contact_discovery(config, db, dry_run=dry_run)
    print(f"  [contacts] Resolved {resolved} contacts.")
    return resolved


def run_assembly(config, watchlist, db, dry_run):
    """Stage 4: Assemble outreach emails."""
    from src.email_assembler import run as run_email_assembly
    count = run_email_assembly(config, db, dry_run=dry_run)
    print(f"  [assembly] Assembled {count} messages.")
    return count


def run_review(config, watchlist, db, dry_run):
    """Stage 5: Show review queue status."""
    from src.review_queue import get_pending_items, get_queue_stats
    stats = get_queue_stats(db)
    pending = stats.get("pending", 0)
    approved = stats.get("approved", 0)
    skipped = stats.get("skipped", 0)
    print(f"  [review] Queue: {pending} pending, {approved} approved, {skipped} skipped.")
    if pending > 0:
        print(f"  [review] Run 'python -m src.review_cli' to review pending items.")
    return pending


def run_sending(config, watchlist, db, dry_run, send_limit=None):
    """Stage 6: Send approved messages via Gmail API."""
    if dry_run:
        # Write drafts to a fresh folder so each run is not mixed with old exports
        output_dir = Path(PROJECT_ROOT) / "dry_run_output" / "last_run"
        output_dir.mkdir(parents=True, exist_ok=True)
        for old in output_dir.glob("*.txt"):
            old.unlink(missing_ok=True)
        messages = db.get_dry_run_export_messages()
        for msg in messages:
            filename = f"{msg['id']}_{msg['company_name']}_{msg['job_title']}.txt"
            filename = "".join(c if c.isalnum() or c in "._- " else "_" for c in filename)
            filepath = str(output_dir / filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"To: {msg['contact_email']}\n")
                f.write(f"Subject: {msg['subject']}\n")
                f.write(f"Status: {msg['status']}\n")
                f.write(f"Confidence: {msg['confidence_tier']}\n")
                f.write(f"Resume: {msg['resume_variant']}\n")
                f.write(f"\n{msg['body']}\n")
            print(f"  [sending][dry-run] Wrote: {filepath}")
        print(f"  [sending][dry-run] {len(messages)} emails written to {output_dir}{os.sep}")
        return len(messages)
    from src.sender import run as run_sender
    count = run_sender(config, db, dry_run=False, send_limit=send_limit)
    print(f"  [sending] Sent {count} messages.")
    return count


def run_followups(config, watchlist, db, dry_run):
    """Stage 7: Detect replies and schedule follow-ups."""
    from src.followup_manager import run as run_followup
    count = run_followup(config, db, dry_run=dry_run)
    print(f"  [followups] {count} actions (replies detected + follow-ups queued).")
    return count


def run_reporting(config, watchlist, db, dry_run):
    """Stage 8: Print daily metrics summary."""
    from src.reporter import run as run_reporter, write_report_files
    snapshot = run_reporter(db, emit=True)
    output_dir = os.path.join(PROJECT_ROOT, "logs", "reports")
    paths = write_report_files(snapshot, output_dir)
    print(f"  [reporting] Artifacts: {paths['txt']}, {paths['md']}, {paths['json']}")
    print("  [reporting] Report generated.")
    return snapshot


STAGE_RUNNERS = {
    "discovery": run_discovery,
    "filtering": run_filtering,
    "contacts": run_contacts,
    "assembly": run_assembly,
    "review": run_review,
    "sending": run_sending,
    "followups": run_followups,
    "reporting": run_reporting,
}


def merge_promoted_companies(watchlist, db):
    """Merge DB-backed auto-discovered companies into the working watchlist."""
    promoted = db.get_promoted_companies()
    merged = 0
    existing_domains = {company.domain for company in watchlist.companies}

    for row in promoted:
        if row["domain"] in existing_domains:
            continue
        watchlist.companies.append(WatchlistCompany(
            name=row["name"],
            domain=row["domain"],
            priority=row["priority"],
            ats=row["ats"],
            slug=row["slug"],
            workday_instance=row["workday_instance"] or "",
            workday_board=row["workday_board"] or "",
            careers_url=row["careers_url"],
            jobs_url=row["jobs_url"],
            job_family_focus=row["job_family_focus"],
            notes=row["notes"],
        ))
        existing_domains.add(row["domain"])
        merged += 1

    return merged


def main():
    parser = argparse.ArgumentParser(
        description="AutoApply V2.2 — Daily Outreach Pipeline"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run full pipeline but skip actual Gmail sends; write emails to dry_run_output/"
    )
    parser.add_argument(
        "--stage", choices=STAGES,
        help="Run only a specific stage"
    )
    parser.add_argument(
        "--send-limit", type=int, default=None,
        help="Override max sends for this run (useful for testing)"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config.yaml (default: config.yaml)"
    )
    parser.add_argument(
        "--watchlist", default="watchlist.yaml",
        help="Path to watchlist.yaml (default: watchlist.yaml)"
    )

    args = parser.parse_args()

    # Resolve paths relative to project root
    config_path = os.path.join(PROJECT_ROOT, args.config) if not os.path.isabs(args.config) else args.config
    watchlist_path = os.path.join(PROJECT_ROOT, args.watchlist) if not os.path.isabs(args.watchlist) else args.watchlist

    print(f"AutoApply V2.2 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print("Mode: DRY RUN (no emails will be sent)")
    print()

    # Load config
    config = load_config(config_path)
    watchlist = load_watchlist(watchlist_path)
    print(f"Loaded config: {config.sender.name} <{config.sender.email}>")
    print(f"Watchlist: {len(watchlist.companies)} companies")
    preflight = validate_config_authoring(config, watchlist)
    if preflight.errors:
        print("\nPreflight checks failed:")
        for err in preflight.errors:
            print(f"  [error] {err}")
        print("\nFix config/watchlist authoring errors and retry.")
        raise SystemExit(1)
    if preflight.warnings:
        print("\nPreflight warnings:")
        for warning in preflight.warnings:
            print(f"  [warn] {warning}")
    print()

    # Open database
    db_path = os.path.join(PROJECT_ROOT, config.database.path)
    db = Database(db_path)
    db.connect()
    db.initialize()

    # Sync watchlist companies into DB
    for company in watchlist.companies:
        db.upsert_company(
            name=company.name,
            domain=company.domain,
            priority=company.priority,
            ats=company.ats,
            slug=company.slug,
            careers_url=company.careers_url,
            jobs_url=company.jobs_url,
            workday_instance=company.workday_instance,
            workday_board=company.workday_board,
            source="watchlist",
            job_family_focus=company.job_family_focus,
            notes=company.notes,
        )

    merged = merge_promoted_companies(watchlist, db)
    if merged:
        print(f"  + {merged} auto-discovered companies merged into pipeline")

    # Determine which stages to run
    stages_to_run = [args.stage] if args.stage else STAGES

    # Run stages
    for stage_name in stages_to_run:
        runner = STAGE_RUNNERS[stage_name]
        print(f"-- Stage: {stage_name} --")
        start = time.time()
        try:
            if stage_name == "sending":
                runner(config, watchlist, db, args.dry_run, send_limit=args.send_limit)
            else:
                runner(config, watchlist, db, args.dry_run)
        except Exception as e:
            print(f"  ERROR in {stage_name}: {e}")
            import traceback
            traceback.print_exc()
        elapsed = time.time() - start
        print(f"  ({elapsed:.1f}s)")
        print()

    db.close()
    print("Done.")


if __name__ == "__main__":
    main()
