"""Tests for config authoring preflight checks."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_config, load_watchlist
from src.config_authoring import validate_config_authoring


GOOD_CONFIG = """
sender:
  name: Tester
  email: tester@example.com
"""

BAD_CONFIG = """
sender:
  name: Tester
  email: your.email@gmail.com
"""

GOOD_WATCHLIST = """
companies:
  - name: Acme
    domain: acme.com
    priority: 3
    ats: greenhouse
    slug: acme
"""

SMALL_WEAK_WATCHLIST = """
companies:
  - name: TinyCo
    domain: tinyco.com
    priority: 8
"""


class TestConfigAuthoring(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "config.yaml")
        self.watchlist_path = os.path.join(self.temp_dir.name, "watchlist.yaml")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, config_text: str, watchlist_text: str):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(config_text)
        with open(self.watchlist_path, "w", encoding="utf-8") as handle:
            handle.write(watchlist_text)

    def test_validate_config_authoring_passes_for_sane_inputs(self):
        self._write(GOOD_CONFIG, GOOD_WATCHLIST)
        config = load_config(self.config_path)
        watchlist = load_watchlist(self.watchlist_path)

        report = validate_config_authoring(config, watchlist)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])

    def test_validate_config_authoring_flags_placeholder_sender_email(self):
        self._write(BAD_CONFIG, GOOD_WATCHLIST)
        config = load_config(self.config_path)
        watchlist = load_watchlist(self.watchlist_path)

        report = validate_config_authoring(config, watchlist)
        self.assertTrue(any("placeholder" in err for err in report.errors))

    def test_validate_config_authoring_warns_for_weak_watchlist(self):
        self._write(GOOD_CONFIG, SMALL_WEAK_WATCHLIST)
        config = load_config(self.config_path)
        watchlist = load_watchlist(self.watchlist_path)

        report = validate_config_authoring(config, watchlist)
        self.assertEqual(report.errors, [])
        self.assertGreaterEqual(len(report.warnings), 1)
        self.assertGreaterEqual(len(report.notes), 1)


if __name__ == "__main__":
    unittest.main()
