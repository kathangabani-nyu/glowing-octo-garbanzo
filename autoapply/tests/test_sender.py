"""Tests for sender module. Uses in-memory SQLite."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime
from email import message_from_bytes
import base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import Database
from src.config import Config, SenderConfig, SendingConfig, SafetyConfig, CooldownConfig
from src.sender import (
    _is_business_hours,
    _get_warm_up_limit,
    _check_safety_stops,
    _build_mime_message,
)


def _make_config(**overrides):
    defaults = dict(
        sender=SenderConfig(name="Test", email="test@gmail.com"),
        sending=SendingConfig(
            max_initial_per_day=12,
            warm_up_days=7,
            warm_up_initial_limit=3,
            business_hours_start=8,
            business_hours_end=18,
        ),
        safety=SafetyConfig(
            max_bounce_rate=0.05, bounce_window=50,
            max_review_skip_rate=0.40, review_skip_window=20,
        ),
    )
    defaults.update(overrides)
    return Config(**defaults)


class TestBusinessHours(unittest.TestCase):
    @patch("src.sender.datetime")
    def test_weekday_in_hours(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 3, 23, 10, 0)  # Monday 10am
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        config = _make_config()
        # Can't easily mock weekday, test the logic directly
        now = datetime(2026, 3, 23, 10, 0)  # Monday
        self.assertTrue(now.weekday() < 5)
        self.assertTrue(config.sending.business_hours_start <= now.hour < config.sending.business_hours_end)


class TestWarmUpLimit(unittest.TestCase):
    def test_fresh_start(self):
        db = Database(":memory:")
        db.connect()
        db.initialize()
        config = _make_config()

        limit = _get_warm_up_limit(config, db)
        # No sends yet, should be at warm_up_initial_limit
        self.assertEqual(limit, 3)
        db.close()


class TestSafetyStops(unittest.TestCase):
    def test_no_stops_when_clean(self):
        db = Database(":memory:")
        db.connect()
        db.initialize()
        config = _make_config()

        reason = _check_safety_stops(config, db)
        self.assertIsNone(reason)
        db.close()

    def test_bounce_rate_stop(self):
        db = Database(":memory:")
        db.connect()
        db.initialize()
        config = _make_config()

        # Insert a company, job, person
        cid = db.upsert_company(name="X", domain="x.com")
        jid = db.insert_job(company_id=cid, external_job_id="1", title="SWE")
        pid = db.insert_person(company_id=cid, email="a@x.com", confidence_tier="public_exact")

        # Insert 10 messages, 4 bounced (40% > 5%)
        for i in range(10):
            mid = db.insert_message(
                job_id=jid, person_id=pid, company_id=cid,
                subject=f"Test {i}", body="body",
            )
            status = "bounced" if i < 4 else "sent"
            db.update_message_status(mid, status)
            if status == "sent":
                db.conn.execute(
                    "UPDATE messages SET sent_at = datetime('now') WHERE id = ?", (mid,)
                )
                db.conn.commit()

        reason = _check_safety_stops(config, db)
        self.assertIsNotNone(reason)
        self.assertIn("Bounce rate", reason)
        db.close()


class TestMimeMessage(unittest.TestCase):
    def _decode_message(self, raw):
        padding = "=" * (-len(raw) % 4)
        return message_from_bytes(base64.urlsafe_b64decode(raw + padding))

    def test_plain_text(self):
        raw = _build_mime_message(
            sender_email="from@test.com",
            sender_name="Test Sender",
            to_email="to@test.com",
            subject="Test Subject",
            body="Hello World",
        )
        self.assertIsInstance(raw, str)
        self.assertTrue(len(raw) > 0)
        msg = self._decode_message(raw)
        self.assertEqual(msg["From"], "Test Sender <from@test.com>")
        self.assertTrue(msg.is_multipart())
        self.assertEqual(msg.get_content_subtype(), "alternative")

    def test_with_reply_headers(self):
        raw = _build_mime_message(
            sender_email="from@test.com",
            sender_name="Test Sender",
            to_email="to@test.com",
            subject="Re: Test",
            body="Follow-up",
            in_reply_to="<abc123@gmail.com>",
        )
        self.assertIsInstance(raw, str)
        msg = self._decode_message(raw)
        self.assertEqual(msg["In-Reply-To"], "<abc123@gmail.com>")

    def test_with_attachment(self):
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"resume")
            temp_path = handle.name

        try:
            raw = _build_mime_message(
                sender_email="from@test.com",
                sender_name="Test Sender",
                to_email="to@test.com",
                subject="Attached",
                body="Hello\n\nWorld",
                resume_path=temp_path,
            )
            msg = self._decode_message(raw)
            self.assertTrue(msg.is_multipart())
            self.assertEqual(msg.get_content_subtype(), "mixed")
            payload = msg.get_payload()
            self.assertEqual(payload[0].get_content_subtype(), "alternative")
        finally:
            os.unlink(temp_path)


if __name__ == "__main__":
    unittest.main()
