"""
SQLite database schema, migrations, and query helpers for AutoApply V2.2.
"""

import sqlite3
import os
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from src.utils import get_logger

logger = get_logger("db")

SCHEMA_VERSION = 3
_CONTACT_SCOPE_ALL = object()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    priority INTEGER NOT NULL DEFAULT 5,
    ats TEXT,
    slug TEXT,
    careers_url TEXT,
    jobs_url TEXT,
    workday_instance TEXT,
    workday_board TEXT,
    source TEXT NOT NULL DEFAULT 'watchlist',
    discovery_source TEXT,
    discovery_source_url TEXT,
    job_family_focus TEXT,
    notes TEXT,
    industry TEXT,
    headcount_range TEXT,
    hq_location TEXT,
    description TEXT,
    tech_stack TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    external_job_id TEXT,
    title TEXT NOT NULL,
    url TEXT,
    location TEXT,
    posting_text TEXT,
    job_family TEXT,
    source TEXT,
    qualification_status TEXT DEFAULT 'unscored',
    qualification_score INTEGER,
    qualification_reasons TEXT,
    qualification_mode TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, external_job_id)
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    job_id INTEGER REFERENCES jobs(id),
    name TEXT,
    email TEXT,
    role TEXT,
    confidence_tier TEXT NOT NULL,
    contact_source_type TEXT,
    source_url TEXT,
    evidence_snippet TEXT,
    verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    template_used TEXT,
    resume_variant TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'initial',
    message_quality_score INTEGER,
    review_required INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS domain_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    pattern TEXT NOT NULL,
    confidence TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 1,
    is_catch_all INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS suppression_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL,
    value TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(entry_type, value)
);

CREATE TABLE IF NOT EXISTS discovered_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_url TEXT,
    ats TEXT,
    ats_status TEXT NOT NULL DEFAULT 'pending',
    slug TEXT,
    careers_url TEXT,
    jobs_url TEXT,
    workday_instance TEXT,
    workday_board TEXT,
    priority INTEGER NOT NULL DEFAULT 5,
    industry TEXT,
    headcount_range TEXT,
    hq_location TEXT,
    description TEXT,
    tech_stack TEXT,
    promoted INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    ats_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS discovered_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    name TEXT,
    email TEXT,
    role TEXT,
    source TEXT NOT NULL,
    source_url TEXT,
    evidence_snippet TEXT,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
    companies_found INTEGER NOT NULL DEFAULT 0,
    page_hash TEXT,
    UNIQUE(source_name, source_url)
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_date TEXT NOT NULL UNIQUE,
    jobs_discovered INTEGER NOT NULL DEFAULT 0,
    jobs_qualified_auto INTEGER NOT NULL DEFAULT 0,
    jobs_qualified_review INTEGER NOT NULL DEFAULT 0,
    jobs_rejected INTEGER NOT NULL DEFAULT 0,
    contacts_resolved INTEGER NOT NULL DEFAULT 0,
    contacts_public_exact INTEGER NOT NULL DEFAULT 0,
    contacts_pattern_verified INTEGER NOT NULL DEFAULT 0,
    contacts_generic_inbox INTEGER NOT NULL DEFAULT 0,
    emails_sent INTEGER NOT NULL DEFAULT 0,
    followups_sent INTEGER NOT NULL DEFAULT 0,
    replies_received INTEGER NOT NULL DEFAULT 0,
    replies_positive INTEGER NOT NULL DEFAULT 0,
    bounces INTEGER NOT NULL DEFAULT 0,
    reviews_approved INTEGER NOT NULL DEFAULT 0,
    reviews_skipped INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    person_id INTEGER REFERENCES people(id),
    message_id INTEGER REFERENCES messages(id),
    queue_reason TEXT NOT NULL,
    confidence_tier TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(qualification_status);
