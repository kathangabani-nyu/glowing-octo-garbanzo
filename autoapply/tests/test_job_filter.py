"""Tests for job_filter module."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_config
from src.db import Database
from src.job_filter import run, score_job


CONFIG_YAML = """
sender:
  name: Tester
  email: tester@example.com
job_targets:
  title_keywords:
    - machine learning
    - software engineer
  title_exclude:
    - intern
  skills:
    - python
    - pytorch
    - kubernetes
  seniority:
    - mid
  locations:
    - new york
    - remote
  remote_ok: true
  min_experience_years: 1
  max_experience_years: 4
  visa_reject_keywords:
    - no sponsorship
qualification:
  auto_threshold: 70
  review_threshold: 40
"""


class TestJobFilter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(CONFIG_YAML)
        self.config = load_config(self.config_path)

        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()
        self.company_id = self.db.upsert_company(
            name="Acme",
            domain="acme.com",
            priority=2,
        )

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def _insert_job(self, *, title: str, location: str, posting_text: str):
        return self.db.insert_job(
            company_id=self.company_id,
            external_job_id=f"{title}-{location}",
            title=title,
            url="https://acme.com/jobs/1",
            location=location,
            posting_text=posting_text,
            job_family="software",
            source="greenhouse_api",
        )

    def test_score_job_auto_qualified(self):
        job_id = self._insert_job(
            title="Machine Learning Engineer",
            location="Remote",
            posting_text="We use Python, PyTorch, and Kubernetes. 3+ years experience preferred.",
        )
        job = self.db.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        company = self.db.get_company(self.company_id)

        result = score_job(self.config, job, company)
        self.assertEqual(result.status, "qualified_auto")
        self.assertGreaterEqual(result.score, 70)

    def test_score_job_rejects_excluded_title(self):
        job_id = self._insert_job(
            title="Machine Learning Intern",
            location="Remote",
            posting_text="Python experience welcomed.",
        )
        job = self.db.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        company = self.db.get_company(self.company_id)

        result = score_job(self.config, job, company)
        self.assertEqual(result.status, "reject")

    def test_score_job_rejects_domain_profile_role(self):
        job_id = self._insert_job(
            title="Fraud Analyst",
            location="Remote",
            posting_text="Python and SQL experience preferred.",
        )
        job = self.db.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        company = self.db.get_company(self.company_id)

        result = score_job(self.config, job, company)
        self.assertEqual(result.status, "reject")

    def test_run_updates_job_statuses_and_metrics(self):
        self._insert_job(
            title="Software Engineer",
            location="Austin",
            posting_text="Python role for engineers. 2+ years experience.",
        )
        self._insert_job(
            title="Platform Engineer",
            location="Austin",
            posting_text="Kubernetes experience helpful. 10+ years experience. No sponsorship.",
        )

        processed = run(self.config, self.db)

        self.assertEqual(processed, 2)
        statuses = [
            row["qualification_status"]
            for row in self.db.conn.execute("SELECT qualification_status FROM jobs").fetchall()
        ]
        self.assertIn("qualified_review", statuses)
        self.assertIn("reject", statuses)
        metrics = self.db.get_daily_metrics()
        self.assertEqual(metrics["jobs_qualified_review"], 1)
        self.assertEqual(metrics["jobs_rejected"], 1)


if __name__ == "__main__":
    unittest.main()
