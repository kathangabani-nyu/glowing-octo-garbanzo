"""
Contact discovery cascade for AutoApply V2.2.

For each qualified job's company, resolves the best contact path:
1. Parse posting page for recruiter name/email
2. Scrape /team, /about, /people pages
3. Scrape /contact page
4. If name found: call permutator + smtp_verifier
5. If no name: verify generic inboxes
6. Assign confidence tier

Module owner: Claude Code
"""

import re
import time
from typing import Optional, List, Tuple
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from src.db import Database
from src.permutator import generate_permutations, match_pattern
from src.smtp_verifier import verify_email, check_catch_all, VerificationResult
from src.utils import get_logger, RateLimiter, retry

logger = get_logger("contact_discoverer")

# Rate limiter: max 1 SMTP connection per 2 seconds
_smtp_limiter = RateLimiter(rate=0.5, capacity=1)

# Generic inbox names to try, ordered by likelihood for recruiting
GENERIC_INBOXES = [
    "careers", "recruiting", "jobs", "talent", "hr",
    "hiring", "people", "team", "apply",
]

# Patterns to find recruiter/contact info in page text
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
)

NAME_TITLE_PATTERNS = [
    # "Posted by Jane Smith" / "Recruiter: Jane Smith"
    re.compile(r"(?:posted\s+by|recruiter|hiring\s+manager|contact)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", re.IGNORECASE),
    # "Jane Smith, Technical Recruiter"
    re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*,\s*(?:technical\s+)?recruiter", re.IGNORECASE),
    # "Jane Smith - Talent Acquisition"
    re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*[\-\|]\s*(?:talent|recruit|hiring|people|hr)", re.IGNORECASE),
]

TEAM_PAGE_PATHS = ["/team", "/about", "/about-us", "/people", "/our-team", "/company"]
CONTACT_PAGE_PATHS = ["/contact", "/contact-us", "/get-in-touch"]


