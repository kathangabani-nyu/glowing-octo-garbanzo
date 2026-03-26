"""Tests for smtp_verifier module."""

import os
import socket
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import smtp_verifier
from src.smtp_verifier import VerificationResult, check_catch_all, verify_email


class TestSmtpVerifier(unittest.TestCase):
    def setUp(self):
        smtp_verifier._mx_cache.clear()
        smtp_verifier._last_connect.clear()

    @patch("src.smtp_verifier.time.sleep")
    @patch("src.smtp_verifier.smtplib.SMTP")
    @patch("src.smtp_verifier.dns.resolver.resolve")
    def test_verify_email_success(self, mock_resolve, mock_smtp_cls, _mock_sleep):
        mx_record = Mock()
        mx_record.preference = 10
        mx_record.exchange = "mx.acme.com."
        mock_resolve.return_value = [mx_record]

        smtp = Mock()
        smtp.connect.return_value = (220, b"ready")
        smtp.helo.return_value = (250, b"ok")
        smtp.mail.return_value = (250, b"ok")
        smtp.rcpt.return_value = (250, b"accepted")
        mock_smtp_cls.return_value = smtp

        result = verify_email("jane@acme.com", timeout=5)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.mx_host, "mx.acme.com")

    @patch("src.smtp_verifier.time.sleep")
    @patch("src.smtp_verifier.smtplib.SMTP")
    @patch("src.smtp_verifier.dns.resolver.resolve")
    def test_verify_email_rejected(self, mock_resolve, mock_smtp_cls, _mock_sleep):
        mx_record = Mock()
        mx_record.preference = 10
        mx_record.exchange = "mx.acme.com."
        mock_resolve.return_value = [mx_record]

        smtp = Mock()
        smtp.connect.return_value = (220, b"ready")
        smtp.helo.return_value = (250, b"ok")
        smtp.mail.return_value = (250, b"ok")
        smtp.rcpt.return_value = (550, b"no such user")
        mock_smtp_cls.return_value = smtp

        result = verify_email("jane@acme.com")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.response_code, 550)

    @patch("src.smtp_verifier.time.sleep")
    @patch("src.smtp_verifier.smtplib.SMTP")
    @patch("src.smtp_verifier.dns.resolver.resolve")
    def test_verify_email_timeout(self, mock_resolve, mock_smtp_cls, _mock_sleep):
        mx_record = Mock()
        mx_record.preference = 10
        mx_record.exchange = "mx.acme.com."
        mock_resolve.return_value = [mx_record]

        smtp = Mock()
        smtp.connect.side_effect = socket.timeout()
        mock_smtp_cls.return_value = smtp

        result = verify_email("jane@acme.com")
        self.assertEqual(result.status, "timeout")

    @patch("src.smtp_verifier.verify_email")
    def test_check_catch_all(self, mock_verify):
        mock_verify.return_value = VerificationResult(status="verified")
        self.assertTrue(check_catch_all("acme.com"))


if __name__ == "__main__":
    unittest.main()
