"""Minimal SQLite helpers for the AI-assisted AutoApply toolkit."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outreach_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email TEXT NOT NULL,
    to_name TEXT,
    company_domain TEXT NOT NULL,
    company_name TEXT,
    job_title TEXT,
    job_url TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    resume_used TEXT,
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    status TEXT NOT NULL DEFAULT 'sent',
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    agent_session TEXT
);

CREATE INDEX IF NOT EXISTS idx_outreach_email ON outreach_log(to_email);
CREATE INDEX IF NOT EXISTS idx_outreach_domain ON outreach_log(company_domain);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_log(status);

CREATE TABLE IF NOT EXISTS suppression_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL,
    value TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(entry_type, value)
);

CREATE TABLE IF NOT EXISTS domain_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    pattern TEXT NOT NULL,
    confidence TEXT NOT NULL,
    is_catch_all INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _module_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path

    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate

    module_candidate = _module_dir() / path
    if module_candidate.exists():
        return module_candidate

    return cwd_candidate


class ToolkitDB:
    """Small database wrapper used by the agent-facing toolkit."""

    def __init__(self, db_path: str):
        self.db_path = _resolve_path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.initialize()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ToolkitDB":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def check_already_contacted(self, email: str) -> bool:
        normalized = self._normalize_email(email)
        if not normalized:
            return False

        domain = normalized.split("@", 1)[1]
        suppressed = self.conn.execute(
            """
            SELECT 1
            FROM suppression_list
            WHERE (entry_type = 'email' AND lower(value) = ?)
               OR (entry_type = 'domain' AND lower(value) = ?)
            LIMIT 1
            """,
            (normalized, domain),
        ).fetchone()
        if suppressed:
            return True

        prior = self.conn.execute(
            """
            SELECT 1
            FROM outreach_log
            WHERE lower(to_email) = ?
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        return prior is not None

    def check_company_contacted_recently(self, domain: str, days: int = 30) -> bool:
        normalized = self._normalize_domain(domain)
        if not normalized:
            return False

        row = self.conn.execute(
            """
            SELECT 1
            FROM outreach_log
            WHERE lower(company_domain) = ?
              AND sent_at >= datetime('now', ?)
            LIMIT 1
            """,
            (normalized, f"-{int(days)} days"),
        ).fetchone()
        return row is not None

    def record_send(
        self,
        to_email: str,
        to_name: Optional[str],
        company_domain: str,
        company_name: Optional[str],
        job_title: Optional[str],
        job_url: Optional[str],
        subject: str,
        body: str,
        gmail_message_id: Optional[str],
        *,
        gmail_thread_id: Optional[str] = None,
        resume_used: Optional[str] = None,
        status: str = "sent",
        agent_session: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO outreach_log (
                to_email,
                to_name,
                company_domain,
                company_name,
                job_title,
                job_url,
                subject,
                body,
                resume_used,
                gmail_message_id,
                gmail_thread_id,
                status,
                agent_session
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._normalize_email(to_email),
                self._normalize_text(to_name),
                self._normalize_domain(company_domain),
                self._normalize_text(company_name),
                self._normalize_text(job_title),
                self._normalize_text(job_url),
                subject,
                body,
                self._normalize_text(resume_used),
                self._normalize_text(gmail_message_id),
                self._normalize_text(gmail_thread_id),
                status,
                self._normalize_text(agent_session),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_suppression(
        self,
        *,
        email: Optional[str] = None,
        domain: Optional[str] = None,
        company: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        entries = []
        if email:
            entries.append(("email", self._normalize_email(email)))
        if domain:
            entries.append(("domain", self._normalize_domain(domain)))
        if company:
            entries.append(("company", company.strip()))

        if not entries:
            raise ValueError("At least one suppression target is required.")

        for entry_type, value in entries:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO suppression_list (entry_type, value, reason)
                VALUES (?, ?, ?)
                """,
                (entry_type, value, reason),
            )
        self.conn.commit()

    def get_send_history(self, days: int = 30) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM outreach_log
            WHERE sent_at >= datetime('now', ?)
            ORDER BY sent_at DESC, id DESC
            """,
            (f"-{int(days)} days",),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_today_send_count(self) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM outreach_log
            WHERE status = 'sent'
              AND date(sent_at) = date('now')
            """
        ).fetchone()
        return int(row["count"])

    def get_domain_pattern(self, domain: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM domain_patterns WHERE lower(domain) = ?",
            (self._normalize_domain(domain),),
        ).fetchone()

    def upsert_domain_pattern(
        self,
        domain: str,
        pattern: str,
        confidence: str,
        *,
        is_catch_all: bool = False,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO domain_patterns (
                domain,
                pattern,
                confidence,
                is_catch_all,
                updated_at
            )
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(domain) DO UPDATE SET
                pattern = excluded.pattern,
                confidence = excluded.confidence,
                is_catch_all = excluded.is_catch_all,
                updated_at = datetime('now')
            """,
            (
                self._normalize_domain(domain),
                pattern,
                confidence,
                1 if is_catch_all else 0,
            ),
        )
        self.conn.commit()

    def record_pattern_outcome(self, domain: str, success: bool) -> None:
        field = "success_count" if success else "failure_count"
        self.conn.execute(
            f"""
            UPDATE domain_patterns
            SET {field} = {field} + 1,
                updated_at = datetime('now')
            WHERE lower(domain) = ?
            """,
            (self._normalize_domain(domain),),
        )
        self.conn.commit()

    def migrate_legacy_data(self, source_db_path: Optional[str] = None) -> Dict[str, int]:
        source_path = self.db_path if source_db_path is None else _resolve_path(source_db_path)
        same_database = source_path.resolve() == self.db_path.resolve()

        source_conn = self.conn if same_database else sqlite3.connect(str(source_path))
        source_conn.row_factory = sqlite3.Row

        try:
            summary = {
                "outreach_rows_inserted": 0,
                "suppression_rows_copied": 0,
                "pattern_rows_copied": 0,
            }

            if self._table_exists(source_conn, "messages"):
                summary["outreach_rows_inserted"] = self._copy_legacy_outreach(source_conn)

            if not same_database and self._table_exists(source_conn, "suppression_list"):
                summary["suppression_rows_copied"] = self._copy_suppression_list(source_conn)

            if not same_database and self._table_exists(source_conn, "domain_patterns"):
                summary["pattern_rows_copied"] = self._copy_domain_patterns(source_conn)

            self.conn.commit()
            return summary
        finally:
            if not same_database:
                source_conn.close()

    def _copy_legacy_outreach(self, source_conn: sqlite3.Connection) -> int:
        rows = source_conn.execute(
            """
            SELECT
                lower(trim(p.email)) AS to_email,
                p.name AS to_name,
                lower(trim(c.domain)) AS company_domain,
                c.name AS company_name,
                j.title AS job_title,
                j.url AS job_url,
                m.subject AS subject,
                m.body AS body,
                m.resume_variant AS resume_used,
                m.gmail_message_id AS gmail_message_id,
                m.gmail_thread_id AS gmail_thread_id,
                CASE
                    WHEN m.status LIKE 'replied%' THEN 'replied'
                    WHEN m.status = 'bounced' THEN 'bounced'
                    ELSE 'sent'
                END AS status,
                COALESCE(m.sent_at, m.created_at) AS sent_at
            FROM messages m
            JOIN people p ON m.person_id = p.id
            JOIN companies c ON m.company_id = c.id
            LEFT JOIN jobs j ON m.job_id = j.id
            WHERE trim(COALESCE(p.email, '')) != ''
              AND (
                    m.status = 'sent'
                    OR m.status = 'bounced'
                    OR m.status LIKE 'replied%'
                  )
            ORDER BY COALESCE(m.sent_at, m.created_at) ASC, m.id ASC
            """
        ).fetchall()

        inserted = 0
        for row in rows:
            exists = self.conn.execute(
                """
                SELECT 1
                FROM outreach_log
                WHERE lower(to_email) = ?
                  AND company_domain = ?
                  AND subject = ?
                  AND body = ?
                  AND COALESCE(gmail_message_id, '') = COALESCE(?, '')
                  AND sent_at = ?
                LIMIT 1
                """,
                (
                    row["to_email"],
                    row["company_domain"],
                    row["subject"],
                    row["body"],
                    row["gmail_message_id"],
                    row["sent_at"],
                ),
            ).fetchone()
            if exists:
                continue

            self.conn.execute(
                """
                INSERT INTO outreach_log (
                    to_email,
                    to_name,
                    company_domain,
                    company_name,
                    job_title,
                    job_url,
                    subject,
                    body,
                    resume_used,
                    gmail_message_id,
                    gmail_thread_id,
                    status,
                    sent_at,
                    agent_session
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["to_email"],
                    row["to_name"],
                    row["company_domain"],
                    row["company_name"],
                    row["job_title"],
                    row["job_url"],
                    row["subject"],
                    row["body"],
                    row["resume_used"],
                    row["gmail_message_id"],
                    row["gmail_thread_id"],
                    row["status"],
                    row["sent_at"],
                    "legacy-migration",
                ),
            )
            inserted += 1

        return inserted

    def _copy_suppression_list(self, source_conn: sqlite3.Connection) -> int:
        rows = source_conn.execute(
            "SELECT entry_type, value, reason, created_at FROM suppression_list"
        ).fetchall()
        inserted = 0
        for row in rows:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO suppression_list (entry_type, value, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (row["entry_type"], row["value"], row["reason"], row["created_at"]),
            )
            inserted += int(cur.rowcount > 0)
        return inserted

    def _copy_domain_patterns(self, source_conn: sqlite3.Connection) -> int:
        rows = source_conn.execute(
            """
            SELECT domain, pattern, confidence, is_catch_all, success_count, failure_count, updated_at
            FROM domain_patterns
            """
        ).fetchall()
        inserted = 0
        for row in rows:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO domain_patterns (
                    domain,
                    pattern,
                    confidence,
                    is_catch_all,
                    success_count,
                    failure_count,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["domain"],
                    row["pattern"],
                    row["confidence"],
                    row["is_catch_all"],
                    row["success_count"],
                    row["failure_count"],
                    row["updated_at"],
                ),
            )
            inserted += int(cur.rowcount > 0)
        return inserted

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _normalize_email(email: Optional[str]) -> str:
        return (email or "").strip().lower()

    @staticmethod
    def _normalize_domain(domain: Optional[str]) -> str:
        return (domain or "").strip().lower()

    @staticmethod
    def _normalize_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
