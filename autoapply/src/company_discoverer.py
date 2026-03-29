"""
Company discovery pipeline for AutoApply V2.2.

Finds actively hiring companies from high-signal sources, detects their ATS,
and promotes them into the DB-backed active company set.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.config import Config, DomainProfile
from src.db import Database
from src.job_discoverer import REQUEST_HEADERS
from src.utils import RateLimiter, get_logger, retry


logger = get_logger("company_discoverer")

YC_HOME_URL = "https://www.workatastartup.com/"
YC_COMPANY_URL = "https://www.workatastartup.com/companies/{slug}"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{story_id}"

BUILTIN_CITY_URLS = {
    "nyc": "https://www.builtinnyc.com",
    "sf": "https://www.builtinsf.com",
    "chicago": "https://www.builtinchicago.org",
    "boston": "https://www.builtinboston.com",
    "la": "https://www.builtinla.com",
}

ATS_HOST_MARKERS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
)

SOCIAL_HOST_MARKERS = (
    "linkedin.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
    "glassdoor.com",
)

NOISE_HOST_MARKERS = (
    "forbes.com",
    "medium.com",
    "substack.com",
    "notion.site",
    "docs.google.com",
)

FINANCE_KEYWORDS = (
    "fintech",
    "financial",
    "payments",
    "banking",
    "capital markets",
    "investment",
    "wealth",
    "insurance",
    "insurtech",
    "trading",
    "asset management",
    "credit",
    "lending",
    "brokerage",
)

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

BUILTIN_LIST_PAGES = 3

_yc_limiter = RateLimiter(rate=0.5, capacity=1)
_builtin_limiter = RateLimiter(rate=(1 / 3), capacity=1)
_ats_limiter = RateLimiter(rate=0.5, capacity=1)
_hn_limiter = RateLimiter(rate=1, capacity=2)


@dataclass
class RawCompany:
    name: str
    domain: str
    source: str
    source_url: str
    description: Optional[str] = None
    industry: Optional[str] = None
    headcount_range: Optional[str] = None
    hq_location: Optional[str] = None
    tech_stack: Optional[str] = None


@dataclass
class ATSInfo:
    ats_type: str
    slug: str
    careers_url: Optional[str]
    jobs_url: Optional[str]
    workday_instance: str = ""
    workday_board: str = ""


@dataclass
class DiscoverySummary:
    scraped: int = 0
    inserted: int = 0
    detected: int = 0
    promoted: int = 0
    skipped_fresh: int = 0
    dry_run_companies: Optional[List[Dict[str, Optional[str]]]] = None


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _get_response(url: str, timeout: int = 20) -> requests.Response:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _post_json(url: str, body: Optional[dict] = None, timeout: int = 20) -> dict:
    response = requests.post(url, headers=REQUEST_HEADERS, json=body or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _get_json(url: str, params: Optional[dict] = None, timeout: int = 20) -> dict:
    response = requests.get(url, headers=REQUEST_HEADERS, params=params or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_domain(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = value.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or parsed.path or "").strip().lower()
    if "/" in host:
        host = host.split("/", 1)[0]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    return host


def _is_ats_host(domain: str) -> bool:
    return any(marker in domain for marker in ATS_HOST_MARKERS)


def _extract_inertia_page_data(html_text: str) -> dict:
    match = re.search(r'data-page=["\']([^"\']+)["\']', html_text)
    if not match:
        raise ValueError("Inertia page data not found")
    return json.loads(html.unescape(match.group(1)))


def _hash_companies(companies: Sequence[RawCompany]) -> str:
    joined = "|".join(sorted(company.domain for company in companies))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _matches_domain_profile(text: str, domain_profile: DomainProfile) -> bool:
    if domain_profile.name.lower() != "finance":
        return True
    lowered = text.lower()
    return any(keyword in lowered for keyword in FINANCE_KEYWORDS)


def _is_recent(row, freshness_days: int) -> bool:
    if not row:
        return False
    scraped_at = row["scraped_at"]
    try:
        when = datetime.fromisoformat(scraped_at)
    except ValueError:
        return False
    return when >= datetime.now() - timedelta(days=freshness_days)


def _extract_external_company_url(profile_html: str) -> Optional[str]:
    soup = BeautifulSoup(profile_html, "html.parser")
    candidates: List[str] = []
    for anchor in soup.find_all("a", href=True):
        href = html.unescape(anchor["href"]).strip()
        if not href.startswith("http"):
            continue
        domain = _normalize_domain(href)
        if not domain:
            continue
        if "builtin" in domain or "cdn.builtin.com" in domain:
            continue
        if any(marker in domain for marker in SOCIAL_HOST_MARKERS):
            continue
        if any(marker in href.lower() for marker in ("auth/login", "/jobs?", "/companies?")):
            continue
        if "utm_source=BuiltIn" in href or "utm_medium=BuiltIn" in href:
            return href
        candidates.append(href)
    return candidates[0] if candidates else None


def _extract_builtin_field(profile_html: str, labels: Sequence[str]) -> Optional[str]:
    soup = BeautifulSoup(profile_html, "html.parser")
    for label in labels:
        label_node = soup.find(string=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
        if not label_node:
            continue
        parent = label_node.parent
        if not parent:
            continue
        sibling = parent.find_next_sibling()
        if sibling:
            value = _clean_text(sibling.get_text(" ", strip=True))
            if value:
                return value

    text = _clean_text(soup.get_text(" ", strip=True))
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[:\-]?\s*([A-Za-z0-9 ,&+/().-]{{2,80}})",
            text,
            re.IGNORECASE,
        )
        if match:
            return _clean_text(match.group(1))
    return None


def _extract_builtin_metadata(profile_html: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(profile_html, "html.parser")
    description_el = soup.select_one("meta[name='description']")
    description = description_el.get("content", "").strip() if description_el else None
    return {
        "industry": _extract_builtin_field(profile_html, ["Industry"]),
        "headcount_range": _extract_builtin_field(profile_html, ["Company Size", "Size"]),
        "hq_location": _extract_builtin_field(profile_html, ["Location", "Headquarters"]),
        "description": description or None,
        "tech_stack": None,
    }


def _hn_has_discovery_keyword(text: str, domain_profile: DomainProfile) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in domain_profile.discovery_keywords)


def _extract_hn_company_name(comment_text: str) -> Optional[str]:
    for line in comment_text.splitlines():
        candidate = _clean_text(line)
        if len(candidate) < 2 or len(candidate) > 120:
            continue
        if "http" in candidate.lower() or "www." in candidate.lower():
            continue
        candidate = re.split(r"\s+[|:-]\s+", candidate, maxsplit=1)[0].strip()
        candidate = re.sub(r"^\*+|\*+$", "", candidate).strip()
        if "/" in candidate or ".com" in candidate.lower():
            continue
        if candidate and len(candidate.split()) <= 4:
            return candidate
    return None


def _extract_hn_contact_name(comment_text: str) -> Optional[str]:
    patterns = [
        re.compile(r"(?:contact|reach out to|email)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"),
        re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*[\-–|]\s*(?:recruiter|hiring|talent|people)", re.I),
    ]
    for pattern in patterns:
        match = pattern.search(comment_text)
        if match:
            return _clean_text(match.group(1))
    return None


def discover_from_hn_hiring(config: Config, db: Database, persist_contacts: bool = True) -> List[RawCompany]:
    _hn_limiter.acquire()
    search_payload = _get_json(
        HN_SEARCH_URL,
        params={
            "query": '"who is hiring"',
            "tags": "story,ask_hn",
            "hitsPerPage": 3,
        },
    )
    hits = search_payload.get("hits", [])
    if not hits:
        return []

    hits = sorted(hits, key=lambda item: item.get("created_at_i", 0), reverse=True)
    story_id = hits[0].get("objectID")
    if not story_id:
        return []

    _hn_limiter.acquire()
    item_payload = _get_json(HN_ITEM_URL.format(story_id=story_id))
    discovered: Dict[str, RawCompany] = {}
    inserted = 0

    for child in item_payload.get("children", []):
        if inserted >= config.discovery.hn_max_per_run:
            break

        raw_html = child.get("text") or ""
        comment_text = BeautifulSoup(raw_html, "html.parser").get_text("\n", strip=True)
        if len(comment_text) < 50:
            continue
        if not _hn_has_discovery_keyword(comment_text, config.domain_profile):
            continue
        if not _matches_domain_profile(comment_text, config.domain_profile):
            continue

        soup = BeautifulSoup(raw_html, "html.parser")
        candidate_urls = []
        for anchor in soup.find_all("a", href=True):
            candidate_urls.append(anchor["href"])
        for url in re.findall(r"https?://[^\s<>()]+", comment_text):
            candidate_urls.append(url)

        domain = None
        for url in candidate_urls:
            normalized = _normalize_domain(url)
            if not normalized or _is_ats_host(normalized):
                continue
            if any(marker in normalized for marker in SOCIAL_HOST_MARKERS):
                continue
            if any(marker in normalized for marker in NOISE_HOST_MARKERS):
                continue
            domain = normalized
            break
        if not domain:
            continue
        if db.get_company_by_domain(domain) or db.get_discovered_company_by_domain(domain):
            continue

        company_name = _extract_hn_company_name(comment_text)
        if not company_name:
            continue

        source_url = f"https://news.ycombinator.com/item?id={child.get('id')}"
        discovered[domain] = RawCompany(
            name=company_name,
            domain=domain,
            source="hn",
            source_url=source_url,
            description=_clean_text(comment_text[:300]),
        )

        emails = [
            email.lower() for email in EMAIL_REGEX.findall(comment_text)
            if email.lower().endswith(f"@{domain}")
        ]
        contact_name = _extract_hn_contact_name(comment_text)
        if persist_contacts:
            for email in emails[:2]:
                db.insert_discovered_contact(
                    domain=domain,
                    name=contact_name,
                    email=email,
                    role="recruiting",
                    source="hn",
                    source_url=source_url,
                    evidence_snippet=_clean_text(comment_text[:250]),
                )
            if contact_name and not emails:
                db.insert_discovered_contact(
                    domain=domain,
                    name=contact_name,
                    email=None,
                    role="recruiting",
                    source="hn",
                    source_url=source_url,
                    evidence_snippet=_clean_text(comment_text[:250]),
                )

        inserted += 1

    return list(discovered.values())


def _validate_company_domain(domain: str, timeout: int = 10) -> bool:
    normalized = _normalize_domain(domain)
    if not normalized or _is_ats_host(normalized):
        return False

    for url in (f"https://{normalized}", f"http://{normalized}"):
        try:
            response = requests.head(url, headers=REQUEST_HEADERS, timeout=timeout, allow_redirects=True)
            if response.status_code < 500:
                return True
        except requests.RequestException:
            continue
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout, allow_redirects=True)
            if response.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def _iter_ats_candidates(page_url: str, html_text: str) -> Iterable[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    yield page_url

    for tag_name, attr in (("a", "href"), ("iframe", "src"), ("script", "src")):
        for tag in soup.find_all(tag_name):
            value = tag.get(attr)
            if value:
                yield urljoin(page_url, value)

    for meta in soup.find_all("meta"):
        if meta.get("http-equiv", "").lower() == "refresh":
            content = meta.get("content", "")
            match = re.search(r"url=(.+)$", content, re.IGNORECASE)
            if match:
                yield urljoin(page_url, match.group(1).strip())

    for match in re.finditer(r"""(?:window\.location|location\.href)\s*=\s*['"]([^'"]+)['"]""", html_text):
        yield urljoin(page_url, match.group(1))


def _match_ats(value: str, careers_url: str) -> Optional[ATSInfo]:
    patterns = [
        ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)")),
        ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")),
        ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)")),
        ("workday", re.compile(r"([a-zA-Z0-9_-]+)\.wd(\d+)\.myworkdayjobs\.com.*?/([a-zA-Z0-9_-]+)")),
        ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)")),
    ]
    for ats_type, pattern in patterns:
        match = pattern.search(value)
        if not match:
            continue
        if ats_type == "workday":
            slug, instance_number, board = match.groups()
            return ATSInfo(
                ats_type="workday",
                slug=slug,
                careers_url=careers_url,
                jobs_url=value,
                workday_instance=f"wd{instance_number}",
                workday_board=board,
            )
        return ATSInfo(
            ats_type=ats_type,
            slug=match.group(1),
            careers_url=careers_url,
            jobs_url=value,
        )
    return None


def _verify_ats(info: ATSInfo) -> bool:
    try:
        if info.ats_type == "greenhouse":
            data = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{info.slug}/jobs",
                headers=REQUEST_HEADERS,
                params={"content": "true"},
                timeout=10,
            )
            data.raise_for_status()
            payload = data.json()
            return isinstance(payload.get("jobs"), list)

        if info.ats_type == "lever":
            data = requests.get(
                f"https://api.lever.co/v0/postings/{info.slug}",
                headers=REQUEST_HEADERS,
                params={"mode": "json"},
                timeout=10,
            )
            data.raise_for_status()
            return isinstance(data.json(), list)

        if info.ats_type == "ashby":
            data = requests.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{info.slug}",
                headers=REQUEST_HEADERS,
                timeout=10,
            )
            data.raise_for_status()
            payload = data.json()
            return isinstance(payload, dict)

        if info.ats_type == "workday":
            base_url = (
                f"https://{info.slug}.{info.workday_instance}.myworkdayjobs.com"
                f"/wday/cxs/{info.slug}/{info.workday_board}"
            )
            payload = _post_json(
                f"{base_url}/jobs",
                {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                timeout=10,
            )
            return isinstance(payload.get("jobPostings"), list)

        if info.ats_type == "smartrecruiters":
            data = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{info.slug}/postings",
                headers=REQUEST_HEADERS,
                timeout=10,
            )
            data.raise_for_status()
            payload = data.json()
            return isinstance(payload.get("content"), list)
    except (requests.RequestException, ValueError):
        return False
    return False


def detect_ats(domain: str) -> Optional[ATSInfo]:
    normalized = _normalize_domain(domain)
    if not normalized:
        return None

    candidate_paths = ("/careers", "/jobs", "/join", "/join-us", "/careers/open-positions", "")
    seen_urls = set()

    for path in candidate_paths:
        url = f"https://{normalized}{path}"
        if url in seen_urls:
            continue
        seen_urls.add(url)
        _ats_limiter.acquire()
        try:
            response = _get_response(url, timeout=10)
        except requests.RequestException:
            continue

        page_url = response.url
        for candidate in _iter_ats_candidates(page_url, response.text):
            info = _match_ats(candidate, careers_url=page_url)
            if info and _verify_ats(info):
                return info
        info = _match_ats(response.text, careers_url=page_url)
        if info and _verify_ats(info):
            return info
    return None


def discover_from_yc(domain_profile: DomainProfile) -> List[RawCompany]:
    home_html = _get_response(YC_HOME_URL).text
    payload = _extract_inertia_page_data(home_html)
    jobs = payload.get("props", {}).get("jobs", [])

    companies: Dict[str, RawCompany] = {}
    for job in jobs:
        company_slug = job.get("companySlug")
        company_name = (job.get("companyName") or "").strip()
        if not company_slug or not company_name:
            continue

        company_page = YC_COMPANY_URL.format(slug=company_slug)
        _yc_limiter.acquire()
        try:
            profile_html = _get_response(company_page).text
            profile_payload = _extract_inertia_page_data(profile_html)
        except (requests.RequestException, ValueError) as exc:
            logger.debug("[yc] failed to load %s: %s", company_page, exc)
            continue

        company_data = profile_payload.get("props", {}).get("company", {})
        website_url = company_data.get("url")
        domain = _normalize_domain(website_url)
        if not domain:
            continue

        text_parts = [
            company_data.get("description") or "",
            company_data.get("industry") or "",
            BeautifulSoup(company_data.get("hiringDescriptionHtml") or "", "html.parser").get_text(" ", strip=True),
            BeautifulSoup(company_data.get("techDescriptionHtml") or "", "html.parser").get_text(" ", strip=True),
        ]
        for active_job in company_data.get("jobs", []):
            text_parts.append(active_job.get("title") or "")
        if not _matches_domain_profile(" ".join(text_parts), domain_profile):
            continue

        companies[domain] = RawCompany(
            name=company_data.get("name") or company_name,
            domain=domain,
            source="yc",
            source_url=company_page,
            description=_clean_text(company_data.get("description") or "") or None,
            industry=_clean_text(company_data.get("industry") or "") or None,
            headcount_range=_clean_text(
                str(
                    company_data.get("teamSize")
                    or company_data.get("teamSizeLabel")
                    or company_data.get("teamSizeRange")
                    or ""
                )
            ) or None,
            hq_location=_clean_text(company_data.get("location") or "") or None,
            tech_stack=None,
        )

    return list(companies.values())


def discover_from_builtin(cities: Sequence[str], domain_profile: DomainProfile) -> List[RawCompany]:
    discovered: Dict[str, RawCompany] = {}
    seen_profiles = set()

    for city in cities:
        city_key = city.strip().lower()
        base_url = BUILTIN_CITY_URLS.get(city_key)
        if not base_url:
            logger.warning("[builtin] unsupported city '%s'", city)
            continue

        for page in range(1, BUILTIN_LIST_PAGES + 1):
            page_url = f"{base_url}/companies" if page == 1 else f"{base_url}/companies?country=USA&page={page}"
            _builtin_limiter.acquire()
            try:
                html_text = _get_response(page_url).text
            except requests.RequestException as exc:
                logger.debug("[builtin] failed to load %s: %s", page_url, exc)
                break

            soup = BeautifulSoup(html_text, "html.parser")
            cards = soup.select("div.company-card-horizontal")
            if not cards:
                break

            for card in cards:
                card_text = _clean_text(card.get_text(" ", strip=True))
                if "hiring now" not in card_text.lower():
                    continue

                overlay = card.select_one("a.company-card-overlay[href]")
                name_el = card.select_one("h2")
                if not overlay or not name_el:
                    continue

                profile_url = urljoin(base_url, overlay["href"])
                if profile_url in seen_profiles:
                    continue
                seen_profiles.add(profile_url)

                _builtin_limiter.acquire()
                try:
                    profile_html = _get_response(profile_url).text
                except requests.RequestException as exc:
                    logger.debug("[builtin] failed profile %s: %s", profile_url, exc)
                    continue

                website_url = _extract_external_company_url(profile_html)
                domain = _normalize_domain(website_url)
                if not domain:
                    continue

                profile_text = _clean_text(BeautifulSoup(profile_html, "html.parser").get_text(" ", strip=True))
                if not _matches_domain_profile(f"{card_text} {profile_text}", domain_profile):
                    continue

                metadata = _extract_builtin_metadata(profile_html)

                discovered[domain] = RawCompany(
                    name=_clean_text(name_el.get_text(" ", strip=True)),
                    domain=domain,
                    source=f"builtin_{city_key}",
                    source_url=profile_url,
                    description=metadata["description"],
                    industry=metadata["industry"],
                    headcount_range=metadata["headcount_range"],
                    hq_location=metadata["hq_location"],
                    tech_stack=metadata["tech_stack"],
                )

    return list(discovered.values())


def _dedupe_new_companies(db: Database, companies: Sequence[RawCompany]) -> List[RawCompany]:
    unique: Dict[str, RawCompany] = {}
    for company in companies:
        domain = _normalize_domain(company.domain)
        if not domain:
            continue
        if db.get_company_by_domain(domain):
            logger.debug("Dedup: %s already in pipeline", domain)
            continue
        if db.get_discovered_company_by_domain(domain):
            logger.debug("Dedup: %s already discovered", domain)
            continue
        if db.check_suppression(domain=domain):
            logger.debug("Dedup: %s in suppression list", domain)
            continue
        unique[domain] = RawCompany(
            name=company.name,
            domain=domain,
            source=company.source,
            source_url=company.source_url,
            description=company.description,
            industry=company.industry,
            headcount_range=company.headcount_range,
            hq_location=company.hq_location,
            tech_stack=company.tech_stack,
        )
    return list(unique.values())


def _persist_discovered_company(db: Database, company: RawCompany, priority: int) -> Optional[int]:
    return db.insert_discovered_company(
        name=company.name,
        domain=company.domain,
        source=company.source,
        source_url=company.source_url,
        priority=priority,
        description=company.description,
        industry=company.industry,
        headcount_range=company.headcount_range,
        hq_location=company.hq_location,
        tech_stack=company.tech_stack,
    )


def _promote_ready_companies(db: Database, dry_run: bool = False) -> int:
    promoted = 0
    for row in db.get_unpromoted_companies("detected"):
        if not _validate_company_domain(row["domain"]):
            if not dry_run:
                db.update_ats_info(row["id"])
            continue
        if dry_run:
            promoted += 1
            continue
        if db.promote_company(row["id"]):
            promoted += 1
    return promoted


def run(config: Config, db: Database, *, sources: Sequence[str], dry_run: bool = False,
        cities: Optional[Sequence[str]] = None, freshness_days: int = 7,
        promote_only: bool = False) -> DiscoverySummary:
    summary = DiscoverySummary(dry_run_companies=[])

    normalized_sources = [source.strip().lower() for source in sources if source.strip()]
    if not normalized_sources or "all" in normalized_sources:
        normalized_sources = ["yc", "builtin"]
        if config.discovery.hn_enabled:
            normalized_sources.append("hn")

    builtin_cities = list(cities or config.discovery.builtin_cities)

    if promote_only:
        summary.promoted = _promote_ready_companies(db, dry_run=dry_run)
        return summary

    for source_name in normalized_sources:
        if source_name == "yc":
            source_url = YC_HOME_URL
            if _is_recent(db.get_last_scrape(source_name, source_url), freshness_days):
                logger.info("Skipping YC — scraped within last %d days", freshness_days)
                summary.skipped_fresh += 1
                continue
            raw_companies = discover_from_yc(config.domain_profile)
            summary.scraped += len(raw_companies)
            new_companies = _dedupe_new_companies(db, raw_companies)
            if dry_run:
                for company in new_companies:
                    info = detect_ats(company.domain)
                    summary.dry_run_companies.append({
                        "name": company.name,
                        "domain": company.domain,
                        "source": company.source,
                        "ats": info.ats_type if info else None,
                    })
                    if info:
                        summary.detected += 1
                continue
            for company in new_companies:
                inserted = _persist_discovered_company(db, company, priority=2)
                if inserted:
                    summary.inserted += 1
            db.log_scrape(source_name, source_url, len(raw_companies), _hash_companies(raw_companies))
            continue

        if source_name == "builtin":
            for city in builtin_cities:
                city_key = city.strip().lower()
                base_url = BUILTIN_CITY_URLS.get(city_key)
                if not base_url:
                    continue
                source_url = f"{base_url}/companies"
                source_label = f"builtin_{city_key}"
                if _is_recent(db.get_last_scrape(source_label, source_url), freshness_days):
                    logger.info("Skipping BuiltIn %s — scraped within last %d days", city_key, freshness_days)
                    summary.skipped_fresh += 1
                    continue
                raw_companies = discover_from_builtin([city_key], config.domain_profile)
                summary.scraped += len(raw_companies)
                new_companies = _dedupe_new_companies(db, raw_companies)
                if dry_run:
                    for company in new_companies:
                        info = detect_ats(company.domain)
                        summary.dry_run_companies.append({
                            "name": company.name,
                            "domain": company.domain,
                            "source": company.source,
                            "ats": info.ats_type if info else None,
                        })
                        if info:
                            summary.detected += 1
                    continue
                for company in new_companies:
                    inserted = _persist_discovered_company(db, company, priority=3)
                    if inserted:
                        summary.inserted += 1
                db.log_scrape(source_label, source_url, len(raw_companies), _hash_companies(raw_companies))
            continue

        if source_name == "hn":
            source_url = "https://news.ycombinator.com/ask"
            if _is_recent(db.get_last_scrape(source_name, source_url), freshness_days):
                logger.info("Skipping HN hiring thread — scraped within last %d days", freshness_days)
                summary.skipped_fresh += 1
                continue
            raw_companies = discover_from_hn_hiring(config, db, persist_contacts=not dry_run)
            summary.scraped += len(raw_companies)
            new_companies = _dedupe_new_companies(db, raw_companies)
            if dry_run:
                for company in new_companies:
                    info = detect_ats(company.domain)
                    summary.dry_run_companies.append({
                        "name": company.name,
                        "domain": company.domain,
                        "source": company.source,
                        "ats": info.ats_type if info else None,
                    })
                    if info:
                        summary.detected += 1
                continue
            for company in new_companies:
                inserted = _persist_discovered_company(db, company, priority=2)
                if inserted:
                    summary.inserted += 1
            db.log_scrape(source_name, source_url, len(raw_companies), _hash_companies(raw_companies))

    if dry_run:
        return summary

    for row in db.get_pending_ats_check():
        if not _validate_company_domain(row["domain"]):
            logger.warning("Domain validation failed for %s — marking unknown", row["domain"])
            db.update_ats_info(row["id"])
            continue
        info = detect_ats(row["domain"])
        if info:
            db.update_ats_info(
                row["id"],
                ats=info.ats_type,
                slug=info.slug,
                careers_url=info.careers_url,
                jobs_url=info.jobs_url,
                workday_instance=info.workday_instance,
                workday_board=info.workday_board,
            )
            summary.detected += 1
        else:
            db.update_ats_info(row["id"])

    summary.promoted = _promote_ready_companies(db, dry_run=False)
    return summary
