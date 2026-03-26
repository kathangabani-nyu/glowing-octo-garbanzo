"""Tests for review_queue module. Uses in-memory SQLite."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.review_queue import (
    insert_for_review, get_pending_items, approve_item,
    skip_item, suppress_item, get_approval_rate, get_queue_stats,
)


class TestReviewQueue(unittest.TestCase):
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
            job_id=self.job_id, person_id=self.person_id,
            company_id=self.company_id,
            subject="Test", body="Test body",
            review_required=True,
        )

    def tearDown(self):
        self.db.close()

    def test_insert_and_fetch(self):
        review_id = insert_for_review(
            self.db, job_id=self.job_id, person_id=self.person_id,
            message_id=self.message_id, queue_reason="pattern_inferred",
            confidence_tier="pattern_inferred",
        )
        items = get_pending_items(self.db)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].queue_reason, "pattern_inferred")
        self.assertEqual(items[0].contact_email, "jane@testco.com")

    def test_approve_moves_message_to_ready(self):
        review_id = insert_for_review(
            self.db, job_id=self.job_id, person_id=self.person_id,
            message_id=self.message_id, queue_reason="pattern_inferred",
        )
        approve_item(self.db, review_id)
        items = get_pending_items(self.db)
        self.assertEqual(len(items), 0)

        # Message should now be ready
        messages = self.db.get_ready_messages()
        self.assertEqual(len(messages), 1)

    def test_skip_marks_message_skipped(self):
        review_id = insert_for_review(
            self.db, job_id=self.job_id, person_id=self.person_id,
            message_id=self.message_id, queue_reason="borderline_fit",
        )
        skip_item(self.db, review_id, notes="Not a good fit")
        items = get_pending_items(self.db)
        self.assertEqual(len(items), 0)

    def test_suppress_adds_to_suppression_list(self):
        review_id = insert_for_review(
            self.db, job_id=self.job_id, person_id=self.person_id,
            message_id=self.message_id, queue_reason="catch_all_guess",
        )
        suppress_item(self.db, review_id, suppress_type="email")
        self.assertTrue(self.db.check_suppression(email="jane@testco.com"))

    def test_approval_rate(self):
        # Create and approve 3, skip 1
        for i in range(3):
            rid = insert_for_review(
                self.db, job_id=self.job_id, person_id=self.person_id,
                queue_reason="test",
            )
            approve_item(self.db, rid)
        rid = insert_for_review(
            self.db, job_id=self.job_id, person_id=self.person_id,
            queue_reason="test",
        )
        skip_item(self.db, rid)

        rate = get_approval_rate(self.db, last_n=10)
        self.assertAlmostEqual(rate, 0.75)

    def test_queue_stats(self):
        insert_for_review(
            self.db, job_id=self.job_id, person_id=self.person_id,
            queue_reason="test",
        )
        stats = get_queue_stats(self.db)
        self.assertEqual(stats.get("pending", 0), 1)


if __name__ == "__main__":
    unittest.main()