CREATE INDEX IF NOT EXISTS idx_people_company ON people(company_id);
CREATE INDEX IF NOT EXISTS idx_people_email ON people(email);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_person ON messages(person_id);
CREATE INDEX IF NOT EXISTS idx_messages_job ON messages(job_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(review_status);
CREATE INDEX IF NOT EXISTS idx_suppression_type_value ON suppression_list(entry_type, value);
CREATE INDEX IF NOT EXISTS idx_daily_metrics_date ON daily_metrics(metric_date);
CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered_companies(ats_status, promoted, dismissed);
CREATE INDEX IF NOT EXISTS idx_discovered_domain ON discovered_companies(domain);
CREATE INDEX IF NOT EXISTS idx_disc_contacts_domain ON discovered_contacts(domain);
CREATE INDEX IF NOT EXISTS idx_scrape_log_source ON scrape_log(source_name, source_url);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def initialize(self):
        self.conn.executescript(SCHEMA_SQL)
        existing = self.conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        if existing is not None and existing < SCHEMA_VERSION:
            self._apply_migrations(existing)
        if self._column_exists("companies", "source"):
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_companies_source ON companies(source)"
            )
        if self._column_exists("people", "job_id"):
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_people_job ON people(job_id)"
            )
        if existing is None or existing < SCHEMA_VERSION:
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)

    def _apply_migrations(self, existing_version: int):
        if existing_version < 2:
            if not self._column_exists("companies", "workday_instance"):
                self.conn.execute("ALTER TABLE companies ADD COLUMN workday_instance TEXT")
            if not self._column_exists("companies", "workday_board"):
                self.conn.execute("ALTER TABLE companies ADD COLUMN workday_board TEXT")
            if not self._column_exists("companies", "source"):
                self.conn.execute("ALTER TABLE companies ADD COLUMN source TEXT DEFAULT 'watchlist'")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS discovered_companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    ats TEXT,
                    ats_status TEXT NOT NULL DEFAULT 'pending',
                    slug TEXT,
                    careers_url TEXT,
                    jobs_url TEXT,
                    workday_instance TEXT,
                    workday_board TEXT,
                    priority INTEGER NOT NULL DEFAULT 5,
                    promoted INTEGER NOT NULL DEFAULT 0,
                    dismissed INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
                    ats_checked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS scrape_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
                    companies_found INTEGER NOT NULL DEFAULT 0,
                    page_hash TEXT,
                    UNIQUE(source_name, source_url)
                );

                CREATE INDEX IF NOT EXISTS idx_companies_source ON companies(source);
                CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered_companies(ats_status, promoted, dismissed);
                CREATE INDEX IF NOT EXISTS idx_discovered_domain ON discovered_companies(domain);
                CREATE INDEX IF NOT EXISTS idx_scrape_log_source ON scrape_log(source_name, source_url);
            """)
            self.conn.execute(
                "UPDATE companies SET source = COALESCE(source, 'watchlist')"
            )
            self.conn.commit()
        if existing_version < 3:
            company_columns = {
                "discovery_source": "TEXT",
                "discovery_source_url": "TEXT",
                "industry": "TEXT",
                "headcount_range": "TEXT",
                "hq_location": "TEXT",
                "description": "TEXT",
                "tech_stack": "TEXT",
            }
            for column_name, column_type in company_columns.items():
                if not self._column_exists("companies", column_name):
                    self.conn.execute(
                        f"ALTER TABLE companies ADD COLUMN {column_name} {column_type}"
                    )

            discovered_columns = {
                "industry": "TEXT",
                "headcount_range": "TEXT",
                "hq_location": "TEXT",
                "description": "TEXT",
                "tech_stack": "TEXT",
            }
            for column_name, column_type in discovered_columns.items():
                if not self._column_exists("discovered_companies", column_name):
                    self.conn.execute(
                        f"ALTER TABLE discovered_companies ADD COLUMN {column_name} {column_type}"
                    )

            if not self._column_exists("people", "job_id"):
                self.conn.execute(
                    "ALTER TABLE people ADD COLUMN job_id INTEGER REFERENCES jobs(id)"
                )

            pattern_columns = {
                "success_count": "INTEGER NOT NULL DEFAULT 0",
                "failure_count": "INTEGER NOT NULL DEFAULT 0",
                "last_verified_at": "TEXT",
            }
            for column_name, column_type in pattern_columns.items():
                if not self._column_exists("domain_patterns", column_name):
                    self.conn.execute(
                        f"ALTER TABLE domain_patterns ADD COLUMN {column_name} {column_type}"
                    )

            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS discovered_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    name TEXT,
                    email TEXT,
                    role TEXT,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    evidence_snippet TEXT,
                    discovered_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_people_job ON people(job_id);
                CREATE INDEX IF NOT EXISTS idx_disc_contacts_domain ON discovered_contacts(domain);
            """)
            self.conn.commit()

    # ── Company queries ──

    def upsert_company(self, *, name: str, domain: str, priority: int = 5,
                       ats: str = None, slug: str = None, careers_url: str = None,
                       jobs_url: str = None, workday_instance: str = None,
                       workday_board: str = None, source: str = "watchlist",
                       discovery_source: str = None,
                       discovery_source_url: str = None,
                       job_family_focus: str = None, notes: str = None,
                       industry: str = None, headcount_range: str = None,
                       hq_location: str = None, description: str = None,
                       tech_stack: str = None) -> int:
        row = self.conn.execute(
            "SELECT id FROM companies WHERE domain = ?", (domain,)
        ).fetchone()
        if row:
            self.conn.execute("""
                UPDATE companies SET name=?, priority=?, ats=?, slug=?,
                    careers_url=?, jobs_url=?, workday_instance=?,
                    workday_board=?, source=?,
                    discovery_source=COALESCE(?, discovery_source),
                    discovery_source_url=COALESCE(?, discovery_source_url),
                    job_family_focus=?, notes=?,
                    industry=COALESCE(?, industry),
                    headcount_range=COALESCE(?, headcount_range),
                    hq_location=COALESCE(?, hq_location),
                    description=COALESCE(?, description),
                    tech_stack=COALESCE(?, tech_stack),
                    updated_at=datetime('now')
                WHERE id=?
            """, (name, priority, ats, slug, careers_url, jobs_url,
                  workday_instance, workday_board, source, discovery_source,
                  discovery_source_url, job_family_focus, notes, industry,
                  headcount_range, hq_location, description, tech_stack,
                  row["id"]))
            self.conn.commit()
            return row["id"]
        else:
            cur = self.conn.execute("""
                INSERT INTO companies (name, domain, priority, ats, slug,
                    careers_url, jobs_url, workday_instance, workday_board,
                    source, discovery_source, discovery_source_url,
                    job_family_focus, notes, industry, headcount_range,
                    hq_location, description, tech_stack)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, domain, priority, ats, slug, careers_url, jobs_url,
                  workday_instance, workday_board, source, discovery_source,
                  discovery_source_url, job_family_focus, notes, industry,
                  headcount_range, hq_location, description, tech_stack))
            self.conn.commit()
            return cur.lastrowid

    def get_company(self, company_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()

    def get_company_by_domain(self, domain: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM companies WHERE domain = ?", (domain,)
        ).fetchone()

    def get_all_companies(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM companies ORDER BY priority ASC, name ASC"
        ).fetchall()

    def get_promoted_companies(self) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT * FROM companies
            WHERE source = 'auto_discovered'
            ORDER BY priority ASC, name ASC
        """).fetchall()

    # ── Discovered company queries ──

    def insert_discovered_company(self, *, name: str, domain: str, source: str,
                                  source_url: str = None, priority: int = 5,
                                  industry: str = None, headcount_range: str = None,
                                  hq_location: str = None, description: str = None,
                                  tech_stack: str = None) -> Optional[int]:
        existing = self.get_discovered_company_by_domain(domain)
        cur = self.conn.execute("""
            INSERT INTO discovered_companies
                (name, domain, source, source_url, priority, industry,
                 headcount_range, hq_location, description, tech_stack)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                source_url = COALESCE(discovered_companies.source_url, excluded.source_url),
                priority = CASE
                    WHEN excluded.priority < discovered_companies.priority THEN excluded.priority
                    ELSE discovered_companies.priority
                END,
                industry = COALESCE(discovered_companies.industry, excluded.industry),
                headcount_range = COALESCE(discovered_companies.headcount_range, excluded.headcount_range),
                hq_location = COALESCE(discovered_companies.hq_location, excluded.hq_location),
                description = COALESCE(discovered_companies.description, excluded.description),
                tech_stack = COALESCE(discovered_companies.tech_stack, excluded.tech_stack)
        """, (name, domain, source, source_url, priority, industry,
              headcount_range, hq_location, description, tech_stack))
        self.conn.commit()
        if existing:
            return None
        return cur.lastrowid if cur.rowcount else None

    def get_discovered_company_by_domain(self, domain: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM discovered_companies WHERE domain = ?", (domain,)
        ).fetchone()

    def get_unpromoted_companies(self, ats_status: str = "detected") -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT * FROM discovered_companies
            WHERE ats_status = ? AND promoted = 0 AND dismissed = 0
            ORDER BY priority ASC, discovered_at ASC
        """, (ats_status,)).fetchall()

    def get_pending_ats_check(self) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT * FROM discovered_companies
            WHERE ats_status = 'pending' AND dismissed = 0
            ORDER BY discovered_at ASC
        """).fetchall()

    def update_ats_info(self, discovered_id: int, ats: str = None, slug: str = None,
                        careers_url: str = None, jobs_url: str = None,
                        workday_instance: str = None, workday_board: str = None):
        status = "detected" if ats else "unknown"
        self.conn.execute("""
            UPDATE discovered_companies
            SET ats=?, ats_status=?, slug=?, careers_url=?, jobs_url=?,
                workday_instance=?, workday_board=?, ats_checked_at=datetime('now')
            WHERE id=?
        """, (ats, status, slug, careers_url, jobs_url,
              workday_instance, workday_board, discovered_id))
        self.conn.commit()

    def promote_company(self, discovered_id: int) -> Optional[int]:
        row = self.conn.execute(
            "SELECT * FROM discovered_companies WHERE id = ?", (discovered_id,)
        ).fetchone()
        if not row or row["dismissed"]:
            return None

        existing = self.get_company_by_domain(row["domain"])
        source = "auto_discovered"
        if existing and existing["source"] == "watchlist":
            source = "watchlist"
            logger.info("Promote: %s already in watchlist — keeping watchlist source", row["domain"])

        company_id = self.upsert_company(
            name=row["name"],
            domain=row["domain"],
            priority=row["priority"],
            ats=row["ats"],
            slug=row["slug"],
            careers_url=row["careers_url"],
            jobs_url=row["jobs_url"],
            workday_instance=row["workday_instance"],
            workday_board=row["workday_board"],
            discovery_source=row["source"],
            discovery_source_url=row["source_url"],
            job_family_focus=None,
            notes=f"Auto-discovered from {row['source']}",
            source=source,
            industry=row["industry"],
            headcount_range=row["headcount_range"],
            hq_location=row["hq_location"],
            description=row["description"],
            tech_stack=row["tech_stack"],
        )
        self.conn.execute(
            "UPDATE discovered_companies SET promoted = 1 WHERE id = ?",
            (discovered_id,)
        )
        self.conn.commit()
        return company_id

    def dismiss_company(self, discovered_id: int, reason: str = None):
        row = self.conn.execute(
            "SELECT domain FROM discovered_companies WHERE id = ?", (discovered_id,)
        ).fetchone()
        if not row:
            return
        self.conn.execute(
            "UPDATE discovered_companies SET dismissed = 1 WHERE id = ?",
            (discovered_id,)
        )
        self.conn.commit()
        self.add_suppression("domain", row["domain"], reason or "dismissed discovered company")

    def log_scrape(self, source_name: str, source_url: str,
                   companies_found: int = 0, page_hash: str = None):
        self.conn.execute("""
            INSERT INTO scrape_log (source_name, source_url, companies_found, page_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_name, source_url) DO UPDATE SET
                scraped_at=datetime('now'),
                companies_found=excluded.companies_found,
                page_hash=excluded.page_hash
        """, (source_name, source_url, companies_found, page_hash))
        self.conn.commit()

    def get_last_scrape(self, source_name: str, source_url: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("""
            SELECT * FROM scrape_log
            WHERE source_name = ? AND source_url = ?
        """, (source_name, source_url)).fetchone()

    # ── Job queries ──

    def insert_job(self, *, company_id: int, external_job_id: str, title: str,
                   url: str = None, location: str = None, posting_text: str = None,
                   job_family: str = None, source: str = None) -> Optional[int]:
        try:
            cur = self.conn.execute("""
                INSERT INTO jobs (company_id, external_job_id, title, url,
                    location, posting_text, job_family, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (company_id, external_job_id, title, url, location,
                  posting_text, job_family, source))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Duplicate — update last_seen_at
            self.conn.execute("""
                UPDATE jobs SET last_seen_at = datetime('now'),
                    title = COALESCE(?, title),
                    url = COALESCE(?, url),
                    location = COALESCE(?, location),
                    posting_text = COALESCE(?, posting_text),
                    job_family = COALESCE(?, job_family),
                    source = COALESCE(?, source),
                    closed_at = NULL
                WHERE company_id = ? AND external_job_id = ?
            """, (title, url, location, posting_text, job_family, source,
                  company_id, external_job_id))
            self.conn.commit()
            return None

    def get_unscored_jobs(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE qualification_status = 'unscored' AND closed_at IS NULL"
        ).fetchall()

    def update_job_score(self, job_id: int, status: str, score: int,
                         reasons: str, mode: str = "keyword"):
        self.conn.execute("""
            UPDATE jobs SET qualification_status=?, qualification_score=?,
                qualification_reasons=?, qualification_mode=?
            WHERE id=?
        """, (status, score, reasons, mode, job_id))
        self.conn.commit()

    def get_qualified_jobs(self, status: str = "qualified_auto") -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT j.*, c.name as company_name, c.domain as company_domain
            FROM jobs j JOIN companies c ON j.company_id = c.id
            WHERE j.qualification_status = ? AND j.closed_at IS NULL
        """, (status,)).fetchall()

    def mark_jobs_closed(self, company_id: int, active_external_ids: List[str]):
        if not active_external_ids:
            self.conn.execute("""
                UPDATE jobs SET closed_at = datetime('now')
                WHERE company_id = ? AND closed_at IS NULL
            """, (company_id,))
            self.conn.commit()
            return
        placeholders = ",".join("?" * len(active_external_ids))
        self.conn.execute(f"""
            UPDATE jobs SET closed_at = datetime('now')
            WHERE company_id = ? AND external_job_id NOT IN ({placeholders})
                AND closed_at IS NULL
        """, [company_id] + active_external_ids)
        self.conn.commit()

    # ── People queries ──

    def insert_person(self, *, company_id: int, name: str = None,
                      job_id: int = None,
                      email: str = None, role: str = None,
                      confidence_tier: str, contact_source_type: str = None,
                      source_url: str = None, evidence_snippet: str = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO people (company_id, job_id, name, email, role, confidence_tier,
                contact_source_type, source_url, evidence_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (company_id, job_id, name, email, role, confidence_tier,
              contact_source_type, source_url, evidence_snippet))
        self.conn.commit()
        return cur.lastrowid

    def get_pending_contacts(self, company_id: int, job_id=_CONTACT_SCOPE_ALL) -> List[sqlite3.Row]:
        base_query = """
            SELECT * FROM people WHERE company_id = ?
            ORDER BY
                CASE confidence_tier
                    WHEN 'public_exact' THEN 1
                    WHEN 'public_generic_inbox' THEN 2
                    WHEN 'pattern_verified' THEN 3
                    WHEN 'catch_all_pattern_match' THEN 4
                    WHEN 'pattern_inferred' THEN 5
                    WHEN 'catch_all_guess' THEN 6
                    WHEN 'generic_guess' THEN 7
                    ELSE 8
                END
        """
        params: List[Any] = [company_id]
        if job_id is _CONTACT_SCOPE_ALL:
            query = base_query
        elif job_id is None:
            query = base_query.replace("WHERE company_id = ?", "WHERE company_id = ? AND job_id IS NULL")
        else:
            query = base_query.replace("WHERE company_id = ?", "WHERE company_id = ? AND job_id = ?")
            params.append(job_id)
        return self.conn.execute(query, params).fetchall()

    def get_contacts_for_job(self, job_id: int) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT * FROM people
            WHERE job_id = ?
            ORDER BY
                CASE confidence_tier
                    WHEN 'public_exact' THEN 1
                    WHEN 'public_generic_inbox' THEN 2
                    WHEN 'pattern_verified' THEN 3
                    WHEN 'catch_all_pattern_match' THEN 4
                    WHEN 'pattern_inferred' THEN 5
                    WHEN 'catch_all_guess' THEN 6
                    WHEN 'generic_guess' THEN 7
                    ELSE 8
                END
        """, (job_id,)).fetchall()

    @staticmethod
    def _is_real_person_contact(contact) -> bool:
        """
        Check if a contact looks like a real person vs scraped junk.
        Rejects: generic inboxes, functional mailboxes, and contacts
        whose "name" is clearly scraped HTML text rather than a person.
        """
        import re as _re

        email = contact["email"]
        if not email:
            return False

        local_part = email.split("@")[0].lower()

        # Reject generic / functional inboxes
        GENERIC_LOCAL_PARTS = {
            "careers", "recruiting", "jobs", "talent", "hr",
            "hiring", "people", "team", "apply", "info",
            "contact", "hello", "support", "admin",
            "accommodations", "resume", "resumes", "cv",
            "press", "media", "privacy", "legal", "sales",
            "security", "compliance", "noreply", "no-reply",
        }
        if local_part in GENERIC_LOCAL_PARTS:
            return False

        # Reject emails whose local part contains HTML/junk artifacts
        if "u003e" in local_part or "u003c" in local_part:
            return False

        # If there's a name, validate it looks like a real person name
        name = contact["name"]
        if name:
            # Real names are 2-4 words, < 40 chars, all alphabetic words
            name_stripped = name.strip()
            if len(name_stripped) > 40:
                return False
            words = name_stripped.split()
            if len(words) < 2 or len(words) > 4:
                return False
            # Each word should be mostly alpha (allow hyphens, apostrophes)
            for word in words:
                cleaned = word.replace("-", "").replace("'", "").replace(".", "")
                if not cleaned.isalpha():
                    return False
            # Reject names that are clearly scraped text fragments
            JUNK_NAME_WORDS = {
                "the", "and", "for", "from", "with", "our", "can", "will",
                "you", "your", "more", "about", "this", "that", "all",
                "not", "via", "per", "has", "are", "was", "get", "at",
                "us", "we", "or", "in", "on", "to", "by", "of", "is",
                "share", "discuss", "information", "process",
                "contact", "support", "press", "here", "click",
            }
            name_lower_words = {w.lower() for w in words}
            if name_lower_words & JUNK_NAME_WORDS:
                return False
        else:
            # No name — check if the email itself looks like a person
            # (first.last@ or firstlast@ patterns, not functional)
            if not _re.match(r'^[a-z]+[._][a-z]+$', local_part):
                # Could still be valid if it's a clear first.last pattern
                # but if it doesn't look like a name, skip it
                return False

        return True

    def get_best_contact(self, company_id: int, job_id: int = None,
                         skip_generic: bool = True) -> Optional[sqlite3.Row]:
        """
        Get the best contact for a company.
        If skip_generic=True (default), filters out generic inboxes and
        scraped-text junk, returning only contacts that are real people.
        Cold outreach to generic inboxes is a waste — nobody reads those.
        """
        contact_sets: List[List[sqlite3.Row]] = []
        if job_id is not None:
            contact_sets.append(self.get_pending_contacts(company_id, job_id=job_id))
        contact_sets.append(self.get_pending_contacts(company_id, job_id=None))
        if job_id is None:
            contact_sets = [self.get_pending_contacts(company_id)]

        for contacts in contact_sets:
            if not contacts:
                continue
            if skip_generic:
                real_contacts = [c for c in contacts if self._is_real_person_contact(c)]
                if real_contacts:
                    return real_contacts[0]
                continue
            return contacts[0]

        return None

    # ── Message queries ──

    def insert_message(self, *, job_id: int, person_id: int, company_id: int,
                       template_used: str = None, resume_variant: str = None,
                       subject: str, body: str, message_type: str = "initial",
                       message_quality_score: int = None,
                       review_required: bool = False) -> int:
        cur = self.conn.execute("""
            INSERT INTO messages (job_id, person_id, company_id, template_used,
                resume_variant, subject, body, message_type,
                message_quality_score, review_required, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, person_id, company_id, template_used, resume_variant,
              subject, body, message_type, message_quality_score,
              1 if review_required else 0,
              "pending_review" if review_required else "ready"))
        self.conn.commit()
        return cur.lastrowid

    def get_ready_messages(self) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT m.*, p.name as contact_name, p.email as contact_email,
                p.confidence_tier, c.name as company_name, j.title as job_title
            FROM messages m
            JOIN people p ON m.person_id = p.id
            JOIN companies c ON m.company_id = c.id
            JOIN jobs j ON m.job_id = j.id
            WHERE m.status = 'ready'
        """).fetchall()

    def update_message_status(self, message_id: int, status: str,
                              gmail_message_id: str = None,
                              gmail_thread_id: str = None):
        self.conn.execute("""
            UPDATE messages SET status=?, gmail_message_id=?, gmail_thread_id=?,
                sent_at = CASE WHEN ? = 'sent' THEN datetime('now') ELSE sent_at END
            WHERE id=?
        """, (status, gmail_message_id, gmail_thread_id, status, message_id))
        self.conn.commit()

    def get_sent_messages_for_followup(self, min_age_days: int) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT m.*, p.email as contact_email, p.name as contact_name,
                c.name as company_name, j.title as job_title
            FROM messages m
            JOIN people p ON m.person_id = p.id
            JOIN companies c ON m.company_id = c.id
            JOIN jobs j ON m.job_id = j.id
            WHERE m.status = 'sent'
                AND m.message_type = 'initial'
                AND julianday('now') - julianday(m.sent_at) >= ?
                AND NOT EXISTS (
                    SELECT 1 FROM messages m2
                    WHERE m2.job_id = m.job_id AND m2.person_id = m.person_id
                        AND m2.message_type != 'initial'
                )
        """, (min_age_days,)).fetchall()

    # ── Suppression queries ──

    def check_suppression(self, email: str = None, domain: str = None,
                          company_name: str = None) -> bool:
        checks = []
        if email:
            checks.append(("email", email))
        if domain:
            checks.append(("domain", domain))
        if company_name:
            checks.append(("company", company_name))
        for entry_type, value in checks:
            row = self.conn.execute(
                "SELECT 1 FROM suppression_list WHERE entry_type=? AND value=?",
                (entry_type, value)
            ).fetchone()
            if row:
                return True
        return False

    def add_suppression(self, entry_type: str, value: str, reason: str = None):
        self.conn.execute(
            "INSERT OR IGNORE INTO suppression_list (entry_type, value, reason) VALUES (?, ?, ?)",
            (entry_type, value, reason)
        )
        self.conn.commit()

    # ── Cooldown checks ──

    def check_person_has_pending_message(self, person_id: int) -> bool:
        """Check if this person already has an unsent message (ready, pending_review, etc.)."""
        row = self.conn.execute("""
            SELECT 1 FROM messages
            WHERE person_id = ? AND status IN ('ready', 'pending_review', 'approved')
        """, (person_id,)).fetchone()
        return row is not None

    def check_person_cooldown(self, person_id: int, days: int = 90) -> bool:
        row = self.conn.execute("""
            SELECT 1 FROM messages
            WHERE person_id = ? AND status = 'sent'
                AND julianday('now') - julianday(sent_at) < ?
        """, (person_id, days)).fetchone()
        return row is not None

    def check_company_job_family_cooldown(self, company_id: int,
                                          job_family: str, days: int = 30) -> bool:
        row = self.conn.execute("""
            SELECT 1 FROM messages m
            JOIN jobs j ON m.job_id = j.id
            WHERE m.company_id = ? AND j.job_family = ? AND m.status = 'sent'
                AND julianday('now') - julianday(m.sent_at) < ?
        """, (company_id, job_family, days)).fetchone()
        return row is not None

    def check_exact_posting_contacted(self, job_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE job_id = ? AND status = 'sent'",
            (job_id,)
        ).fetchone()
        return row is not None

    def check_exact_posting_already_assembled(self, job_id: int,
                                              message_type: str = "initial") -> bool:
        """
        Check whether we already have a message for this exact posting.

        This prevents repeated dry-runs or assembly reruns from creating
        duplicate unsent drafts/review items for the same job.
        """
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE job_id = ? AND message_type = ?",
            (job_id, message_type)
        ).fetchone()
        return row is not None

    # ── Domain pattern queries ──

    def get_domain_pattern(self, domain: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM domain_patterns WHERE domain = ?", (domain,)
        ).fetchone()

    def upsert_domain_pattern(self, domain: str, pattern: str,
                              confidence: str, is_catch_all: bool = False):
        self.conn.execute("""
            INSERT INTO domain_patterns (domain, pattern, confidence, is_catch_all)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                pattern=excluded.pattern,
                confidence=excluded.confidence,
                is_catch_all=excluded.is_catch_all,
                sample_count=sample_count+1,
                updated_at=datetime('now')
        """, (domain, pattern, confidence, 1 if is_catch_all else 0))
        self.conn.commit()

    def record_pattern_outcome(self, domain: str, success: bool):
        field = "success_count" if success else "failure_count"
        self.conn.execute(f"""
            UPDATE domain_patterns
            SET {field} = {field} + 1,
                last_verified_at = datetime('now'),
                updated_at = datetime('now')
            WHERE domain = ?
        """, (domain,))
        self.conn.commit()

    def insert_discovered_contact(self, *, domain: str, name: str = None,
                                  email: str = None, role: str = None,
                                  source: str, source_url: str = None,
                                  evidence_snippet: str = None) -> Optional[int]:
        existing = self.conn.execute("""
            SELECT id FROM discovered_contacts
            WHERE domain = ?
              AND COALESCE(name, '') = COALESCE(?, '')
              AND COALESCE(email, '') = COALESCE(?, '')
              AND COALESCE(role, '') = COALESCE(?, '')
              AND source = ?
              AND COALESCE(source_url, '') = COALESCE(?, '')
        """, (domain, name, email, role, source, source_url)).fetchone()
        if existing:
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO discovered_contacts (domain, name, email, role, source, source_url, evidence_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (domain, name, email, role, source, source_url, evidence_snippet))
        self.conn.commit()
        return cur.lastrowid

    def get_discovered_contacts(self, domain: str) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT * FROM discovered_contacts
            WHERE domain = ?
            ORDER BY discovered_at ASC, id ASC
        """, (domain,)).fetchall()

    def get_pipeline_funnel(self, discovery_source: str = None) -> List[sqlite3.Row]:
        query = """
            SELECT
                c.discovery_source,
                COUNT(DISTINCT c.id) as companies,
                COUNT(DISTINCT j.id) as jobs,
                COUNT(DISTINCT CASE
                    WHEN j.qualification_status IN ('qualified_auto', 'qualified_review') THEN j.id
                END) as qualified,
                COUNT(DISTINCT p.id) as contacts,
                COUNT(DISTINCT CASE WHEN m.status = 'sent' THEN m.id END) as sent,
                COUNT(DISTINCT CASE WHEN m.status LIKE 'replied_%' THEN m.id END) as replies
            FROM companies c
            LEFT JOIN jobs j ON j.company_id = c.id
            LEFT JOIN people p ON p.company_id = c.id
            LEFT JOIN messages m ON m.company_id = c.id
            WHERE c.discovery_source IS NOT NULL
        """
        params: List[Any] = []
        if discovery_source is not None:
            query += " AND c.discovery_source = ?"
            params.append(discovery_source)
        query += """
            GROUP BY c.discovery_source
            ORDER BY companies DESC, c.discovery_source ASC
        """
        return self.conn.execute(query, params).fetchall()

    # ── Review queue queries ──

    def insert_review_item(self, *, job_id: int, person_id: int = None,
                           message_id: int = None, queue_reason: str,
                           confidence_tier: str = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO review_queue (job_id, person_id, message_id,
                queue_reason, confidence_tier)
            VALUES (?, ?, ?, ?, ?)
        """, (job_id, person_id, message_id, queue_reason, confidence_tier))
        self.conn.commit()
        return cur.lastrowid

    def get_pending_reviews(self) -> List[sqlite3.Row]:
        return self.conn.execute("""
            SELECT rq.*, j.title as job_title, j.url as job_url,
                c.name as company_name, c.domain as company_domain,
                p.name as contact_name, p.email as contact_email,
                p.confidence_tier as contact_confidence,
                m.subject as email_subject, m.body as email_body,
                m.resume_variant
            FROM review_queue rq
            JOIN jobs j ON rq.job_id = j.id
            JOIN companies c ON j.company_id = c.id
            LEFT JOIN people p ON rq.person_id = p.id
            LEFT JOIN messages m ON rq.message_id = m.id
            WHERE rq.review_status = 'pending'
            ORDER BY rq.created_at ASC
        """).fetchall()

    def update_review_status(self, review_id: int, status: str,
                             notes: str = None):
        self.conn.execute("""
            UPDATE review_queue SET review_status=?, reviewed_at=datetime('now'),
                review_notes=?
            WHERE id=?
        """, (status, notes, review_id))
        self.conn.commit()

    def get_review_approval_rate(self, last_n: int = 20) -> Optional[float]:
        rows = self.conn.execute("""
            SELECT review_status FROM review_queue
            WHERE review_status IN ('approved', 'skipped')
            ORDER BY reviewed_at DESC LIMIT ?
        """, (last_n,)).fetchall()
        if not rows:
            return None
        approved = sum(1 for r in rows if r["review_status"] == "approved")
        return approved / len(rows)

    # ── Metrics queries ──

    def get_or_create_daily_metrics(self, metric_date: str = None) -> int:
        if metric_date is None:
            metric_date = date.today().isoformat()
        row = self.conn.execute(
            "SELECT id FROM daily_metrics WHERE metric_date = ?", (metric_date,)
        ).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO daily_metrics (metric_date) VALUES (?)", (metric_date,)
        )
        self.conn.commit()
        return cur.lastrowid

    def increment_metric(self, field: str, amount: int = 1,
                         metric_date: str = None):
        metrics_id = self.get_or_create_daily_metrics(metric_date)
        self.conn.execute(
            f"UPDATE daily_metrics SET {field} = {field} + ? WHERE id = ?",
            (amount, metrics_id)
        )
        self.conn.commit()

    def get_daily_metrics(self, metric_date: str = None) -> Optional[sqlite3.Row]:
        if metric_date is None:
            metric_date = date.today().isoformat()
        return self.conn.execute(
            "SELECT * FROM daily_metrics WHERE metric_date = ?", (metric_date,)
        ).fetchone()

    # ── Safety stop queries ──

    def get_recent_bounce_rate(self, last_n: int = 50) -> float:
        rows = self.conn.execute("""
            SELECT status FROM messages
            WHERE status IN ('sent', 'bounced', 'replied_bounce')
            ORDER BY sent_at DESC LIMIT ?
        """, (last_n,)).fetchall()
        if not rows:
            return 0.0
        bounced = sum(1 for r in rows if r["status"] in ("bounced", "replied_bounce"))
        return bounced / len(rows)

    def get_today_send_count(self) -> int:
        row = self.conn.execute("""
            SELECT COUNT(*) as cnt FROM messages
            WHERE status = 'sent' AND date(sent_at) = date('now')
                AND message_type = 'initial'
        """).fetchone()
        return row["cnt"]

    def get_today_followup_count(self) -> int:
        row = self.conn.execute("""
            SELECT COUNT(*) as cnt FROM messages
            WHERE status = 'sent' AND date(sent_at) = date('now')
                AND message_type != 'initial'
        """).fetchone()
        return row["cnt"]
