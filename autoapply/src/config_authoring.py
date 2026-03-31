"""
Config authoring preflight checks for AutoApply V2.2.

This module helps catch common setup issues before running the daily pipeline.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import List

from .config import Config, Watchlist, load_config, load_watchlist


PLACEHOLDER_EMAILS = {
    "your.email@gmail.com",
    "example@example.com",
    "me@example.com",
}


@dataclass
class PreflightReport:
    errors: List[str]
    warnings: List[str]
    notes: List[str]


def _looks_like_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))


def validate_config_authoring(config: Config, watchlist: Watchlist) -> PreflightReport:
    errors: List[str] = []
    warnings: List[str] = []
    notes: List[str] = []

    sender_name = (config.sender.name or "").strip()
    sender_email = (config.sender.email or "").strip().lower()
    if not sender_name:
        errors.append("sender.name is empty.")
    if not _looks_like_email(sender_email):
        errors.append("sender.email is not a valid email format.")
    elif sender_email in PLACEHOLDER_EMAILS:
        errors.append("sender.email is still a placeholder value.")

    if not watchlist.companies:
        errors.append("watchlist has zero companies.")
        return PreflightReport(errors=errors, warnings=warnings, notes=notes)

    for company in watchlist.companies:
        name = (company.name or "").strip()
        domain = (company.domain or "").strip()
        ats = (company.ats or "").strip().lower()
        slug = (company.slug or "").strip()

        if not name:
            errors.append("a watchlist company is missing 'name'.")
        if not domain or "." not in domain:
            errors.append(f"{name or '<unknown>'}: invalid or missing domain.")
        if company.priority < 1 or company.priority > 5:
            warnings.append(f"{name}: priority should usually be 1-5.")

        if ats in {"greenhouse", "lever", "ashby", "smartrecruiters"} and not slug:
            warnings.append(f"{name}: ATS '{ats}' usually needs a slug.")
        if ats == "workday":
            if not slug:
                warnings.append(f"{name}: ATS 'workday' needs a slug.")
            if not company.workday_instance:
                warnings.append(f"{name}: ATS 'workday' needs workday_instance.")
            if not company.workday_board:
                warnings.append(f"{name}: ATS 'workday' needs workday_board.")
        if not company.jobs_url and not company.careers_url and not ats:
            warnings.append(
                f"{name}: add at least one source (ats, careers_url, or jobs_url)."
            )

    if len(watchlist.companies) < 5:
        notes.append("watchlist is small (<5 companies); discovery volume may be limited.")

    return PreflightReport(errors=errors, warnings=warnings, notes=notes)


def run(config_path: str = "config.local.yaml", watchlist_path: str = "watchlist.local.yaml") -> int:
    config = load_config(config_path)
    watchlist = load_watchlist(watchlist_path)
    report = validate_config_authoring(config, watchlist)

    print("Config preflight:")
    print(f"  companies: {len(watchlist.companies)}")
    print(f"  errors: {len(report.errors)}")
    print(f"  warnings: {len(report.warnings)}")
    print()

    for err in report.errors:
        print(f"[ERROR] {err}")
    for warning in report.warnings:
        print(f"[WARN]  {warning}")
    for note in report.notes:
        print(f"[NOTE]  {note}")

    if report.errors:
        print("\nFix the errors above before running the full pipeline.")
        return 1

    print("\nPreflight checks passed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate config and watchlist authoring quality."
    )
    parser.add_argument("--config", default="config.local.yaml", help="Path to config file")
    parser.add_argument("--watchlist", default="watchlist.local.yaml", help="Path to watchlist file")
    args = parser.parse_args()
    raise SystemExit(run(config_path=args.config, watchlist_path=args.watchlist))


if __name__ == "__main__":
    main()
