"""Tests for job_discoverer module."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import Config, SenderConfig, Watchlist, WatchlistCompany
from src.db import Database
from src.job_discoverer import (
    _fetch_smartrecruiters_jobs,
    _fetch_workday_jobs,
    _parse_greenhouse_jobs,
    _parse_html_jobs,
    run,
)


class TestJobParsing(unittest.TestCase):
    def setUp(self):
        self.config = Config(sender=SenderConfig(name="Tester", email="tester@example.com"))

    def test_parse_greenhouse_jobs(self):
        company = WatchlistCompany(name="Acme", domain="acme.com", ats="greenhouse", slug="acme")
        data = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Machine Learning Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                    "location": {"name": "New York, NY"},
                    "content": "<p>Build ML systems with Python and PyTorch.</p>",
                }
            ]
        }
        jobs = _parse_greenhouse_jobs(data, company, self.config.domain_profile)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_job_id, "123")
        self.assertEqual(jobs[0].job_family, "ml")
        self.assertIn("PyTorch", jobs[0].posting_text)

    def test_parse_html_jobs(self):
        company = WatchlistCompany(name="Acme", domain="acme.com", ats="careers_page")
        html = """
        <html><body>
            <a href="/jobs/ml-engineer">Machine Learning Engineer</a>
            <a href="/about">About us</a>
        </body></html>
        """
        jobs = _parse_html_jobs(
            html,
            company,
            "https://acme.com/careers",
            self.config.domain_profile,
            self.config.domain_profile.discovery_keywords,
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].url, "https://acme.com/jobs/ml-engineer")

    @patch("src.job_discoverer._get_json")
    @patch("src.job_discoverer._post_json")
    def test_fetch_workday_jobs(self, mock_post_json, mock_get_json):
        company = WatchlistCompany(
            name="GS",
            domain="goldmansachs.com",
            ats="workday",
            slug="gs",
            workday_instance="wd1",
            workday_board="GS_Careers",
        )
        mock_post_json.return_value = {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Investment Banking Analyst",
                    "externalPath": "/job/new-york/investment-banking-analyst_R1",
                    "id": "R1",
                    "locationsText": "New York, NY",
                }
            ],
        }
        mock_get_json.return_value = {
            "jobPostingInfo": {
                "jobDescription": "<p>Support M&A and financial modeling work.</p>"
            }
        }

        jobs = _fetch_workday_jobs(company, self.config.domain_profile)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_family, self.config.domain_profile.default_bucket)
        self.assertIn("financial modeling", jobs[0].posting_text.lower())

    @patch("src.job_discoverer._get_json")
    def test_fetch_smartrecruiters_jobs(self, mock_get_json):
        company = WatchlistCompany(
            name="Evercore",
            domain="evercore.com",
            ats="smartrecruiters",
            slug="evercore",
        )
        mock_get_json.side_effect = [
            {
                "content": [
                    {
                        "id": "abc123",
                        "name": "Research Analyst",
                        "ref": "https://jobs.smartrecruiters.com/evercore/abc123",
                        "location": {"city": "New York", "country": "United States"},
                    }
                ]
            },
            {
                "sections": {
                    "jobDescription": {"text": "<p>Equity research and industry analysis.</p>"}
                }
            },
        ]

        jobs = _fetch_smartrecruiters_jobs(company, self.config.domain_profile)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_job_id, "abc123")
        self.assertIn("equity research", jobs[0].posting_text.lower())


class TestJobDiscovererRun(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()
        self.config = Config(sender=SenderConfig(name="Tester", email="tester@example.com"))

    def tearDown(self):
        self.db.close()

    @patch("src.job_discoverer.discover_company_jobs")
    def test_run_inserts_new_jobs_and_updates_metrics(self, mock_discover):
        company = WatchlistCompany(name="Acme", domain="acme.com", ats="greenhouse", slug="acme", priority=2)
        watchlist = Watchlist(companies=[company])
        mock_discover.return_value = [
            type("Job", (), {
                "external_job_id": "job-1",
                "title": "Software Engineer",
                "url": "https://acme.com/jobs/1",
                "location": "Remote",
                "posting_text": "Python backend role",
                "job_family": "software",
                "source": "greenhouse_api",
            })()
        ]

        inserted = run(watchlist, self.db, self.config)

        self.assertEqual(inserted, 1)
        jobs = self.db.get_unscored_jobs()
        self.assertEqual(len(jobs), 1)
        metrics = self.db.get_daily_metrics()
        self.assertEqual(metrics["jobs_discovered"], 1)

    @patch("src.job_discoverer.discover_company_jobs")
    def test_run_dry_run_still_inserts_jobs_without_metrics(self, mock_discover):
        company = WatchlistCompany(name="Acme", domain="acme.com", ats="greenhouse", slug="acme", priority=2)
        watchlist = Watchlist(companies=[company])
        mock_discover.return_value = [
            type("Job", (), {
                "external_job_id": "job-1",
                "title": "Software Engineer",
                "url": "https://acme.com/jobs/1",
                "location": "Remote",
                "posting_text": "Python backend role",
                "job_family": "software",
                "source": "greenhouse_api",
            })()
        ]

        inserted = run(watchlist, self.db, self.config, dry_run=True)

        self.assertEqual(inserted, 1)
        self.assertEqual(len(self.db.get_unscored_jobs()), 1)
        metrics = self.db.get_daily_metrics()
        self.assertTrue(
            metrics is None or (metrics["jobs_discovered"] or 0) == 0,
            "dry-run should not bump jobs_discovered metric",
        )

    @patch("src.job_discoverer.discover_company_jobs")
    def test_run_marks_missing_jobs_closed(self, mock_discover):
        company = WatchlistCompany(name="Acme", domain="acme.com", ats="greenhouse", slug="acme", priority=2)
        watchlist = Watchlist(companies=[company])
        company_id = self.db.upsert_company(name="Acme", domain="acme.com", priority=2, ats="greenhouse", slug="acme")
        self.db.insert_job(
            company_id=company_id,
            external_job_id="old-job",
            title="Old Role",
            url="https://acme.com/jobs/old",
            location="Remote",
            posting_text="Old posting",
            job_family="software",
            source="greenhouse_api",
        )

        mock_discover.return_value = []
        run(watchlist, self.db, self.config)

        row = self.db.conn.execute(
            "SELECT closed_at FROM jobs WHERE external_job_id = 'old-job'"
        ).fetchone()
        self.assertIsNotNone(row["closed_at"])


if __name__ == "__main__":
    unittest.main()
