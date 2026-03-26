"""Tests for company discovery and promoted-company merging."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_daily import merge_promoted_companies
from src.company_discoverer import ATSInfo, RawCompany, detect_ats, discover_from_yc, run
from src.config import Config, SenderConfig, Watchlist, WatchlistCompany
from src.db import Database


class TestCompanyDiscoverer(unittest.TestCase):
    def setUp(self):
        self.config = Config(sender=SenderConfig(name="Tester", email="tester@example.com"))
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()

    def tearDown(self):
        self.db.close()

    def test_initialize_adds_company_source_and_workday_columns(self):
        columns = self.db.conn.execute("PRAGMA table_info(companies)").fetchall()
        names = {column["name"] for column in columns}
        self.assertIn("source", names)
        self.assertIn("workday_instance", names)
        self.assertIn("workday_board", names)

        tables = self.db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        self.assertIn("discovered_companies", table_names)
        self.assertIn("scrape_log", table_names)

    @patch("src.company_discoverer._verify_ats", return_value=True)
    @patch("src.company_discoverer._get_response")
    def test_detect_ats_greenhouse_from_careers_link(self, mock_get_response, _mock_verify):
        response = Mock()
        response.url = "https://acme.com/careers"
        response.text = """
        <html><body>
            <a href="https://boards.greenhouse.io/acme">Jobs</a>
        </body></html>
        """
        mock_get_response.return_value = response

        info = detect_ats("acme.com")

        self.assertIsNotNone(info)
        self.assertEqual(info.ats_type, "greenhouse")
        self.assertEqual(info.slug, "acme")

    @patch("src.company_discoverer._get_response")
    def test_discover_from_yc_extracts_company_domains_from_profiles(self, mock_get_response):
        home = Mock()
        home.text = """
        <div id="app" data-page="{&quot;props&quot;:{&quot;jobs&quot;:[
            {&quot;companySlug&quot;:&quot;acme&quot;,&quot;companyName&quot;:&quot;Acme&quot;}
        ]}}"></div>
        """

        profile = Mock()
        profile.text = """
        <div id="app" data-page="{&quot;props&quot;:{&quot;company&quot;:{
            &quot;name&quot;:&quot;Acme&quot;,
            &quot;url&quot;:&quot;https://www.acme.com&quot;,
            &quot;description&quot;:&quot;Developer tools for teams&quot;,
            &quot;industry&quot;:&quot;B2B -&gt; SaaS&quot;,
            &quot;hiringDescriptionHtml&quot;:&quot;&lt;p&gt;Hiring engineers&lt;/p&gt;&quot;,
            &quot;techDescriptionHtml&quot;:&quot;&lt;p&gt;Python and React&lt;/p&gt;&quot;,
            &quot;jobs&quot;:[{&quot;title&quot;:&quot;Software Engineer&quot;}]
        }}}"></div>
        """
        mock_get_response.side_effect = [home, profile]

        companies = discover_from_yc(self.config.domain_profile)

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0].domain, "acme.com")
        self.assertEqual(companies[0].source, "yc")

    @patch("src.company_discoverer.detect_ats")
    @patch("src.company_discoverer._validate_company_domain", return_value=True)
    @patch("src.company_discoverer.discover_from_yc")
    def test_run_promotes_workday_company_with_metadata(self, mock_discover_from_yc, _mock_validate, mock_detect_ats):
        mock_discover_from_yc.return_value = [
            RawCompany(
                name="Acme",
                domain="acme.com",
                source="yc",
                source_url="https://www.workatastartup.com/companies/acme",
            )
        ]
        mock_detect_ats.return_value = ATSInfo(
            ats_type="workday",
            slug="acme",
            careers_url="https://acme.com/careers",
            jobs_url="https://acme.wd1.myworkdayjobs.com/en-US/Careers",
            workday_instance="wd1",
            workday_board="Careers",
        )

        summary = run(self.config, self.db, sources=["yc"], dry_run=False)

        self.assertEqual(summary.inserted, 1)
        self.assertEqual(summary.detected, 1)
        self.assertEqual(summary.promoted, 1)

        company = self.db.get_company_by_domain("acme.com")
        self.assertIsNotNone(company)
        self.assertEqual(company["source"], "auto_discovered")
        self.assertEqual(company["workday_instance"], "wd1")
        self.assertEqual(company["workday_board"], "Careers")

    def test_merge_promoted_companies_adds_db_backed_entries_to_watchlist(self):
        self.db.upsert_company(
            name="Acme",
            domain="acme.com",
            priority=3,
            ats="greenhouse",
            slug="acme",
            source="auto_discovered",
        )

        watchlist = Watchlist(companies=[
            WatchlistCompany(name="Manual Co", domain="manual.com", priority=1)
        ])

        merged = merge_promoted_companies(watchlist, self.db)

        self.assertEqual(merged, 1)
        domains = {company.domain for company in watchlist.companies}
        self.assertIn("manual.com", domains)
        self.assertIn("acme.com", domains)


if __name__ == "__main__":
    unittest.main()
