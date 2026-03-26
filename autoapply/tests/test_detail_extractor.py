"""Tests for detail_extractor module."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.detail_extractor import (
    extract_company_blurb,
    extract_details,
    extract_key_technology,
    extract_team_or_product,
)


class TestDetailExtractor(unittest.TestCase):
    def test_extract_team_or_product(self):
        text = "Join our Data Platform team to build internal tooling."
        self.assertEqual(extract_team_or_product(text), "Data Platform")

    def test_extract_team_or_product_from_platform_phrase(self):
        text = "You will work on the Search Infrastructure platform across the company."
        self.assertEqual(extract_team_or_product(text), "Search Infrastructure")

    def test_extract_key_technology_prefers_first_match(self):
        text = "Experience with Python, PyTorch, and Kubernetes is preferred."
        self.assertEqual(extract_key_technology(text, ["python", "pytorch", "kubernetes"]), "python")

    def test_extract_company_blurb_from_meta(self):
        html = """
        <html><head>
            <meta name="description" content="Acme builds AI tools for modern teams.">
        </head><body></body></html>
        """
        self.assertEqual(extract_company_blurb(html), "Acme builds AI tools for modern teams.")

    def test_extract_details_bundle(self):
        result = extract_details(
            "Join our ML Platform team. Strong Python and PyTorch skills required.",
            ["python", "pytorch", "sql"],
            "<html><body><p>Acme builds developer infrastructure for ML teams.</p></body></html>",
        )
        self.assertEqual(result.team_or_product, "ML Platform")
        self.assertEqual(result.key_technology, "python")
        self.assertIn("Acme builds developer infrastructure", result.company_blurb)


if __name__ == "__main__":
    unittest.main()
