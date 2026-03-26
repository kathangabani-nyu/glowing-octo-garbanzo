"""Tests for contact_discoverer module. Uses in-memory SQLite."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.contact_discoverer import (
    _extract_emails_from_html,
    _extract_names_from_html,
    _extract_people_from_team_page,
    _is_recruiting_role,
    _split_name,
    resolve_contact_for_company,
    ContactCandidate,
)
from src.smtp_verifier import VerificationResult


class TestEmailExtraction(unittest.TestCase):
    def test_extracts_company_emails(self):
        html = """
        <p>Contact us at hiring@acme.com or info@acme.com</p>
        <p>Also reach out to external@gmail.com</p>
        """
        result = _extract_emails_from_html(html, "acme.com")
        self.assertEqual(result, ["hiring@acme.com", "info@acme.com"])

    def test_no_match(self):
        html = "<p>No emails here</p>"
        result = _extract_emails_from_html(html, "acme.com")
        self.assertEqual(result, [])

    def test_deduplicates(self):
        html = "<p>hiring@acme.com and HIRING@acme.com</p>"
        result = _extract_emails_from_html(html, "acme.com")
        self.assertEqual(len(result), 1)


class TestNameExtraction(unittest.TestCase):
    def test_posted_by(self):
        html = "<p>Posted by Jane Smith, Technical Recruiter</p>"
        result = _extract_names_from_html(html)
        self.assertTrue(any("Jane Smith" in name for name, _ in result))

    def test_recruiter_pattern(self):
        html = "<p>Recruiter: John Doe</p>"
        result = _extract_names_from_html(html)
        self.assertTrue(any("John Doe" in name for name, _ in result))

    def test_name_with_title(self):
        html = "<p>Sarah Johnson, Technical Recruiter at Acme</p>"
        result = _extract_names_from_html(html)
        self.assertTrue(any("Sarah Johnson" in name for name, _ in result))


class TestTeamPageExtraction(unittest.TestCase):
    def test_heading_pattern(self):
        html = """
        <div>
            <h3>Jane Smith</h3>
            <p>Head of Talent Acquisition</p>
            <h3>Bob Jones</h3>
            <p>Software Engineer</p>
        </div>
        """
        result = _extract_people_from_team_page(html)
        names = [name for name, role, _ in result]
        self.assertIn("Jane Smith", names)
        self.assertIn("Bob Jones", names)

    def test_member_class_pattern(self):
        html = """
        <div class="team-member">
            <h3>Alice Brown</h3>
            <span class="title">Recruiting Manager</span>
        </div>
        """
        result = _extract_people_from_team_page(html)
        self.assertTrue(any("Alice Brown" in name for name, _, _ in result))


class TestHelpers(unittest.TestCase):
    def test_is_recruiting_role(self):
        self.assertTrue(_is_recruiting_role("Technical Recruiter"))
        self.assertTrue(_is_recruiting_role("Head of Talent Acquisition"))
        self.assertTrue(_is_recruiting_role("HR Manager"))
        self.assertFalse(_is_recruiting_role("Software Engineer"))
        self.assertFalse(_is_recruiting_role("CEO"))

    def test_split_name(self):
        self.assertEqual(_split_name("Jane Smith"), ("Jane", "Smith"))
        self.assertEqual(_split_name("Mary Jane Watson"), ("Mary", "Watson"))
        self.assertEqual(_split_name("Bob"), ("Bob", ""))


class TestResolveContactWithDB(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()
        self.company_id = self.db.upsert_company(
            name="Acme Corp", domain="acme.com", priority=3
        )

    def tearDown(self):
        self.db.close()

    @patch("src.contact_discoverer._fetch_page")
    @patch("src.contact_discoverer.verify_email")
    @patch("src.contact_discoverer.check_catch_all")
    def test_finds_email_on_job_page(self, mock_catch_all, mock_verify, mock_fetch):
        mock_fetch.return_value = """
        <html><body>
            <p>Apply at careers@acme.com</p>
        </body></html>
        """
        mock_verify.return_value = VerificationResult(status="verified", mx_host="mx.acme.com", response_code=250)
        mock_catch_all.return_value = False

        result = resolve_contact_for_company(
            self.db, self.company_id, "acme.com",
            job_url="https://acme.com/jobs/123"
        )

        self.assertTrue(len(result) > 0)
        self.assertEqual(result[0].email, "careers@acme.com")

    @patch("src.contact_discoverer._fetch_page")
    @patch("src.contact_discoverer.verify_email")
    @patch("src.contact_discoverer.check_catch_all")
    def test_no_contacts_found(self, mock_catch_all, mock_verify, mock_fetch):
        mock_fetch.return_value = "<html><body><p>No info here</p></body></html>"
        mock_verify.return_value = VerificationResult(status="rejected", mx_host="mx.acme.com", response_code=550)
        mock_catch_all.return_value = False

        result = resolve_contact_for_company(
            self.db, self.company_id, "acme.com"
        )

        self.assertEqual(len(result), 0)

    @patch("src.contact_discoverer._fetch_page")
    @patch("src.contact_discoverer.verify_email")
    @patch("src.contact_discoverer.check_catch_all")
    def test_recruiter_name_with_smtp_verify(self, mock_catch_all, mock_verify, mock_fetch):
        # Job page has recruiter name but no email
        def fetch_side_effect(url, **kwargs):
            if "jobs" in url:
                return "<html><body><p>Recruiter: Jane Smith</p></body></html>"
            return None

        mock_fetch.side_effect = fetch_side_effect

        # SMTP verifies jane.smith@acme.com
        def verify_side_effect(email, **kwargs):
            if email == "jane.smith@acme.com":
                return VerificationResult(status="verified", mx_host="mx.acme.com", response_code=250)
            return VerificationResult(status="rejected", mx_host="mx.acme.com", response_code=550)

        mock_verify.side_effect = verify_side_effect
        mock_catch_all.return_value = False

        result = resolve_contact_for_company(
            self.db, self.company_id, "acme.com",
            job_url="https://acme.com/jobs/swe"
        )

        verified = [c for c in result if c.confidence_tier == "pattern_verified"]
        self.assertTrue(len(verified) > 0)
        self.assertEqual(verified[0].email, "jane.smith@acme.com")


if __name__ == "__main__":
    unittest.main()
