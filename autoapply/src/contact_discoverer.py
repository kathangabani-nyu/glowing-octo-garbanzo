"""
Contact discovery cascade for AutoApply V2.2.

Company-level sources are resolved once per company, while job posting pages
are resolved per job so job-specific contacts can be preferred downstream.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from src.db import Database
from src.permutator import apply_pattern, generate_permutations, match_pattern
from src.smtp_verifier import check_catch_all, verify_email
from src.utils import RateLimiter, get_logger, retry

logger = get_logger("contact_discoverer")

_smtp_limiter = RateLimiter(rate=0.5, capacity=1)

GENERIC_INBOXES = [
    "careers", "recruiting", "jobs", "talent", "hr",
    "hiring", "people", "team", "apply",
]

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

NAME_TITLE_PATTERNS = [
    re.compile(
        r"(?:posted\s+by|recruiter|hiring\s+manager|contact)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*,\s*(?:technical\s+)?recruiter",
        re.IGNORECASE,
    ),
    re.compile(
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*[\-\|]\s*(?:talent|recruit|hiring|people|hr)",
        re.IGNORECASE,
    ),
]

TEAM_PAGE_PATHS = ["/team", "/about", "/about-us", "/people", "/our-team", "/company"]
CONTACT_PAGE_PATHS = ["/contact", "/contact-us", "/get-in-touch"]

TIER_ORDER = {
    "public_exact": 1,
    "public_generic_inbox": 2,
    "pattern_verified": 3,
    "catch_all_pattern_match": 4,
    "pattern_inferred": 5,
    "catch_all_guess": 6,
    "generic_guess": 7,
    "contact_failed": 8,
    "name_found": 9,
}


@dataclass
class ContactCandidate:
    name: Optional[str]
    email: Optional[str]
    role: Optional[str]
    confidence_tier: str
    contact_source_type: str
    source_url: Optional[str]
    evidence_snippet: str


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _fetch_page(url: str, timeout: int = 15) -> Optional[str]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AutoApply/2.2; job-search-bot)"
        }
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 100:
            return resp.text
        return None
    except requests.RequestException:
        return None


def _extract_emails_from_html(html: str, company_domain: str) -> List[str]:
    all_emails = EMAIL_REGEX.findall(html)
    company_emails = [
        email.lower() for email in all_emails
        if email.lower().endswith(f"@{company_domain}")
    ]
    seen = set()
    result = []
    for email in company_emails:
        if email not in seen:
            seen.add(email)
            result.append(email)
    return result


def _extract_names_from_html(html: str) -> List[Tuple[str, str]]:
    results = []
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    for pattern in NAME_TITLE_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            snippet = text[start:end].strip()
            results.append((name, snippet))
    return results


def _append_structured_person(entry, people: List[Tuple[str, str, str]]):
    if isinstance(entry, list):
        for item in entry:
            _append_structured_person(item, people)
        return
    if not isinstance(entry, dict):
        return

    entry_type = entry.get("@type")
    name = (entry.get("name") or "").strip()
    role = (entry.get("jobTitle") or entry.get("roleName") or "").strip()
    if entry_type == "Person" and re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", name):
        evidence = f"{name} - {role}" if role else name
        people.append((name, role, evidence))

    employees = entry.get("employee")
    if employees:
        _append_structured_person(employees, people)


def _extract_people_from_structured_data(html: str) -> List[Tuple[str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    people: List[Tuple[str, str, str]] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _append_structured_person(payload, people)

    deduped = []
    seen = set()
    for person in people:
        if person not in seen:
            seen.add(person)
            deduped.append(person)
    return deduped


def _extract_people_from_team_page(html: str) -> List[Tuple[str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    people = []

    for name, role, evidence in _extract_people_from_structured_data(html):
        people.append((name, role, evidence))

    for heading in soup.find_all(["h2", "h3", "h4"]):
        name_text = heading.get_text(strip=True)
        if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", name_text):
            role = ""
            sibling = heading.find_next_sibling(["p", "span", "div"])
            if sibling:
                role_text = sibling.get_text(strip=True)
                if len(role_text) < 80:
                    role = role_text
            evidence = f"{name_text} - {role}" if role else name_text
            if (name_text, role, evidence) not in people:
                people.append((name_text, role, evidence))

    member_containers = soup.find_all(
        ["div", "li", "article"],
        class_=re.compile(r"team|member|person|staff|employee", re.I),
    )
    for container in member_containers:
        name_el = container.find(["h2", "h3", "h4", "strong", "b"])
        if not name_el:
            continue
        name_text = name_el.get_text(strip=True)
        if not re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", name_text):
            continue
        role = ""
        role_el = container.find(["p", "span"], class_=re.compile(r"title|role|position", re.I))
        if role_el:
            role = role_el.get_text(strip=True)
        evidence = f"{name_text} - {role}" if role else name_text
        if (name_text, role, evidence) not in people:
            people.append((name_text, role, evidence))

    return people


def _is_recruiting_role(role: str) -> bool:
    if not role:
        return False
    keywords = [
        "recruit", "talent", "hiring", "people", "hr",
        "human resource", "staffing", "acquisition",
    ]
    role_lower = role.lower()
    return any(keyword in role_lower for keyword in keywords)


def _split_name(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) < 2:
        return (parts[0], "") if parts else ("", "")
    return (parts[0], parts[-1])


def _record_pattern_from_candidate(db: Database, domain: str, candidate: ContactCandidate):
    if not candidate.name or not candidate.email:
        return
    if not candidate.email.lower().endswith(f"@{domain}"):
        return
    first, last = _split_name(candidate.name)
    if not first or not last:
        return
    pattern = match_pattern(candidate.email, first, last)
    if not pattern:
        return
    existing = db.get_domain_pattern(domain)
    is_catch_all = bool(existing and existing["is_catch_all"])
    confidence = "public_exact" if candidate.confidence_tier == "public_exact" else "public_match"
    db.upsert_domain_pattern(domain, pattern, confidence, is_catch_all=is_catch_all)


def _resolve_named_candidates(db: Database, domain: str, candidates: List[ContactCandidate]):
    cached_pattern = db.get_domain_pattern(domain)
    is_catch_all = bool(cached_pattern and cached_pattern["is_catch_all"])

    for candidate in [item for item in candidates if item.name and not item.email]:
        first, last = _split_name(candidate.name)
        if not first or not last:
            continue

        cached_pattern = db.get_domain_pattern(domain)
        is_catch_all = bool(cached_pattern and cached_pattern["is_catch_all"])
        known_pattern = cached_pattern["pattern"] if cached_pattern and cached_pattern["pattern"] != "unknown" else None

        if known_pattern:
            guessed_email = apply_pattern(known_pattern, first, last, domain)
            if is_catch_all:
                candidate.email = guessed_email
                candidate.confidence_tier = "catch_all_pattern_match"
                candidate.evidence_snippet += f" | Cached pattern on catch-all domain: {guessed_email}"
                continue

            _smtp_limiter.acquire()
            result = verify_email(guessed_email)
            if result.status == "verified":
                candidate.email = guessed_email
                candidate.confidence_tier = "pattern_verified"
                candidate.evidence_snippet += f" | Verified via cached pattern: {guessed_email}"
                continue

        verified = None
        for email in generate_permutations(first, last, domain):
            _smtp_limiter.acquire()
            result = verify_email(email)
            if result.status == "verified":
                verified = email
                break
            if result.status not in ("rejected",):
                break

        if verified:
            pattern = match_pattern(verified, first, last) or "unknown"
            candidate.email = verified
            candidate.confidence_tier = "pattern_verified"
            candidate.evidence_snippet += f" | SMTP verified: {verified}"
            db.upsert_domain_pattern(domain, pattern, "smtp_verified", is_catch_all=is_catch_all)
            continue

        if not is_catch_all:
            _smtp_limiter.acquire()
            if check_catch_all(domain):
                is_catch_all = True
                cached_pattern = db.get_domain_pattern(domain)
                if cached_pattern and cached_pattern["pattern"] != "unknown":
                    db.upsert_domain_pattern(
                        domain,
                        cached_pattern["pattern"],
                        cached_pattern["confidence"],
                        is_catch_all=True,
                    )
                else:
                    db.upsert_domain_pattern(domain, "unknown", "catch_all", is_catch_all=True)

        if is_catch_all:
            cached_pattern = db.get_domain_pattern(domain)
            known_pattern = cached_pattern["pattern"] if cached_pattern and cached_pattern["pattern"] != "unknown" else None
            if known_pattern:
                guessed_email = apply_pattern(known_pattern, first, last, domain)
                candidate.email = guessed_email
                candidate.confidence_tier = "catch_all_pattern_match"
                candidate.evidence_snippet += f" | Cached pattern on catch-all domain: {guessed_email}"
                continue

        permutations = generate_permutations(first, last, domain)
        if not permutations:
            continue
        candidate.email = permutations[0]
        if is_catch_all:
            candidate.confidence_tier = "catch_all_guess"
            candidate.evidence_snippet += f" | Catch-all guess: {permutations[0]}"
        else:
            candidate.confidence_tier = "pattern_inferred"
            candidate.evidence_snippet += f" | Inferred (unverified): {permutations[0]}"


def _dedupe_candidates(candidates: List[ContactCandidate]) -> List[ContactCandidate]:
    seen = {}
    for candidate in candidates:
        if not candidate.email:
            continue
        email_lower = candidate.email.lower()
        existing = seen.get(email_lower)
        if not existing or TIER_ORDER.get(candidate.confidence_tier, 99) < TIER_ORDER.get(existing.confidence_tier, 99):
            seen[email_lower] = candidate

    return sorted(
        seen.values(),
        key=lambda candidate: TIER_ORDER.get(candidate.confidence_tier, 99),
    )


def _try_generic_inboxes(domain: str) -> Optional[str]:
    for inbox_name in GENERIC_INBOXES:
        email = f"{inbox_name}@{domain}"
        _smtp_limiter.acquire()
        result = verify_email(email)
        if result.status == "verified":
            return email
    return None


def resolve_job_contacts(
    db: Database,
    company_id: int,
    domain: str,
    job_url: str = None,
) -> List[ContactCandidate]:
    del company_id
    if not job_url:
        return []

    candidates: List[ContactCandidate] = []
    html = _fetch_page(job_url)
    if not html:
        return []

    for email in _extract_emails_from_html(html, domain):
        candidates.append(ContactCandidate(
            name=None,
            email=email,
            role="recruiter (from posting)",
            confidence_tier="public_exact",
            contact_source_type="job_posting_email",
            source_url=job_url,
            evidence_snippet=f"Email found on job posting: {email}",
        ))

    for name, snippet in _extract_names_from_html(html):
        first, last = _split_name(name)
        if not first or not last:
            continue
        candidates.append(ContactCandidate(
            name=name,
            email=None,
            role="recruiter (from posting)",
            confidence_tier="name_found",
            contact_source_type="job_posting_name",
            source_url=job_url,
            evidence_snippet=snippet,
        ))

    _resolve_named_candidates(db, domain, candidates)
    for candidate in candidates:
        _record_pattern_from_candidate(db, domain, candidate)
    return _dedupe_candidates(candidates)


def resolve_company_contacts(
    db: Database,
    company_id: int,
    domain: str,
    careers_url: str = None,
) -> List[ContactCandidate]:
    del company_id, careers_url
    candidates: List[ContactCandidate] = []

    for discovered_contact in db.get_discovered_contacts(domain):
        confidence_tier = "public_exact" if discovered_contact["email"] else "name_found"
        evidence = discovered_contact["evidence_snippet"] or "Contact from discovery source"
        candidate = ContactCandidate(
            name=discovered_contact["name"],
            email=discovered_contact["email"],
            role=discovered_contact["role"],
            confidence_tier=confidence_tier,
            contact_source_type="discovery_source",
            source_url=discovered_contact["source_url"],
            evidence_snippet=evidence,
        )
        candidates.append(candidate)
        _record_pattern_from_candidate(db, domain, candidate)

    base_url = f"https://{domain}"
    for path in TEAM_PAGE_PATHS:
        url = f"{base_url}{path}"
        html = _fetch_page(url)
        if not html:
            continue

        for name, role, evidence in _extract_people_from_team_page(html):
            if not _is_recruiting_role(role):
                continue
            first, last = _split_name(name)
            if not first or not last:
                continue
            candidates.append(ContactCandidate(
                name=name,
                email=None,
                role=role,
                confidence_tier="name_found",
                contact_source_type="team_page",
                source_url=url,
                evidence_snippet=evidence,
            ))

        for email in _extract_emails_from_html(html, domain):
            candidates.append(ContactCandidate(
                name=None,
                email=email,
                role=None,
                confidence_tier="public_exact",
                contact_source_type="team_page_email",
                source_url=url,
                evidence_snippet=f"Email found on team page: {email}",
            ))

    for path in CONTACT_PAGE_PATHS:
        url = f"{base_url}{path}"
        html = _fetch_page(url)
        if not html:
            continue

        for email in _extract_emails_from_html(html, domain):
            local_part = email.split("@")[0]
            if local_part in GENERIC_INBOXES:
                candidates.append(ContactCandidate(
                    name=None,
                    email=email,
                    role=None,
                    confidence_tier="public_generic_inbox",
                    contact_source_type="contact_page",
                    source_url=url,
                    evidence_snippet=f"Generic inbox found on contact page: {email}",
                ))
            else:
                candidates.append(ContactCandidate(
                    name=None,
                    email=email,
                    role=None,
                    confidence_tier="public_exact",
                    contact_source_type="contact_page_email",
                    source_url=url,
                    evidence_snippet=f"Email found on contact page: {email}",
                ))

    _resolve_named_candidates(db, domain, candidates)
    for candidate in candidates:
        _record_pattern_from_candidate(db, domain, candidate)

    if not any(candidate.email for candidate in candidates):
        verified_generic = _try_generic_inboxes(domain)
        if verified_generic:
            candidates.append(ContactCandidate(
                name=None,
                email=verified_generic,
                role=None,
                confidence_tier="public_generic_inbox",
                contact_source_type="smtp_verified_generic",
                source_url=None,
                evidence_snippet=f"Generic inbox verified via SMTP: {verified_generic}",
            ))

    return _dedupe_candidates(candidates)


def run(config, db: Database, dry_run: bool = False) -> int:
    del config, dry_run
    total_resolved = 0
    tier_counts = defaultdict(int)
    qualified_jobs = db.get_qualified_jobs("qualified_auto") + db.get_qualified_jobs("qualified_review")

    company_jobs = {}
    for job in qualified_jobs:
        company_jobs.setdefault(job["company_id"], []).append(job)

    for company_id, jobs in company_jobs.items():
        company = db.get_company(company_id)
        if not company:
            continue

        domain = company["domain"]
        if db.check_suppression(domain=domain, company_name=company["name"]):
            logger.info("[%s] Suppressed, skipping", domain)
            continue

        company_contacts = db.get_pending_contacts(company_id, job_id=None)
        if not company_contacts:
            logger.info("[%s] Resolving company-level contacts for %s", domain, company["name"])
            company_candidates = resolve_company_contacts(
                db,
                company_id,
                domain,
                careers_url=company["careers_url"],
            )
            for candidate in company_candidates:
                db.insert_person(
                    company_id=company_id,
                    job_id=None,
                    name=candidate.name,
                    email=candidate.email,
                    role=candidate.role,
                    confidence_tier=candidate.confidence_tier,
                    contact_source_type=candidate.contact_source_type,
                    source_url=candidate.source_url,
                    evidence_snippet=candidate.evidence_snippet,
                )
                total_resolved += 1
                tier_counts[candidate.confidence_tier] += 1

        for job in jobs:
            if db.get_contacts_for_job(job["id"]):
                continue

            logger.info("[%s] Resolving job-level contacts for %s", domain, job["title"])
            job_candidates = resolve_job_contacts(
                db,
                company_id,
                domain,
                job_url=job["url"],
            )
            for candidate in job_candidates:
                db.insert_person(
                    company_id=company_id,
                    job_id=job["id"],
                    name=candidate.name,
                    email=candidate.email,
                    role=candidate.role,
                    confidence_tier=candidate.confidence_tier,
                    contact_source_type=candidate.contact_source_type,
                    source_url=candidate.source_url,
                    evidence_snippet=candidate.evidence_snippet,
                )
                total_resolved += 1
                tier_counts[candidate.confidence_tier] += 1

    if total_resolved > 0:
        db.increment_metric("contacts_resolved", total_resolved)
        for metric_field, tier_name in [
            ("contacts_public_exact", "public_exact"),
            ("contacts_pattern_verified", "pattern_verified"),
            ("contacts_generic_inbox", "public_generic_inbox"),
        ]:
            count = tier_counts.get(tier_name, 0)
            if count:
                db.increment_metric(metric_field, count)

    logger.info("Resolved %d contacts", total_resolved)
    return total_resolved
