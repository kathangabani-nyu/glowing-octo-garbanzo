"""Tests for utils module."""

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils import RateLimiter, get_logger, retry


class TestRetry(unittest.TestCase):
    def test_retry_succeeds_after_failure(self):
        attempts = {"count": 0}

        @retry(max_attempts=3, base_delay=0)
        def flaky():
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise ValueError("try again")
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(attempts["count"], 2)


class TestRateLimiter(unittest.TestCase):
    @patch("src.utils.time.sleep")
    @patch("src.utils.time.time")
    def test_acquire_waits_when_bucket_is_empty(self, mock_time, mock_sleep):
        mock_time.side_effect = [0.0, 0.0, 0.0, 0.5, 1.0, 1.0]
        limiter = RateLimiter(rate=1.0, capacity=1)
        limiter.acquire()
        limiter.acquire()
        self.assertTrue(mock_sleep.called)


class TestLogger(unittest.TestCase):
    def test_get_logger_reuses_handlers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger_one = get_logger("test_utils_logger", log_dir=temp_dir)
            logger_two = get_logger("test_utils_logger", log_dir=temp_dir)
            self.assertIs(logger_one, logger_two)
            self.assertFalse(logger_one.propagate)
            self.assertGreaterEqual(len(logger_one.handlers), 2)
            self.assertEqual(logger_one.level, logging.DEBUG)
            for handler in list(logger_one.handlers):
                handler.close()
                logger_one.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
