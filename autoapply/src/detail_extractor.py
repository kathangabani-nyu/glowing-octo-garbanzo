"""
Regex-based detail extraction for AutoApply V2.2.

Extracts a few safe, source-grounded fields from job postings and homepage HTML:
- team_or_product
- key_technology
- company_blurb

Module owner: Codex
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Iterable, Optional

from bs4 import BeautifulSoup


TEAM_PATTERNS = (
    re.compile(
        r"(?:join|joining|on|within|support)\s+(?:our|the)?\s*"
        r"([A-Z][A-Za-z0-9&/\- ]{2,50}?)\s+(?:team|platform|group|org|organization)\b"
    ),
    re.compile(
        r"(?:work|working)\s+on\s+(?:the\s+)?"
        r"([A-Z][A-Za-z0-9&/\- ]{2,50}?)\s+(?:team|platform|group|org|organization)\b"
    ),
    re.compile(
        r"(?:our|the)\s+([A-Z][A-Za-z0-9&/\- ]{2,50}?)\s+(?:team|platform|group|org|organization)\b"
    ),
)

JUNK_TEAM_PATTERNS = (
    "via this link",
    "via the link",
    "click here",
    "learn more",
    "apply now",
    "this link and",
    "link below",
)


@dataclass
class ExtractionResult:
    team_or_product: Optional[str]
    key_technology: Optional[str]
    company_blurb: Optional[str]


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_phrase(value: str) -> Optional[str]:
    cleaned = _normalize_whitespace(value.strip(" ,;:-"))
    if not cleaned:
        return None
    if len(cleaned) > 60:
        return None
    lowered = cleaned.lower()
    if any(pattern in lowered for pattern in JUNK_TEAM_PATTERNS):
        return None
    if lowered in {"team", "platform", "group", "organization", "org"}:
        return None
    return cleaned


def extract_team_or_product(posting_text: str) -> Optional[str]:
    text = _normalize_whitespace(posting_text or "")
    if not text:
        return None

    for pattern in TEAM_PATTERNS:
        match = pattern.search(text)
        if match:
            return _clean_phrase(match.group(1))
    return None


def _iter_skill_matches(posting_text: str, skills: Iterable[str]):
    normalized_posting = (posting_text or "").lower()
    for skill in skills:
        if not skill:
            continue
        normalized_skill = skill.lower().strip()
        if not normalized_skill:
            continue
        match = re.search(rf"(?<!\w){re.escape(normalized_skill)}(?!\w)", normalized_posting)
        if match:
            yield (match.start(), -len(normalized_skill), skill)


def extract_key_technology(posting_text: str, skills: Iterable[str]) -> Optional[str]:
    matches = sorted(_iter_skill_matches(posting_text, skills))
    if not matches:
        return None
    return matches[0][2]


def extract_company_blurb(homepage_html: str) -> Optional[str]:
    if not homepage_html:
        return None

    soup = BeautifulSoup(unescape(homepage_html), "html.parser")

    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta and meta.get("content"):
        return _clean_phrase(meta["content"])

    og_meta = soup.find("meta", attrs={"property": re.compile("og:description", re.I)})
    if og_meta and og_meta.get("content"):
        return _clean_phrase(og_meta["content"])

    for paragraph in soup.find_all("p"):
        text = _clean_phrase(paragraph.get_text(" ", strip=True))
        if text and len(text) >= 25:
            return text

    return None


def extract_details(
    posting_text: str,
    skills: Iterable[str],
    homepage_html: str = "",
) -> ExtractionResult:
    return ExtractionResult(
        team_or_product=extract_team_or_product(posting_text),
        key_technology=extract_key_technology(posting_text, skills),
        company_blurb=extract_company_blurb(homepage_html),
    )
