"""
Email permutation generator for AutoApply V2.2.

Given a person's first name, last name, and company domain,
generates candidate email addresses ordered by prevalence.
Pure function — no side effects, no imports beyond stdlib.
"""

import re
from typing import List


def _normalize(name: str) -> str:
    """Lowercase, strip whitespace, remove non-alpha chars except hyphens."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z\-]", "", name)
    return name


def _first_part(name: str) -> str:
    """For hyphenated names like 'mary-jane', return the first part."""
    return name.split("-")[0] if "-" in name else name


def generate_permutations(first_name: str, last_name: str, domain: str) -> List[str]:
    """
    Generate email permutations ordered by prevalence.

    Supports 8 patterns:
        first.last, firstlast, first, flast, f.last,
        first_last, firstl, last.first

    Args:
        first_name: Person's first name
        last_name: Person's last name
        domain: Company email domain (e.g. "example.com")

    Returns:
        List of candidate email addresses, most likely first.
    """
    if not first_name or not last_name or not domain:
        return []

    first = _normalize(first_name)
    last = _normalize(last_name)
    domain = domain.strip().lower()

    if not first or not last:
        return []

    f_initial = first[0]
    l_initial = last[0]

    # For hyphenated names, also try the dehyphenated versions
    first_no_hyphen = first.replace("-", "")
    last_no_hyphen = last.replace("-", "")

    candidates = []
    seen = set()

    def _add(local_part: str):
        email = f"{local_part}@{domain}"
        if email not in seen:
            seen.add(email)
            candidates.append(email)

    # Ordered by real-world prevalence
    _add(f"{first}.{last}")           # first.last (most common)
    _add(f"{first}{last}")            # firstlast
    _add(first)                       # first
    _add(f"{f_initial}{last}")        # flast
    _add(f"{f_initial}.{last}")       # f.last
    _add(f"{first}_{last}")           # first_last
    _add(f"{first}{l_initial}")       # firstl
    _add(f"{last}.{first}")           # last.first

    # Dehyphenated variants (if names contain hyphens)
    if "-" in first or "-" in last:
        _add(f"{first_no_hyphen}.{last_no_hyphen}")
        _add(f"{first_no_hyphen}{last_no_hyphen}")
        _add(f"{_first_part(first)}.{last_no_hyphen}")
        _add(f"{_first_part(first)}{last_no_hyphen}")

    return candidates


def match_pattern(email: str, first_name: str, last_name: str) -> str | None:
    """
    Given a known email and person name, identify which pattern was used.
    Returns pattern string or None if no match.
    """
    first = _normalize(first_name)
    last = _normalize(last_name)
    local = email.split("@")[0].lower()

    patterns = {
        f"{first}.{last}": "first.last",
        f"{first}{last}": "firstlast",
        first: "first",
        f"{first[0]}{last}": "flast",
        f"{first[0]}.{last}": "f.last",
        f"{first}_{last}": "first_last",
        f"{first}{last[0]}": "firstl",
        f"{last}.{first}": "last.first",
    }

    return patterns.get(local)


def apply_pattern(pattern: str, first_name: str, last_name: str, domain: str) -> str:
    """Generate an email from a known pattern and a new person's name."""
    first = _normalize(first_name)
    last = _normalize(last_name)

    templates = {
        "first.last": f"{first}.{last}",
        "firstlast": f"{first}{last}",
        "first": first,
        "flast": f"{first[0]}{last}",
        "f.last": f"{first[0]}.{last}",
        "first_last": f"{first}_{last}",
        "firstl": f"{first}{last[0]}",
        "last.first": f"{last}.{first}",
    }

    local = templates.get(pattern)
    if local is None:
        raise ValueError(f"Unknown pattern: {pattern}")

    return f"{local}@{domain}"
