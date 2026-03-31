"""One-time migration script for the AI-assisted AutoApply toolkit."""

from __future__ import annotations

import argparse
from pathlib import Path

from toolkit_db import ToolkitDB


def _default_db_path() -> str:
    return str(Path(__file__).resolve().parent / "autoapply.db")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create toolkit tables and migrate legacy outreach history."
    )
    parser.add_argument(
        "--db-path",
        default=_default_db_path(),
        help="Target SQLite database path. Defaults to autoapply.db next to this script.",
    )
    parser.add_argument(
        "--source-db-path",
        default=None,
        help="Optional separate legacy database to copy from. Defaults to the target DB.",
    )
    args = parser.parse_args()

    db = ToolkitDB(args.db_path)
    try:
        summary = db.migrate_legacy_data(args.source_db_path)
    finally:
        db.close()

    print(f"Toolkit DB ready: {Path(args.db_path).resolve()}")
    print(f"Outreach rows inserted: {summary['outreach_rows_inserted']}")
    print(f"Suppression rows copied: {summary['suppression_rows_copied']}")
    print(f"Domain patterns copied: {summary['pattern_rows_copied']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
