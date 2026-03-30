"""Focused tests for DB contact quality heuristics."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database


class TestContactEmailBlocking(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.db.initialize()
        self.cid = self.db.upsert_company(
            name="Acme", domain="acme.com", priority=1, ats="greenhouse", slug="acme"
        )
        self.j1 = self.db.insert_job(
            company_id=self.cid,
            external_job_id="e1",
            title="Software Engineer",
            url="https://acme.com/j1",
            job_family="software",
            source="test",
        )
        self.j2 = self.db.insert_job(
            company_id=self.cid,
            external_job_id="e2",
            title="ML Engineer",
            url="https://acme.com/j2",
            job_family="ml",
            source="test",
        )
        self.db.update_job_score(self.j1, "qualified_auto", 80, "ok", "keyword")
        self.db.update_job_score(self.j2, "qualified_auto", 80, "ok", "keyword")
        self.p1 = self.db.insert_person(
            company_id=self.cid,
            job_id=self.j1,
            name="Liz Cardenas",
            email="liz@acme.com",
            confidence_tier="pattern_verified",
            contact_source_type="test",
        )
        self.p2 = self.db.insert_person(
            company_id=self.cid,
            job_id=self.j2,
            name="Liz Cardenas",
            email="liz@acme.com",
            confidence_tier="pattern_verified",
            contact_source_type="test",
        )

    def tearDown(self):
        self.db.close()

    def test_blocking_when_another_person_row_has_initial(self):
        self.db.insert_message(
            job_id=self.j1,
            person_id=self.p1,
            company_id=self.cid,
            subject="Hi",
            body="Body",
            review_required=False,
        )
        self.assertTrue(self.db.check_contact_email_has_blocking_initial("liz@acme.com"))
        self.assertTrue(self.db.check_contact_email_has_blocking_initial("Liz@acme.com"))

    def test_no_block_when_only_skipped(self):
        mid = self.db.insert_message(
            job_id=self.j1,
            person_id=self.p1,
            company_id=self.cid,
            subject="Hi",
            body="Body",
            review_required=False,
        )
        self.db.update_message_status(mid, "skipped")
        self.assertFalse(self.db.check_contact_email_has_blocking_initial("liz@acme.com"))


class TestDbContactHeuristics(unittest.TestCase):
    def test_rejects_placeholder_first_last_local_part(self):
        contact = {
            "email": "why.english@example.com",
            "name": "Why English",
        }
        self.assertFalse(Database._is_real_person_contact(contact))

    def test_accepts_normal_first_last_contact(self):
        contact = {
            "email": "jane.smith@example.com",
            "name": "Jane Smith",
        }
        self.assertTrue(Database._is_real_person_contact(contact))


if __name__ == "__main__":
    unittest.main()
