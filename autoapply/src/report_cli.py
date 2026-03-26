"""
Reporting CLI for AutoApply V2.2.

Generates a report snapshot and can export text, markdown, and JSON files.
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from src.db import Database
from src.reporter import build_snapshot, render_report, write_report_files


def _resolve_db_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, path)


def _resolve_output_dir(path: str) -> str:
    if os.path.isabs(path):
        return path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, path)


def run(
    db_path: str = "autoapply.db",
    metric_date: Optional[str] = None,
    output_dir: str = "logs/reports",
    write_files: bool = True,
) -> int:
    db = Database(_resolve_db_path(db_path))
    db.connect()
    db.initialize()
    try:
        snapshot = build_snapshot(db, metric_date=metric_date)
    finally:
        db.close()

    print(render_report(snapshot))
    if write_files:
        paths = write_report_files(snapshot, _resolve_output_dir(output_dir))
        print()
        print("Wrote report artifacts:")
        print(f"  txt:  {paths['txt']}")
        print(f"  md:   {paths['md']}")
        print(f"  json: {paths['json']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AutoApply reporting snapshot")
    parser.add_argument("--db", default="autoapply.db", help="Path to SQLite database")
    parser.add_argument("--date", default=None, help="Metric date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--output-dir", default="logs/reports", help="Directory for report artifacts")
    parser.add_argument("--no-write", action="store_true", help="Print report only; do not write files")
    args = parser.parse_args()
    raise SystemExit(
        run(
            db_path=args.db,
            metric_date=args.date,
            output_dir=args.output_dir,
            write_files=not args.no_write,
        )
    )


if __name__ == "__main__":
    main()