@dataclass
class ContactCandidate:
    name: Optional[str]
    email: Optional[str]
    role: Optional[str]
    confidence_tier: str
    contact_source_type: str
    source_url: str
    evidence_snippet: str


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _fetch_page(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch a page's HTML content. Returns None on failure."""
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
    """Extract email addresses from HTML that match the company domain."""
    all_emails = EMAIL_REGEX.findall(html)
    company_emails = [
        e.lower() for e in all_emails
        if e.lower().endswith(f"@{company_domain}")
    ]
    # Deduplicate preserving order
    seen = set()
    result = []
    for e in company_emails:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def _extract_names_from_html(html: str) -> List[Tuple[str, str]]:
    """Extract (name, evidence_snippet) tuples from HTML using recruiter patterns."""
    results = []
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    for pattern in NAME_TITLE_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            # Get surrounding context as evidence
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            snippet = text[start:end].strip()
            results.append((name, snippet))
    return results


def _extract_people_from_team_page(html: str) -> List[Tuple[str, str, str]]:
    """
    Extract (name, role, evidence) from team/about pages.
    Looks for common patterns: cards, list items with names + titles.
    """
    soup = BeautifulSoup(html, "html.parser")
    people = []

    # Look for common team page structures
    # Pattern 1: h2/h3/h4 + p (name in heading, role in paragraph)
    for heading in soup.find_all(["h2", "h3", "h4"]):
        name_text = heading.get_text(strip=True)
        # Must look like a person name (2-4 capitalized words)
        if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", name_text):
            role = ""
            sibling = heading.find_next_sibling(["p", "span", "div"])
            if sibling:
                role_text = sibling.get_text(strip=True)
                if len(role_text) < 80:
                    role = role_text
            evidence = f"{name_text} - {role}" if role else name_text
            people.append((name_text, role, evidence))

    # Pattern 2: divs/li with class containing "team", "member", "person"
    member_containers = soup.find_all(
        ["div", "li", "article"],
        class_=re.compile(r"team|member|person|staff|employee", re.I)
    )
    for container in member_containers:
        # Look for a name-like heading inside
        name_el = container.find(["h2", "h3", "h4", "strong", "b"])
        if name_el:
            name_text = name_el.get_text(strip=True)
            if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", name_text):
                role = ""
                role_el = container.find(["p", "span"], class_=re.compile(r"title|role|position", re.I))
                if role_el:
                    role = role_el.get_text(strip=True)
                evidence = f"{name_text} - {role}" if role else name_text
                if (name_text, role, evidence) not in people:
                    people.append((name_text, role, evidence))

    return people


def _is_recruiting_role(role: str) -> bool:
    """Check if a role title suggests a recruiter or hiring-related person."""
    keywords = [
        "recruit", "talent", "hiring", "people", "hr",
        "human resource", "staffing", "acquisition",
    ]
    role_lower = role.lower()
    return any(kw in role_lower for kw in keywords)


def _is_engineering_role(role: str, job_family: str = None) -> bool:
    """Check if a role title suggests engineering leadership."""
    keywords = [
        "engineering manager", "eng manager", "director of engineering",
        "head of engineering", "vp engineering", "cto",
        "hiring manager", "team lead", "tech lead",
    ]
    role_lower = role.lower()
    return any(kw in role_lower for kw in keywords)


def _split_name(full_name: str) -> Tuple[str, str]:
    """Split a full name into (first, last). Handles 2+ word names."""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return (parts[0], "") if parts else ("", "")
    return (parts[0], parts[-1])


def _try_verify_permutations(
    first_name: str, last_name: str, domain: str
) -> Optional[Tuple[str, str]]:
    """
    Generate permutations, verify via SMTP.
    Returns (verified_email, pattern) or None.
    """
    candidates = generate_permutations(first_name, last_name, domain)
    for email in candidates:
        _smtp_limiter.acquire()
        result = verify_email(email)
        if result.status == "verified":
            pattern = match_pattern(email, first_name, last_name)
            return (email, pattern or "unknown")
        elif result.status == "rejected":
            continue
        else:
            # timeout/error/greylisted — don't keep trying
            break
    return None


def _try_generic_inboxes(domain: str) -> Optional[str]:
    """Try verifying generic recruiting inboxes."""
    for inbox_name in GENERIC_INBOXES:
        email = f"{inbox_name}@{domain}"
        _smtp_limiter.acquire()
        result = verify_email(email)
        if result.status == "verified":
            return email
    return None


def resolve_contact_for_company(
    db: Database, company_id: int, domain: str,
    job_url: str = None, careers_url: str = None,
) -> List[ContactCandidate]:
    """
    Execute the contact discovery cascade for a single company.

    Returns a list of ContactCandidates found, ordered by confidence.
    """
    candidates: List[ContactCandidate] = []

    # Check domain_patterns cache first — if we know the pattern, shortcut
    cached_pattern = db.get_domain_pattern(domain)
    is_catch_all = False

    if cached_pattern and cached_pattern["is_catch_all"]:
        is_catch_all = True
        logger.info(f"[{domain}] Known catch-all domain")

    # ── Step 1: Parse the job posting page for recruiter info ──
    if job_url:
        logger.debug(f"[{domain}] Checking job page: {job_url}")
        html = _fetch_page(job_url)
        if html:
            # Look for direct email addresses
            emails = _extract_emails_from_html(html, domain)
            for email in emails:
                candidates.append(ContactCandidate(
                    name=None, email=email, role="recruiter (from posting)",
                    confidence_tier="public_exact",
                    contact_source_type="job_posting_email",
                    source_url=job_url,
                    evidence_snippet=f"Email found on job posting: {email}",
                ))

            # Look for recruiter names
            names = _extract_names_from_html(html)
            for name, snippet in names:
                first, last = _split_name(name)
                if first and last:
                    candidates.append(ContactCandidate(
                        name=name, email=None, role="recruiter (from posting)",
                        confidence_tier="name_found",
                        contact_source_type="job_posting_name",
                        source_url=job_url,
                        evidence_snippet=snippet,
                    ))

    # ── Step 2: Scrape /team, /about, /people pages ──
    base_url = f"https://{domain}"
    for path in TEAM_PAGE_PATHS:
        url = f"{base_url}{path}"
        logger.debug(f"[{domain}] Checking team page: {url}")
        html = _fetch_page(url)
        if html:
            people = _extract_people_from_team_page(html)
            for name, role, evidence in people:
                if _is_recruiting_role(role):
                    first, last = _split_name(name)
                    if first and last:
                        candidates.append(ContactCandidate(
                            name=name, email=None, role=role,
                            confidence_tier="name_found",
                            contact_source_type="team_page",
                            source_url=url,
                            evidence_snippet=evidence,
                        ))

            # Also check for emails on team pages
            emails = _extract_emails_from_html(html, domain)
            for email in emails:
                candidates.append(ContactCandidate(
                    name=None, email=email, role=None,
                    confidence_tier="public_exact",
                    contact_source_type="team_page_email",
                    source_url=url,
                    evidence_snippet=f"Email found on team page: {email}",
                ))

    # ── Step 3: Scrape /contact page ──
    for path in CONTACT_PAGE_PATHS:
        url = f"{base_url}{path}"
        logger.debug(f"[{domain}] Checking contact page: {url}")
        html = _fetch_page(url)
        if html:
            emails = _extract_emails_from_html(html, domain)
            for email in emails:
                local_part = email.split("@")[0]
                if local_part in GENERIC_INBOXES:
                    candidates.append(ContactCandidate(
                        name=None, email=email, role=None,
                        confidence_tier="public_generic_inbox",
                        contact_source_type="contact_page",
                        source_url=url,
                        evidence_snippet=f"Generic inbox found on contact page: {email}",
                    ))
                else:
                    candidates.append(ContactCandidate(
                        name=None, email=email, role=None,
                        confidence_tier="public_exact",
                        contact_source_type="contact_page_email",
                        source_url=url,
                        evidence_snippet=f"Email found on contact page: {email}",
                    ))

    # ── Step 4: For named contacts without emails, try permutator + SMTP ──
    named_candidates = [c for c in candidates if c.name and not c.email]
    for candidate in named_candidates:
        first, last = _split_name(candidate.name)
        if not first or not last:
            continue

        # Check if we have a cached pattern for this domain
        if cached_pattern and not is_catch_all:
            from src.permutator import apply_pattern
            guessed_email = apply_pattern(
                cached_pattern["pattern"], first, last, domain
            )
            _smtp_limiter.acquire()
            result = verify_email(guessed_email)
            if result.status == "verified":
                candidate.email = guessed_email
                candidate.confidence_tier = "pattern_verified"
                candidate.evidence_snippet += f" | Verified via cached pattern: {guessed_email}"
                continue

        # Full permutation search
        verified = _try_verify_permutations(first, last, domain)
        if verified:
            email, pattern = verified
            candidate.email = email
            candidate.confidence_tier = "pattern_verified"
            candidate.evidence_snippet += f" | SMTP verified: {email}"
            # Cache the pattern
            db.upsert_domain_pattern(domain, pattern, "smtp_verified")
        elif is_catch_all:
            # Catch-all: we can guess but can't verify
            perms = generate_permutations(first, last, domain)
            if perms:
                candidate.email = perms[0]  # Most likely pattern
                candidate.confidence_tier = "catch_all_guess"
                candidate.evidence_snippet += f" | Catch-all guess: {perms[0]}"
        else:
            # Check for catch-all
            _smtp_limiter.acquire()
            if check_catch_all(domain):
                is_catch_all = True
                db.upsert_domain_pattern(domain, "unknown", "catch_all", is_catch_all=True)
                perms = generate_permutations(first, last, domain)
                if perms:
                    candidate.email = perms[0]
                    candidate.confidence_tier = "catch_all_guess"
                    candidate.evidence_snippet += f" | Catch-all guess: {perms[0]}"
            else:
                # Pattern inferred but unverified
                perms = generate_permutations(first, last, domain)
                if perms:
                    candidate.email = perms[0]
                    candidate.confidence_tier = "pattern_inferred"
                    candidate.evidence_snippet += f" | Inferred (unverified): {perms[0]}"

    # ── Step 5: If no named contacts, try generic inboxes ──
    has_email_candidate = any(c.email for c in candidates)
    if not has_email_candidate:
        logger.debug(f"[{domain}] No named contacts found, trying generic inboxes")
        verified_generic = _try_generic_inboxes(domain)
        if verified_generic:
            candidates.append(ContactCandidate(
                name=None, email=verified_generic, role=None,
                confidence_tier="public_generic_inbox",
                contact_source_type="smtp_verified_generic",
                source_url=None,
                evidence_snippet=f"Generic inbox verified via SMTP: {verified_generic}",
            ))

    # ── Step 6: Filter and deduplicate ──
    # Remove candidates with no email
    candidates = [c for c in candidates if c.email]

    # Deduplicate by email, keeping the highest confidence
    tier_order = {
        "public_exact": 1,
        "public_generic_inbox": 2,
        "pattern_verified": 3,
        "pattern_inferred": 4,
        "catch_all_guess": 5,
        "generic_guess": 6,
        "contact_failed": 7,
    }

    seen_emails = {}
    for c in candidates:
        email_lower = c.email.lower()
        existing = seen_emails.get(email_lower)
        if not existing or tier_order.get(c.confidence_tier, 99) < tier_order.get(existing.confidence_tier, 99):
            seen_emails[email_lower] = c

    final = sorted(
        seen_emails.values(),
        key=lambda c: tier_order.get(c.confidence_tier, 99)
    )

    return final


def run(config, db: Database, dry_run: bool = False) -> int:
    """
    Run contact discovery for all qualified jobs.

    Returns count of contacts resolved.
    """
    total_resolved = 0
    qualified_jobs = db.get_qualified_jobs("qualified_auto") + db.get_qualified_jobs("qualified_review")

    # Group jobs by company to avoid re-scraping
    company_jobs = {}
    for job in qualified_jobs:
        cid = job["company_id"]
        if cid not in company_jobs:
            company_jobs[cid] = []
        company_jobs[cid].append(job)

    for company_id, jobs in company_jobs.items():
        company = db.get_company(company_id)
        if not company:
            continue

        domain = company["domain"]

        # Check suppression
        if db.check_suppression(domain=domain, company_name=company["name"]):
            logger.info(f"[{domain}] Suppressed, skipping")
            continue

        # Check if we already have contacts for this company
        existing = db.get_pending_contacts(company_id)
        if existing:
            logger.info(f"[{domain}] Already has {len(existing)} contacts, skipping")
            continue

        logger.info(f"[{domain}] Resolving contacts for {company['name']}")

        # Use the first job's URL for posting-page scraping
        job_url = jobs[0]["url"] if jobs[0]["url"] else None
        careers_url = company["careers_url"]

        candidates = resolve_contact_for_company(
            db, company_id, domain,
            job_url=job_url, careers_url=careers_url,
        )

        if not candidates:
            logger.warning(f"[{domain}] No contacts found")
            continue

        # Store contacts in DB
        for candidate in candidates:
            db.insert_person(
                company_id=company_id,
                name=candidate.name,
                email=candidate.email,
                role=candidate.role,
                confidence_tier=candidate.confidence_tier,
                contact_source_type=candidate.contact_source_type,
                source_url=candidate.source_url,
                evidence_snippet=candidate.evidence_snippet,
            )
            total_resolved += 1

        logger.info(
            f"[{domain}] Resolved {len(candidates)} contacts "
            f"(best: {candidates[0].confidence_tier})"
        )

    # Update metrics
    if total_resolved > 0:
        db.increment_metric("contacts_resolved", total_resolved)
        # Count by tier
        for tier_field, tier_name in [
            ("contacts_public_exact", "public_exact"),
            ("contacts_pattern_verified", "pattern_verified"),
            ("contacts_generic_inbox", "public_generic_inbox"),
        ]:
            count = sum(
                1 for jobs_list in company_jobs.values()
                for _ in jobs_list  # placeholder
            )
            # This is approximate — real counts come from what was inserted

    return total_resolved
