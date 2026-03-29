"""
Keyword-based job qualification for AutoApply V2.2.

Scores unscored jobs against the user's target profile and marks them as:
- qualified_auto
- qualified_review
- reject

Module owner: Codex
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from src.config import Config
from src.db import Database
from src.utils import get_logger


logger = get_logger("job_filter")


@dataclass
class ScoreResult:
    score: int
    status: str
    reasons: List[str]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _contains_any(text: str, keywords: List[str]) -> List[str]:
    normalized = _normalize(text)
    return [keyword for keyword in keywords if _normalize(keyword) and _normalize(keyword) in normalized]


def _extract_required_years(text: str) -> int | None:
    normalized = _normalize(text)
    matches = []

    for pattern in (
        r"(\d+)\+?\s+years",
        r"minimum\s+of\s+(\d+)\s+years",
        r"at\s+least\s+(\d+)\s+years",
        r"(\d+)\s*-\s*(\d+)\s+years",
    ):
        for match in re.finditer(pattern, normalized):
            groups = [int(value) for value in match.groups() if value is not None]
            if groups:
                matches.append(max(groups))

    return max(matches) if matches else None


def _is_rejected_role(title: str, reject_roles: List[str]) -> List[str]:
    """Check if a job title belongs to a configured reject-role family."""
    normalized = _normalize(title)
    return [role for role in reject_roles if role in normalized]


def _priority_bonus(priority: int) -> Tuple[int, str]:
    if priority <= 2:
        return 12, "high-priority company"
    if priority == 3:
        return 6, "medium-priority company"
    return 0, ""


def score_job(config: Config, job_row, company_row) -> ScoreResult:
    title = job_row["title"] or ""
    posting_text = job_row["posting_text"] or ""
    location = job_row["location"] or ""
    combined = " ".join([title, posting_text, location])
    reasons: List[str] = []
    score = 0

    # ── Hard reject: non-engineering role families ──
    rejected_roles = _is_rejected_role(title, config.domain_profile.reject_roles)
    if rejected_roles:
        reasons.append(f"rejected role: {', '.join(rejected_roles[:3])}")
        return ScoreResult(score=0, status="reject", reasons=reasons)

    title_matches = _contains_any(title, config.job_targets.title_keywords)
    if title_matches:
        bonus = min(40, 20 + 10 * (len(title_matches) - 1))
        score += bonus
        reasons.append(f"title match: {', '.join(title_matches[:3])}")

    excluded = _contains_any(title, config.job_targets.title_exclude)
    if excluded:
        reasons.append(f"title excluded: {', '.join(excluded[:3])}")
        return ScoreResult(score=0, status="reject", reasons=reasons)

    # ── Hard reject: non-US locations ──
    if config.job_targets.us_only:
        loc_reject = _contains_any(
            " ".join([title, location]),
            config.job_targets.location_reject_keywords,
        )
        if loc_reject:
            reasons.append(f"non-US location: {', '.join(loc_reject[:3])}")
            return ScoreResult(score=0, status="reject", reasons=reasons)

        # If location is set but contains no US signals, reject
        if location:
            us_signals = [
                "united states", "usa", "us ", "u.s.",
                "new york", "nyc", "san francisco", "sf",
                "los angeles", "la", "seattle", "austin",
                "boston", "chicago", "denver", "atlanta",
                "miami", "dallas", "houston", "dc",
                "washington", "philadelphia", "portland",
                "san diego", "san jose", "raleigh",
                "remote", "anywhere",
                "ca", "ny", "tx", "wa", "ma", "il", "co",
                "ga", "fl", "pa", "or", "nc", "va", "md",
            ]
            loc_lower = _normalize(location)
            if not any(s in loc_lower for s in us_signals):
                reasons.append(f"location appears non-US: {location[:60]}")
                return ScoreResult(score=0, status="reject", reasons=reasons)

    # ── Gate: require at least a title keyword match OR skill match ──
    # Without this, a random role at a high-priority company can qualify
    # purely on company bonus + location/remote match
    skill_matches = _contains_any(combined, config.job_targets.skills)
    if not title_matches and not skill_matches:
        reasons.append("no title or skill match — not a relevant role")
        return ScoreResult(score=0, status="reject", reasons=reasons)

    if skill_matches:
        bonus = min(30, 8 * len(skill_matches))
        score += bonus
        reasons.append(f"skills matched: {', '.join(skill_matches[:4])}")

    seniority_matches = _contains_any(combined, config.job_targets.seniority)
    if seniority_matches:
        score += 10
        reasons.append(f"seniority matched: {', '.join(seniority_matches[:2])}")

    if config.job_targets.remote_ok and "remote" in _normalize(combined):
        score += 15
        reasons.append("remote-friendly")
    else:
        location_matches = _contains_any(location or posting_text, config.job_targets.locations)
        if location_matches:
            score += 15
            reasons.append(f"location matched: {', '.join(location_matches[:2])}")

    years_required = _extract_required_years(combined)
    if years_required is not None:
        if years_required > config.job_targets.max_experience_years:
            reasons.append(
                f"requires {years_required}+ years, max target is {config.job_targets.max_experience_years}"
            )
            return ScoreResult(score=score, status="reject", reasons=reasons)
        if years_required < config.job_targets.min_experience_years:
            reasons.append(
                f"role appears below minimum experience target ({years_required}+ years)"
            )
        else:
            score += 8
            reasons.append(f"experience fit: {years_required}+ years")

    visa_rejects = _contains_any(combined, config.job_targets.visa_reject_keywords)
    if visa_rejects:
        reasons.append(f"visa/auth rejection: {', '.join(visa_rejects[:2])}")
        return ScoreResult(score=score, status="reject", reasons=reasons)

    priority_bonus, priority_reason = _priority_bonus(company_row["priority"])
    if priority_bonus:
        score += priority_bonus
        reasons.append(priority_reason)

    if score >= config.qualification.auto_threshold:
        status = "qualified_auto"
    elif score >= config.qualification.review_threshold:
        status = "qualified_review"
    else:
        status = "reject"

    if not reasons:
        reasons.append("no strong matching signals found")

    return ScoreResult(score=score, status=status, reasons=reasons)


def run(config: Config, db: Database, dry_run: bool = False) -> int:
    """
    Score all unscored jobs and update their qualification status.

    Returns the number of processed jobs.
    """
    processed = 0
    counters = {
        "qualified_auto": 0,
        "qualified_review": 0,
        "reject": 0,
    }

    for job in db.get_unscored_jobs():
        company = db.get_company(job["company_id"])
        if company is None:
            logger.warning("Skipping job %s: missing company %s", job["id"], job["company_id"])
            continue

        result = score_job(config, job, company)
        reasons_text = "; ".join(result.reasons)

        if not dry_run:
            db.update_job_score(
                job["id"],
                result.status,
                result.score,
                reasons_text,
                mode="keyword",
            )

        counters[result.status] += 1
        processed += 1
        logger.info(
            "[%s] %s / %s -> %s (%s)",
            company["domain"],
            company["name"],
            job["title"],
            result.status,
            result.score,
        )

    if processed and not dry_run:
        db.increment_metric("jobs_qualified_auto", counters["qualified_auto"])
        db.increment_metric("jobs_qualified_review", counters["qualified_review"])
        db.increment_metric("jobs_rejected", counters["reject"])

    return processed
