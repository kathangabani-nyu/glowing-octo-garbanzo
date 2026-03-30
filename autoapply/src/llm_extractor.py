"""
LLM-based detail extraction for AutoApply V2.2.

Sends job posting + user skills to a local Ollama model (Llama 3.1 8B)
for higher-quality detail extraction than regex alone.

Every extracted field is validated against the source text to guard
against hallucination. On any failure (Ollama unreachable, timeout,
parse error, validation failure), falls back to the regex extractor.

Module owner: Claude Code
"""

import json
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import requests

from src.detail_extractor import ExtractionResult, extract_details as regex_extract
from src.config import LLMConfig
from src.utils import get_logger

logger = get_logger("llm_extractor")

# System prompt for structured extraction
SYSTEM_PROMPT = """\
You are a precise data extraction assistant. Extract specific fields from a job posting.
You MUST only extract information that is explicitly stated in the provided text.
Do NOT invent, infer, or hallucinate any information.

Return a JSON object with exactly these fields:
{
  "team_or_product": "The team or product name mentioned in the posting, or null",
  "key_technology": "The most prominent technology from the candidate's skill list that appears in the posting, or null",
  "company_blurb": "A brief factual description of what the company does, extracted from the text, or null"
}

Rules:
- team_or_product: Must be a proper noun or named team/product that appears verbatim in the posting text.
- key_technology: Must appear in BOTH the posting text AND the candidate's skill list.
- company_blurb: Must be a direct quote or close paraphrase from the posting, max 60 characters.
- If you are not confident a field is correct, set it to null.
- Return ONLY the JSON object, no other text."""

ASSEMBLY_GATE_SYSTEM_PROMPT = """\
You are a strict outreach quality reviewer.
Decide if a drafted outreach email should be allowed, reviewed, or rejected.

Return ONLY JSON with exactly these fields:
{
  "role_fit": "allow|review|reject",
  "role_fit_confidence": 0.0,
  "contact_name_ok": true,
  "safe_greeting_name": "string",
  "message_quality": "allow|review|reject",
  "message_confidence": 0.0,
  "reasons": ["short reason", "short reason"]
}

Rules:
- Reject roles that are off-target for the provided profile.
- If contact name seems synthetic, generic, or unsafe for first-name greeting, set contact_name_ok=false and provide a safe greeting fallback.
- Reject if email content is clearly mismatched to role type.
- If uncertain, use review (not allow).
- Keep reasons concise and factual.
"""


@dataclass
class AssemblyGateResult:
    role_fit: str = "review"
    role_fit_confidence: float = 0.0
    contact_name_ok: bool = False
    safe_greeting_name: str = "there"
    message_quality: str = "review"
    message_confidence: float = 0.0
    reasons: List[str] = None


def _build_user_prompt(
    posting_text: str,
    skills: List[str],
    company: str,
    role_title: str,
) -> str:
    """Build the user prompt with the posting and context."""
    skills_str = ", ".join(skills) if skills else "none provided"
    return f"""Company: {company}
Role: {role_title}

Candidate's skills: {skills_str}

Job posting text:
---
{posting_text[:3000]}
---

Extract the fields as specified. Return only JSON."""


def _query_ollama(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
) -> Optional[str]:
    """
    Send a chat completion request to Ollama.
    Returns the response text, or None on failure.
    """
    url = f"{config.ollama_url.rstrip('/')}/api/chat"

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 256,
        },
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("message", {}).get("content", "")
    except requests.ConnectionError:
        logger.debug("Ollama not reachable")
        return None
    except requests.Timeout:
        logger.warning(f"Ollama request timed out after {config.timeout_seconds}s")
        return None
    except Exception as e:
        logger.warning(f"Ollama request failed: {e}")
        return None


def _parse_json_response(response_text: str) -> Optional[dict]:
    """Parse the LLM JSON response, handling common formatting issues."""
    if not response_text:
        return None

    # Try direct parse
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the response
    json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.debug(f"Failed to parse LLM response as JSON: {response_text[:200]}")
    return None


