"""Tests for llm_extractor module.

Tests validation/hallucination guards directly (no Ollama needed).
Tests the full extraction path with mocked Ollama responses.

Module owner: Claude Code
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import LLMConfig
from src.detail_extractor import ExtractionResult
from src.llm_extractor import (
    _validate_team_or_product,
    _validate_key_technology,
    _validate_company_blurb,
    _parse_json_response,
    _build_user_prompt,
    extract_details_llm,
    check_ollama_available,
)

SAMPLE_POSTING = """
We're looking for a Senior Machine Learning Engineer to join our Data Platform team.
You'll work on building large-scale recommendation systems using Python and PyTorch.
Acme Corp is a leading provider of cloud-based analytics solutions that help
enterprises make data-driven decisions. We're a team of 200+ engineers
building the future of business intelligence.
"""

SAMPLE_SKILLS = ["Python", "PyTorch", "TensorFlow", "SQL", "Kubernetes"]


class TestValidateTeamOrProduct(unittest.TestCase):
    def test_valid_team(self):
        result = _validate_team_or_product("Data Platform", SAMPLE_POSTING)
        self.assertEqual(result, "Data Platform")

    def test_hallucinated_team(self):
        result = _validate_team_or_product("Infrastructure", SAMPLE_POSTING)
        self.assertIsNone(result)

    def test_none_input(self):
        result = _validate_team_or_product(None, SAMPLE_POSTING)
        self.assertIsNone(result)

    def test_empty_string(self):
        result = _validate_team_or_product("", SAMPLE_POSTING)
        self.assertIsNone(result)

    def test_too_long(self):
        result = _validate_team_or_product("A" * 70, SAMPLE_POSTING)
        self.assertIsNone(result)

    def test_case_insensitive(self):
        result = _validate_team_or_product("data platform", SAMPLE_POSTING)
        self.assertEqual(result, "data platform")

    def test_generic_words_can_be_configured(self):
        result = _validate_team_or_product("division", "Join our division", ["division"])
        self.assertIsNone(result)


class TestValidateKeyTechnology(unittest.TestCase):
    def test_valid_tech_in_both(self):
        result = _validate_key_technology("Python", SAMPLE_POSTING, SAMPLE_SKILLS)
        self.assertEqual(result, "Python")

    def test_valid_tech_case_insensitive(self):
        result = _validate_key_technology("python", SAMPLE_POSTING, SAMPLE_SKILLS)
        self.assertEqual(result, "Python")  # Returns original from skills list

    def test_tech_not_in_posting(self):
        result = _validate_key_technology("TensorFlow", SAMPLE_POSTING, SAMPLE_SKILLS)
        self.assertIsNone(result)

    def test_tech_not_in_skills(self):
        result = _validate_key_technology("recommendation systems", SAMPLE_POSTING, SAMPLE_SKILLS)
        self.assertIsNone(result)

    def test_hallucinated_tech(self):
        result = _validate_key_technology("Rust", SAMPLE_POSTING, SAMPLE_SKILLS)
        self.assertIsNone(result)

    def test_none_input(self):
        result = _validate_key_technology(None, SAMPLE_POSTING, SAMPLE_SKILLS)
        self.assertIsNone(result)


class TestValidateCompanyBlurb(unittest.TestCase):
    def test_valid_blurb(self):
        result = _validate_company_blurb(
            "cloud-based analytics solutions", SAMPLE_POSTING
        )
        self.assertEqual(result, "cloud-based analytics solutions")

    def test_hallucinated_blurb(self):
        result = _validate_company_blurb(
            "Leading provider of quantum computing hardware", SAMPLE_POSTING
        )
        self.assertIsNone(result)

    def test_too_long(self):
        result = _validate_company_blurb("A" * 90, SAMPLE_POSTING)
        self.assertIsNone(result)

    def test_none_input(self):
        result = _validate_company_blurb(None, SAMPLE_POSTING)
        self.assertIsNone(result)

    def test_partially_grounded(self):
        # "cloud-based analytics" appears, some words grounded
        result = _validate_company_blurb(
            "cloud-based analytics for enterprises", SAMPLE_POSTING
        )
        self.assertIsNotNone(result)


class TestParseJsonResponse(unittest.TestCase):
    def test_clean_json(self):
        response = '{"team_or_product": "Data Platform", "key_technology": "Python", "company_blurb": null}'
        result = _parse_json_response(response)
        self.assertIsNotNone(result)
        self.assertEqual(result["team_or_product"], "Data Platform")

    def test_json_with_prefix(self):
        response = 'Here is the result:\n{"team_or_product": "ML", "key_technology": null, "company_blurb": null}'
        result = _parse_json_response(response)
        self.assertIsNotNone(result)

    def test_empty_response(self):
        result = _parse_json_response("")
        self.assertIsNone(result)

    def test_invalid_json(self):
        result = _parse_json_response("This is not JSON at all")
        self.assertIsNone(result)

    def test_none_input(self):
        result = _parse_json_response(None)
        self.assertIsNone(result)


class TestBuildUserPrompt(unittest.TestCase):
    def test_includes_all_fields(self):
        prompt = _build_user_prompt(
            "Job posting text here", ["Python", "SQL"],
            "Acme Corp", "ML Engineer"
        )
        self.assertIn("Acme Corp", prompt)
        self.assertIn("ML Engineer", prompt)
        self.assertIn("Python, SQL", prompt)
        self.assertIn("Job posting text here", prompt)


class TestExtractDetailsLLM(unittest.TestCase):
    """Test the full extraction path with mocked Ollama."""

    def _make_config(self):
        return LLMConfig(
            use_local_llm=True,
            ollama_url="http://localhost:11434",
            model="llama3.1:8b",
            timeout_seconds=30,
            generic_team_words=["team", "engineering", "company", "organization", "group"],
        )

    @patch("src.llm_extractor._query_ollama")
    def test_successful_extraction(self, mock_query):
        mock_query.return_value = '{"team_or_product": "Data Platform", "key_technology": "Python", "company_blurb": "cloud-based analytics solutions"}'
        config = self._make_config()

        result = extract_details_llm(
            SAMPLE_POSTING, SAMPLE_SKILLS, "Acme Corp", "ML Engineer", config
        )

        self.assertEqual(result.team_or_product, "Data Platform")
        self.assertEqual(result.key_technology, "Python")
        self.assertIn("analytics", result.company_blurb)

    @patch("src.llm_extractor._query_ollama")
    def test_hallucinated_fields_fall_back_to_regex(self, mock_query):
        # LLM returns a hallucinated team name
        mock_query.return_value = '{"team_or_product": "Quantum Division", "key_technology": "Python", "company_blurb": null}'
        config = self._make_config()

        result = extract_details_llm(
            SAMPLE_POSTING, SAMPLE_SKILLS, "Acme Corp", "ML Engineer", config
        )

        # Hallucinated team should be rejected, regex fallback should find "Data Platform"
        self.assertNotEqual(result.team_or_product, "Quantum Division")
        # Tech should pass validation
        self.assertEqual(result.key_technology, "Python")

    @patch("src.llm_extractor._query_ollama")
    def test_ollama_unreachable_falls_back(self, mock_query):
        mock_query.return_value = None
        config = self._make_config()

        result = extract_details_llm(
            SAMPLE_POSTING, SAMPLE_SKILLS, "Acme Corp", "ML Engineer", config
        )

        # Should get regex results
        self.assertIsInstance(result, ExtractionResult)

    @patch("src.llm_extractor._query_ollama")
    def test_unparseable_response_falls_back(self, mock_query):
        mock_query.return_value = "I can't help with that"
        config = self._make_config()

        result = extract_details_llm(
            SAMPLE_POSTING, SAMPLE_SKILLS, "Acme Corp", "ML Engineer", config
        )

        self.assertIsInstance(result, ExtractionResult)

    @patch("src.llm_extractor._query_ollama")
    def test_partial_validation_failure(self, mock_query):
        # tech is valid, team is hallucinated, blurb is valid
        mock_query.return_value = '{"team_or_product": "Fake Team", "key_technology": "PyTorch", "company_blurb": "analytics solutions for enterprises"}'
        config = self._make_config()

        result = extract_details_llm(
            SAMPLE_POSTING, SAMPLE_SKILLS, "Acme Corp", "ML Engineer", config
        )

        # Team should be rejected (hallucinated), tech should pass
        self.assertNotEqual(result.team_or_product, "Fake Team")
        self.assertEqual(result.key_technology, "PyTorch")


class TestCheckOllamaAvailable(unittest.TestCase):
    @patch("src.llm_extractor.requests.get")
    def test_available(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        config = LLMConfig(model="llama3.1:8b")
        self.assertTrue(check_ollama_available(config))

    @patch("src.llm_extractor.requests.get")
    def test_model_not_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "mistral:7b"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        config = LLMConfig(model="llama3.1:8b")
        self.assertFalse(check_ollama_available(config))

    @patch("src.llm_extractor.requests.get")
    def test_connection_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError()
        config = LLMConfig(model="llama3.1:8b")
        self.assertFalse(check_ollama_available(config))


if __name__ == "__main__":
    unittest.main()
