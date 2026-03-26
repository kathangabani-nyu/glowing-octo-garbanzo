"""Tests for review_cli helpers."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.review_cli import apply_decision
from src.review_queue import insert_for_review


class TestReviewCli(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()

        self.company_id = self.db.upsert_company(
            name="TestCo", domain="testco.com", priority=3
        )
        self.job_id = self.db.insert_job(
            company_id=self.company_id,
            external_job_id="j1",
            title="ML Engineer",
            url="https://testco.com/jobs/1",
        )
        self.person_id = self.db.insert_person(
            company_id=self.company_id,
            name="Jane Smith",
            email="jane@testco.com",
            confidence_tier="pattern_inferred",
        )
        self.message_id = self.db.insert_message(
            job_id=self.job_id,
            person_id=self.person_id,
            company_id=self.company_id,
            subject="Test",
            body="Test body",
            review_required=True,
        )

    def tearDown(self):
        self.db.close()

    def _enqueue(self):
        return insert_for_review(
            self.db,
            job_id=self.job_id,
            person_id=self.person_id,
            message_id=self.message_id,
            queue_reason="pattern_inferred",
            confidence_tier="pattern_inferred",
        )

    def test_apply_decision_approve(self):
        rid = self._enqueue()
        apply_decision(self.db, rid, "approve")
        ready = self.db.get_ready_messages()
        self.assertEqual(len(ready), 1)

    def test_apply_decision_skip(self):
        rid = self._enqueue()
        apply_decision(self.db, rid, "skip", notes="manual skip")
        pending = self.db.get_pending_reviews()
        self.assertEqual(len(pending), 0)

    def test_apply_decision_suppress_company(self):
        rid = self._enqueue()
        apply_decision(self.db, rid, "suppress", suppress_type="company")
        self.assertTrue(self.db.check_suppression(domain="testco.com"))

    def test_apply_decision_rejects_invalid_action(self):
        rid = self._enqueue()
        with self.assertRaises(ValueError):
            apply_decision(self.db, rid, "archive")


if __name__ == "__main__":
    unittest.main()
