"""Tests for email_assembler module. Uses in-memory SQLite."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.config import Config, CooldownConfig, DomainProfile, JobTarget, MessageQualityConfig, SenderConfig, LLMConfig
from src.email_assembler import (
    _classify_role_bucket,
    _compute_quality_score,
    _should_attach_resume,
    _determine_review_reason,
    _extract_details,
    _render_email,
    run,
)


class TestRoleBucketClassification(unittest.TestCase):
    def setUp(self):
        self.domain_profile = DomainProfile()

    def test_ml_roles(self):
        self.assertEqual(_classify_role_bucket("Machine Learning Engineer", self.domain_profile), "ml")
        self.assertEqual(_classify_role_bucket("Senior ML Engineer", self.domain_profile), "ml")
        self.assertEqual(_classify_role_bucket("AI Engineer", self.domain_profile), "ml")

    def test_research_roles(self):
        self.assertEqual(_classify_role_bucket("Research Scientist", self.domain_profile), "research")
        self.assertEqual(_classify_role_bucket("Applied Scientist", self.domain_profile), "research")

    def test_software_roles(self):
        self.assertEqual(_classify_role_bucket("Software Engineer", self.domain_profile), "software")
        self.assertEqual(_classify_role_bucket("Backend Engineer", self.domain_profile), "software")

    def test_fullstack_roles(self):
        self.assertEqual(_classify_role_bucket("Full Stack Developer", self.domain_profile), "fullstack")
        self.assertEqual(_classify_role_bucket("Frontend Engineer", self.domain_profile), "fullstack")

    def test_default_fallback(self):
        self.assertEqual(_classify_role_bucket("Product Manager", self.domain_profile), "software")


class TestQualityScore(unittest.TestCase):
    def test_high_quality(self):
        score = _compute_quality_score(
            job_score=90, confidence_tier="public_exact",
            has_personalization=True, is_named_recipient=True
        )
        self.assertGreaterEqual(score, 80)

    def test_low_quality(self):
        score = _compute_quality_score(
            job_score=30, confidence_tier="catch_all_guess",
            has_personalization=False, is_named_recipient=False
        )
        self.assertLess(score, 30)

    def test_score_range(self):
        score = _compute_quality_score(
            job_score=100, confidence_tier="public_exact",
            has_personalization=True, is_named_recipient=True
        )
        self.assertLessEqual(score, 100)


class TestResumeAttachment(unittest.TestCase):
    def test_public_exact_attaches(self):
        self.assertTrue(_should_attach_resume("public_exact"))

    def test_generic_inbox_attaches(self):
        self.assertTrue(_should_attach_resume("public_generic_inbox"))

    def test_pattern_verified_attaches_initial(self):
        self.assertTrue(_should_attach_resume("pattern_verified", "initial"))

    def test_pattern_verified_attaches_followup(self):
        self.assertTrue(_should_attach_resume("pattern_verified", "followup"))

    def test_catch_all_attaches(self):
        self.assertTrue(_should_attach_resume("catch_all_guess"))


class TestReviewReason(unittest.TestCase):
    def test_pattern_inferred_needs_review(self):
        reason = _determine_review_reason("pattern_inferred", 80, 60, True)
        self.assertEqual(reason, "pattern_inferred")

    def test_catch_all_needs_review(self):
        reason = _determine_review_reason("catch_all_guess", 80, 60, True)
        self.assertEqual(reason, "catch_all_guess")

    def test_low_score_no_personalization(self):
        reason = _determine_review_reason("public_exact", 40, 60, False)
        self.assertEqual(reason, "weak_personalization")

    def test_high_quality_no_review(self):
        reason = _determine_review_reason("public_exact", 80, 60, True)
        self.assertIsNone(reason)


class TestDetailExtraction(unittest.TestCase):
    def test_extracts_team_name(self):
        text = "Join our Data Platform team to build amazing things"
        details = _extract_details(text, ["python", "sql"])
        self.assertEqual(details["team_or_product"], "Data Platform")

    def test_extracts_skill(self):
        text = "We're looking for someone with experience in Python and TensorFlow"
        details = _extract_details(text, ["python", "tensorflow", "sql"])
        self.assertEqual(details["key_technology"], "python")

    def test_empty_text(self):
        details = _extract_details("", [])
        self.assertIsNone(details["team_or_product"])


class TestRenderEmail(unittest.TestCase):
    def test_fallback_template(self):
        context = {
            "sender_name": "Test User",
            "sender_email": "test@example.com",
            "contact_name": "Jane Smith",
            "company_name": "Acme Corp",
            "job_title": "ML Engineer",
            "team_or_product": "AI Platform",
            "key_technology": "Python",
        }
        subject, body = _render_email(None, "ml", context, "software")
        self.assertIn("ML Engineer", subject)
        self.assertIn("Acme Corp", body)
        self.assertIn("AI Platform", body)
        self.assertIn("Python", body)
        self.assertIn("Hi Jane,", body)


class TestAssemblerRunWithDB(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()

        # Create a company, qualified job, and contact
        self.company_id = self.db.upsert_company(
            name="TestCo", domain="testco.com", priority=3
        )
        self.job_id = self.db.insert_job(
            company_id=self.company_id,
            external_job_id="test-123",
            title="Machine Learning Engineer",
            url="https://testco.com/jobs/123",
            posting_text="Join our ML Platform team. Python and PyTorch required.",
        )
        self.db.update_job_score(self.job_id, "qualified_auto", 85, "good fit")

        self.person_id = self.db.insert_person(
            company_id=self.company_id,
            name="Jane Smith",
            email="jane.smith@testco.com",
            role="Technical Recruiter",
            confidence_tier="public_exact",
            contact_source_type="team_page",
        )

        self.config = Config(
            sender=SenderConfig(name="Test User", email="test@gmail.com"),
            job_targets=JobTarget(skills=["python", "pytorch"]),
            message_quality=MessageQualityConfig(auto_send_threshold=60),
            cooldowns=CooldownConfig(person_days=90, company_job_family_days=30),
            resume_path="resumes/ml_resume.pdf",
            resume_variants={"ml": "resumes/ml_resume.pdf"},
        )

    def tearDown(self):
        self.db.close()

    def test_assembles_message(self):
        count = run(self.config, self.db)
        self.assertEqual(count, 1)

        # Check message was created
        messages = self.db.get_ready_messages()
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertIn("Machine Learning Engineer", msg["subject"])
        self.assertEqual(msg["contact_email"], "jane.smith@testco.com")

    def test_skips_already_contacted(self):
        # First run creates message
        run(self.config, self.db)
        # Mark as sent
        messages = self.db.get_ready_messages()
        self.db.update_message_status(messages[0]["id"], "sent")
        # Second run should skip (exact posting contacted)
        count = run(self.config, self.db)
        self.assertEqual(count, 0)

    def test_skips_existing_unsent_message_on_rerun(self):
        first_count = run(self.config, self.db)
        self.assertEqual(first_count, 1)

        second_count = run(self.config, self.db)
        self.assertEqual(second_count, 0)

        messages = self.db.get_ready_messages()
        self.assertEqual(len(messages), 1)

    @patch("src.llm_extractor.validate_assembly_candidate")
    @patch("src.llm_extractor.check_ollama_available")
    @patch("src.email_assembler._extract_details")
    def test_llm_strict_reject_skips_message(self, mock_extract, mock_ollama, mock_gate):
        class Gate:
            role_fit = "reject"
            role_fit_confidence = 0.99
            contact_name_ok = True
            safe_greeting_name = "Jane"
            message_quality = "reject"
            message_confidence = 0.99
            reasons = ["off_target_role"]

        mock_ollama.return_value = True
        mock_gate.return_value = Gate()
        mock_extract.return_value = {
            "team_or_product": "ML Platform",
            "key_technology": "Python",
            "company_blurb": None,
        }
        self.config.llm = LLMConfig(
            use_local_llm=True,
            assembly_gate_enabled=True,
            assembly_gate_mode="strict",
            assembly_gate_min_confidence=0.75,
        )

        count = run(self.config, self.db)
        self.assertEqual(count, 0)
        self.assertEqual(len(self.db.get_ready_messages()), 0)

    @patch("src.llm_extractor.validate_assembly_candidate")
    @patch("src.llm_extractor.check_ollama_available")
    @patch("src.email_assembler._extract_details")
    def test_llm_contact_name_fallback_avoids_unsafe_greeting(self, mock_extract, mock_ollama, mock_gate):
        # Replace contact with suspicious placeholder-style name.
        self.db.conn.execute("DELETE FROM people WHERE id = ?", (self.person_id,))
        self.person_id = self.db.insert_person(
            company_id=self.company_id,
            name="Why English",
            email="jane.smith@testco.com",
            role="Recruiter",
            confidence_tier="pattern_verified",
            contact_source_type="team_page",
        )
        self.db.conn.commit()

        class Gate:
            role_fit = "allow"
            role_fit_confidence = 0.95
            contact_name_ok = False
            safe_greeting_name = "there"
            message_quality = "allow"
            message_confidence = 0.95
            reasons = ["unsafe_contact_name"]

        mock_ollama.return_value = True
        mock_gate.return_value = Gate()
        mock_extract.return_value = {
            "team_or_product": "ML Platform",
            "key_technology": "Python",
            "company_blurb": None,
        }
        self.config.llm = LLMConfig(
            use_local_llm=True,
            assembly_gate_enabled=True,
            assembly_gate_mode="strict",
            assembly_gate_min_confidence=0.75,
        )

        count = run(self.config, self.db)
        self.assertEqual(count, 1)
        row = self.db.conn.execute(
            "SELECT status, body FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["status"], "pending_review")
        self.assertIn("Hi TestCo team,", row["body"])
        self.assertNotIn("Hi Why,", row["body"])


if __name__ == "__main__":
    unittest.main()
