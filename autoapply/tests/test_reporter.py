"""Tests for reporter export helpers."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.reporter import build_snapshot, render_markdown_report, snapshot_to_dict, write_report_files


class TestReporter(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()
        self.today = date.today().isoformat()
        self.db.increment_metric("jobs_discovered", amount=5, metric_date=self.today)
        self.db.increment_metric("emails_sent", amount=2, metric_date=self.today)
        self.db.increment_metric("replies_received", amount=1, metric_date=self.today)

    def tearDown(self):
        self.db.close()

    def test_snapshot_to_dict_has_expected_shape(self):
        snapshot = build_snapshot(self.db, metric_date=self.today)
        payload = snapshot_to_dict(snapshot)
        self.assertIn("metric_date", payload)
        self.assertIn("trend_7d", payload)
        self.assertIn("top_responding_companies_30d", payload)

    def test_render_markdown_report_contains_sections(self):
        snapshot = build_snapshot(self.db, metric_date=self.today)
        markdown = render_markdown_report(snapshot)
        self.assertIn("# AutoApply Report", markdown)
        self.assertIn("## Today", markdown)
        self.assertIn("## Trends", markdown)

    def test_write_report_files_writes_txt_md_json(self):
        snapshot = build_snapshot(self.db, metric_date=self.today)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_report_files(snapshot, temp_dir)
            self.assertTrue(os.path.exists(paths["txt"]))
            self.assertTrue(os.path.exists(paths["md"]))
            self.assertTrue(os.path.exists(paths["json"]))
            with open(paths["json"], "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["metric_date"], self.today)


if __name__ == "__main__":
    unittest.main()
"""Tests for reporter module."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.reporter import build_snapshot, render_report


class TestReporter(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()

    def tearDown(self):
        self.db.close()

    def _create_sent_message(self, company_name: str, domain: str, external_job_id: str, status: str, sent_at: str):
        company_id = self.db.upsert_company(name=company_name, domain=domain, priority=2)
        job_id = self.db.insert_job(
            company_id=company_id,
            external_job_id=external_job_id,
            title="Software Engineer",
            url=f"https://{domain}/jobs/{external_job_id}",
            location="Remote",
            posting_text="Python role",
            job_family="software",
            source="greenhouse_api",
        )
        person_id = self.db.insert_person(
            company_id=company_id,
            name="Jane Smith",
            email=f"jane@{domain}",
            role="Recruiter",
            confidence_tier="public_exact",
            contact_source_type="team_page",
        )
        message_id = self.db.insert_message(
            job_id=job_id,
            person_id=person_id,
            company_id=company_id,
            template_used="software.j2",
            resume_variant="resumes/swe.pdf",
            subject="Hello",
            body="Body",
            review_required=False,
        )
        self.db.conn.execute(
            "UPDATE messages SET status = ?, sent_at = ? WHERE id = ?",
            (status, sent_at, message_id),
        )
        self.db.conn.commit()
        return message_id

    def test_build_snapshot_with_metrics_queue_and_trends(self):
        company_id = self.db.upsert_company(name="Acme", domain="acme.com", priority=2)
        job_id = self.db.insert_job(
            company_id=company_id,
            external_job_id="job-1",
            title="Software Engineer",
            url="https://acme.com/jobs/1",
            location="Remote",
            posting_text="Python role",
            job_family="software",
            source="greenhouse_api",
        )
        person_id = self.db.insert_person(
            company_id=company_id,
            name="Jane Smith",
            email="jane@acme.com",
            role="Recruiter",
            confidence_tier="public_exact",
            contact_source_type="team_page",
        )
        message_id = self.db.insert_message(
            job_id=job_id,
            person_id=person_id,
            company_id=company_id,
            template_used="software.j2",
            resume_variant="resumes/swe.pdf",
            subject="Hello",
            body="Body",
            review_required=False,
        )
        self.db.insert_review_item(
            job_id=job_id,
            person_id=person_id,
            message_id=message_id,
            queue_reason="weak_personalization",
            confidence_tier="public_exact",
        )

        self.db.increment_metric("jobs_discovered", 2, metric_date="2026-03-24")
        self.db.increment_metric("emails_sent", 1, metric_date="2026-03-24")
        self.db.increment_metric("replies_received", 1, metric_date="2026-03-22")
        self.db.increment_metric("emails_sent", 2, metric_date="2026-03-22")

        snapshot = build_snapshot(self.db, metric_date="2026-03-24")

        self.assertEqual(snapshot.jobs_discovered, 2)
        self.assertEqual(snapshot.emails_sent, 1)
        self.assertEqual(snapshot.pending_reviews, 1)
        self.assertEqual(snapshot.ready_messages, 1)
        self.assertEqual(snapshot.trend_7d.emails_sent, 3)
        self.assertEqual(snapshot.trend_7d.replies_received, 1)
        self.assertAlmostEqual(snapshot.trend_7d.reply_rate, 1 / 3)

    def test_top_responding_companies_uses_message_history(self):
        self._create_sent_message("Acme", "acme.com", "job-1", "replied_positive", "2026-03-24 09:00:00")
        self._create_sent_message("Acme", "acme.com", "job-2", "replied_referral", "2026-03-23 09:00:00")
        self._create_sent_message("Beta", "beta.com", "job-3", "replied_positive", "2026-03-22 09:00:00")

        snapshot = build_snapshot(self.db, metric_date="2026-03-24")

        self.assertGreaterEqual(len(snapshot.top_responding_companies_30d), 2)
        self.assertEqual(snapshot.top_responding_companies_30d[0].company_name, "Acme")
        self.assertEqual(snapshot.top_responding_companies_30d[0].replies, 2)

    def test_render_report_contains_trend_sections(self):
        snapshot = build_snapshot(self.db, metric_date="2026-03-24")
        report = render_report(snapshot)
        self.assertIn("Trends:", report)
        self.assertIn("7d", report)
        self.assertIn("30d", report)
        self.assertIn("top responding companies", report)
        self.assertIn("Safety:", report)


if __name__ == "__main__":
    unittest.main()
