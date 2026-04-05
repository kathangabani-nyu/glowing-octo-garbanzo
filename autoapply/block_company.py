#!/usr/bin/env python3
"""
Add company-level suppression entries to prevent future outreach.
"""

from __future__ import annotations

import argparse
import os

from src.db import Database


def _resolve_db_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    project_root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(project_root, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Block a company from future AutoApply outreach."
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Company domain to suppress, for example withlantern.com",
    )
    parser.add_argument(
        "--company",
        default="",
        help="Optional company name to suppress as a second guard",
    )
    parser.add_argument(
        "--reason",
        default="manual company block",
        help="Reason stored in suppression_list",
    )
    parser.add_argument(
        "--db",
        default="autoapply.db",
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only show whether the company is already suppressed",
    )
    return parser


def _print_status(db: Database, domain: str, company: str) -> None:
    domain_blocked = db.check_suppression(domain=domain)
    company_blocked = db.check_suppression(company_name=company) if company else False
    print(f"domain={domain} blocked={domain_blocked}")
    if company:
        print(f"company={company} blocked={company_blocked}")


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    domain = args.domain.strip().lower()
    company = args.company.strip()
    reason = args.reason.strip() or "manual company block"

    db = Database(_resolve_db_path(args.db))
    db.connect()
    db.initialize()

    try:
        if args.check:
            _print_status(db, domain, company)
            return 0

        db.add_suppression("domain", domain, reason=reason)
        print(f"Blocked domain: {domain}")

        if company:
            db.add_suppression("company", company, reason=reason)
            print(f"Blocked company: {company}")

        _print_status(db, domain, company)
        return 0
    finally:
        db.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
