"""Tests for permutator module."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.permutator import apply_pattern, generate_permutations, match_pattern


class TestPermutator(unittest.TestCase):
    def test_generate_permutations_order(self):
        result = generate_permutations("Jane", "Smith", "acme.com")
        self.assertEqual(
            result[:4],
            [
                "jane.smith@acme.com",
                "janesmith@acme.com",
                "jane@acme.com",
                "jsmith@acme.com",
            ],
        )

    def test_generate_hyphenated_variants(self):
        result = generate_permutations("Mary-Jane", "Watson-Parker", "acme.com")
        self.assertIn("maryjane.watsonparker@acme.com", result)
        self.assertIn("mary.watsonparker@acme.com", result)

    def test_match_and_apply_pattern(self):
        pattern = match_pattern("jane.smith@acme.com", "Jane", "Smith")
        self.assertEqual(pattern, "first.last")
        generated = apply_pattern(pattern, "John", "Doe", "acme.com")
        self.assertEqual(generated, "john.doe@acme.com")


if __name__ == "__main__":
    unittest.main()
