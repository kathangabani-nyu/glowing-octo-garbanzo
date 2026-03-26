#!/usr/bin/env python3
"""
Run the weekly company discovery pipeline.
"""

import argparse
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from src.company_discoverer import run as run_company_discoverer
from src.config import load_config
from src.db import Database


def main():
    parser = argparse.ArgumentParser(description="AutoApply V2.2 - Company Discovery")
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated sources: yc,builtin or all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and ATS-check companies without writing to the database",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Promote already-detected staged companies into the active companies table",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--cities",
        default="nyc,sf,chicago,boston,la",
        help="Comma-separated BuiltIn city keys",
    )
    args = parser.parse_args()

    config_path = os.path.join(PROJECT_ROOT, args.config) if not os.path.isabs(args.config) else args.config
    config = load_config(config_path)

    db_path = os.path.join(PROJECT_ROOT, config.database.path)
    db = Database(db_path)
    db.connect()
    db.initialize()

    try:
        summary = run_company_discoverer(
            config,
            db,
            sources=[part.strip() for part in args.sources.split(",") if part.strip()],
            dry_run=args.dry_run,
            cities=[part.strip() for part in args.cities.split(",") if part.strip()],
            promote_only=args.promote,
        )
    finally:
        db.close()

    if args.dry_run:
        companies = summary.dry_run_companies or []
        print(f"[dry-run] candidates: {len(companies)}")
        for company in companies:
            ats = company["ats"] or "unknown"
            print(f"  - {company['name']} ({company['domain']}) [{company['source']}] ats={ats}")
        return

    print("Company discovery summary:")
    print(f"  scraped: {summary.scraped}")
    print(f"  inserted: {summary.inserted}")
    print(f"  ats detected: {summary.detected}")
    print(f"  promoted: {summary.promoted}")
    if summary.skipped_fresh:
        print(f"  skipped for freshness: {summary.skipped_fresh}")


if __name__ == "__main__":
    main()