def _validate_team_or_product(
    value: Optional[str],
    posting_text: str,
    generic_team_words: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Validate that team_or_product appears in the posting text.
    The value must exist as a substring (case-insensitive) in the source.
    Also rejects boilerplate phrases and non-team names.
    """
    if not value or not posting_text:
        return None

    value = value.strip()
    if not value or len(value) > 60:
        return None

    # Reject boilerplate / non-team-name extractions
    JUNK_TEAM_PATTERNS = [
        "via this link",
        "via the link",
        "click here",
        "learn more",
        "apply now",
        "this link and",
        "link below",
    ]
    value_lower = value.lower()
    for junk in JUNK_TEAM_PATTERNS:
        if junk in value_lower:
            logger.debug(f"Hallucination guard: team '{value}' matches junk pattern '{junk}'")
            return None

    # Reject if value is just the company name repeated
    # (the blurb field already captures company info)
    if len(value_lower.split()) <= 1:
        # Single-word team names are fine only if they're not generic
        GENERIC_SINGLE = set(generic_team_words or ["team", "engineering", "company", "organization", "group"])
        if value_lower in GENERIC_SINGLE:
            return None

    # Must appear in the source text
    if value_lower not in posting_text.lower():
        logger.debug(f"Hallucination guard: '{value}' not found in posting text")
        return None

    return value


def _validate_key_technology(
    value: Optional[str],
    posting_text: str,
    skills: List[str],
) -> Optional[str]:
    """
    Validate that key_technology appears in BOTH the posting AND the skill list.
    """
    if not value or not posting_text:
        return None

    value = value.strip()
    if not value:
        return None

    posting_lower = posting_text.lower()
    value_lower = value.lower()

    # Must appear in posting
    if value_lower not in posting_lower:
        logger.debug(f"Hallucination guard: tech '{value}' not in posting")
        return None

    # Must appear in user's skill list
    skills_lower = [s.lower() for s in skills] if skills else []
    if value_lower not in skills_lower:
        logger.debug(f"Hallucination guard: tech '{value}' not in user skills")
        return None

    # Return the original skill list version for consistency
    for skill in skills:
        if skill.lower() == value_lower:
            return skill

    return value


def _validate_company_blurb(value: Optional[str], posting_text: str) -> Optional[str]:
    """
    Validate that company_blurb is grounded in the posting text.
    At least 50% of the significant words must appear in the source.
    """
    if not value or not posting_text:
        return None

    value = value.strip()
    if not value or len(value) > 80:
        return None

    # Check that significant words from the blurb appear in the posting
    posting_lower = posting_text.lower()
    words = [w for w in re.findall(r'\b\w{4,}\b', value.lower())]
    if not words:
        return None

    found = sum(1 for w in words if w in posting_lower)
    coverage = found / len(words)

    if coverage < 0.5:
        logger.debug(
            f"Hallucination guard: blurb word coverage {coverage:.0%} < 50%: '{value}'"
        )
        return None

    return value


def check_ollama_available(config: LLMConfig) -> bool:
    """Check if Ollama is running and the configured model is available."""
    try:
        url = f"{config.ollama_url.rstrip('/')}/api/tags"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        # Check if configured model is available (with or without tag)
        model_base = config.model.split(":")[0]
        for name in model_names:
            if name == config.model or name.startswith(model_base):
                return True
        logger.info(
            f"Ollama running but model '{config.model}' not found. "
            f"Available: {model_names}"
        )
        return False
    except Exception:
        return False


def extract_details_llm(
    posting_text: str,
    skills: List[str],
    company: str,
    role_title: str,
    config: LLMConfig,
) -> ExtractionResult:
    """
    Extract detail fields using the local LLM via Ollama.

    Every field is validated against source text. On any failure,
    falls back to the regex extractor for that field (or all fields).

    Args:
        posting_text: The raw job posting text
        skills: User's skill list from config
        company: Company name
        role_title: Job title
        config: LLM configuration

    Returns:
        ExtractionResult with validated fields
    """
    # Build prompt
    user_prompt = _build_user_prompt(posting_text, skills, company, role_title)

    # Query Ollama
    response_text = _query_ollama(config, SYSTEM_PROMPT, user_prompt)
    if response_text is None:
        logger.info("LLM unavailable, falling back to regex extractor")
        return regex_extract(posting_text, skills)

    # Parse response
    parsed = _parse_json_response(response_text)
    if parsed is None:
        logger.warning("LLM returned unparseable response, falling back to regex")
        return regex_extract(posting_text, skills)

    # Validate each field independently
    team = _validate_team_or_product(
        parsed.get("team_or_product"),
        posting_text,
        config.generic_team_words,
    )
    tech = _validate_key_technology(
        parsed.get("key_technology"), posting_text, skills
    )
    blurb = _validate_company_blurb(
        parsed.get("company_blurb"), posting_text
    )

    # For any field that failed validation, try the regex extractor
    regex_result = None
    if team is None or tech is None or blurb is None:
        regex_result = regex_extract(posting_text, skills)

    result = ExtractionResult(
        team_or_product=team if team is not None else (regex_result.team_or_product if regex_result else None),
        key_technology=tech if tech is not None else (regex_result.key_technology if regex_result else None),
        company_blurb=blurb if blurb is not None else (regex_result.company_blurb if regex_result else None),
    )

    logger.debug(
        f"LLM extraction: team={result.team_or_product}, "
        f"tech={result.key_technology}, blurb={result.company_blurb}"
    )
    return result


def _build_assembly_gate_prompt(candidate: Dict[str, Any]) -> str:
    return (
        "Evaluate this outreach candidate using the provided profile and draft.\n"
        f"Profile name: {candidate.get('profile_name', '')}\n"
        f"Target title keywords: {', '.join(candidate.get('title_keywords', []))}\n"
        f"Excluded titles: {', '.join(candidate.get('title_exclude', []))}\n"
        f"Profile reject roles: {', '.join(candidate.get('reject_roles', []))}\n"
        f"Role title: {candidate.get('role_title', '')}\n"
        f"Company: {candidate.get('company', '')}\n"
        f"Contact name: {candidate.get('contact_name', '')}\n"
        f"Contact email: {candidate.get('contact_email', '')}\n"
        f"Draft subject: {candidate.get('subject', '')}\n"
        f"Draft body:\n---\n{candidate.get('body', '')[:2500]}\n---\n"
    )


def validate_assembly_candidate(
    config: LLMConfig,
    candidate: Dict[str, Any],
) -> AssemblyGateResult:
    """Validate role-fit/contact/greeting/message quality for assembled emails."""
    prompt = _build_assembly_gate_prompt(candidate)
    response_text = _query_ollama(config, ASSEMBLY_GATE_SYSTEM_PROMPT, prompt)
    if response_text is None:
        return AssemblyGateResult(
            role_fit="review",
            message_quality="review",
            reasons=["ollama_unavailable"],
        )

    parsed = _parse_json_response(response_text)
    if parsed is None:
        return AssemblyGateResult(
            role_fit="review",
            message_quality="review",
            reasons=["assembly_gate_parse_failed"],
        )

    role_fit = parsed.get("role_fit", "review")
    message_quality = parsed.get("message_quality", "review")
    if role_fit not in {"allow", "review", "reject"}:
        role_fit = "review"
    if message_quality not in {"allow", "review", "reject"}:
        message_quality = "review"

    reasons = parsed.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    reasons = [str(r) for r in reasons[:4] if str(r).strip()]

    safe_greeting = (parsed.get("safe_greeting_name") or "").strip() or "there"
    return AssemblyGateResult(
        role_fit=role_fit,
        role_fit_confidence=float(parsed.get("role_fit_confidence") or 0.0),
        contact_name_ok=bool(parsed.get("contact_name_ok")),
        safe_greeting_name=safe_greeting,
        message_quality=message_quality,
        message_confidence=float(parsed.get("message_confidence") or 0.0),
        reasons=reasons,
    )
