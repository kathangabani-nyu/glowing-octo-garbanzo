"""
Job discovery for AutoApply V2.2.

Polls public ATS endpoints and simple HTML careers pages for watchlist
companies, inserts new jobs into SQLite, and closes jobs that disappeared.

Module owner: Codex
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.config import Config, DomainProfile, Watchlist, WatchlistCompany
from src.db import Database
from src.utils import get_logger, retry


logger = get_logger("job_discoverer")

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AutoApply/2.2; personal-job-search)",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}

@dataclass
class DiscoveredJob:
    external_job_id: str
    title: str
    url: Optional[str]
    location: Optional[str]
    posting_text: Optional[str]
    job_family: Optional[str]
    source: str


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    response = requests.get(url, headers=REQUEST_HEADERS, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _post_json(url: str, body: Optional[Dict[str, Any]] = None) -> Any:
    response = requests.post(url, headers=REQUEST_HEADERS, json=body or {}, timeout=20)
    response.raise_for_status()
    return response.json()


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _get_text(url: str) -> str:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
    response.raise_for_status()
    return response.text


def _clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = BeautifulSoup(unescape(value), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _extract_location(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "location", "text"):
            if value.get(key):
                return str(value[key]).strip()
        parts = []
        for key in ("city", "region", "country"):
            if value.get(key):
                parts.append(str(value[key]).strip())
        if parts:
            return ", ".join(parts)
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts) or None
    return None


def _infer_job_family(title: str, domain_profile: DomainProfile, fallback: Optional[str] = None) -> str:
    title_lower = title.lower()
    for bucket, keywords in domain_profile.role_buckets.items():
        if any(word in title_lower for word in keywords):
            return bucket
    return fallback or domain_profile.default_bucket


def _stable_external_id(company_domain: str, title: str, location: Optional[str], url: Optional[str]) -> str:
    raw = "|".join([company_domain.lower(), title.strip().lower(), (location or "").strip().lower(), (url or "").strip().lower()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _parse_greenhouse_jobs(data: Any, company: WatchlistCompany, domain_profile: DomainProfile) -> List[DiscoveredJob]:
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    discovered: List[DiscoveredJob] = []
    for job in jobs:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        job_id = str(job.get("id") or _stable_external_id(company.domain, title, None, job.get("absolute_url")))
        discovered.append(DiscoveredJob(
            external_job_id=job_id,
            title=title,
            url=job.get("absolute_url"),
            location=_extract_location(job.get("location")),
            posting_text=_clean_text(job.get("content")),
            job_family=_infer_job_family(title, domain_profile, company.job_family_focus),
            source="greenhouse_api",
        ))
    return discovered


def _flatten_lever_posting_text(job: Dict[str, Any]) -> Optional[str]:
    for key in ("descriptionPlain", "descriptionBodyPlain", "description"):
        text = _clean_text(job.get(key))
        if text:
            return text

    lists = job.get("lists") or []
    parts: List[str] = []
    for item in lists:
        if not isinstance(item, dict):
            continue
        if item.get("text"):
            parts.append(str(item["text"]))
        if item.get("content"):
            cleaned = _clean_text(item["content"])
            if cleaned:
                parts.append(cleaned)
    return " ".join(parts) if parts else None


def _parse_lever_jobs(data: Any, company: WatchlistCompany, domain_profile: DomainProfile) -> List[DiscoveredJob]:
    jobs = data if isinstance(data, list) else []
    discovered: List[DiscoveredJob] = []
    for job in jobs:
        title = (job.get("text") or job.get("title") or "").strip()
        if not title:
            continue
        categories = job.get("categories") or {}
        url = job.get("hostedUrl") or job.get("applyUrl") or job.get("url")
        discovered.append(DiscoveredJob(
            external_job_id=str(job.get("id") or _stable_external_id(company.domain, title, None, url)),
            title=title,
            url=url,
            location=_extract_location(categories.get("location") or job.get("location")),
            posting_text=_flatten_lever_posting_text(job),
            job_family=_infer_job_family(title, domain_profile, company.job_family_focus),
            source="lever_api",
        ))
    return discovered


def _iter_ashby_postings(data: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("jobs", "jobPostings", "openJobs"):
            value = data.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
        for key in ("departments", "teams"):
            value = data.get(key)
            if isinstance(value, list):
                for entry in value:
                    if not isinstance(entry, dict):
                        continue
                    nested = entry.get("jobPostings") or entry.get("jobs") or []
                    for item in nested:
                        if isinstance(item, dict):
                            yield item


def _parse_ashby_jobs(data: Any, company: WatchlistCompany, domain_profile: DomainProfile) -> List[DiscoveredJob]:
    discovered: List[DiscoveredJob] = []
    for job in _iter_ashby_postings(data):
        title = (job.get("title") or job.get("name") or "").strip()
        if not title:
            continue
        url = job.get("jobUrl") or job.get("url") or job.get("absoluteUrl")
        posting_text = _clean_text(job.get("descriptionHtml") or job.get("description") or job.get("content"))
        location = _extract_location(job.get("location")) or _extract_location(job.get("locations"))
        discovered.append(DiscoveredJob(
            external_job_id=str(job.get("id") or _stable_external_id(company.domain, title, location, url)),
            title=title,
            url=url,
            location=location,
            posting_text=posting_text,
            job_family=_infer_job_family(title, domain_profile, company.job_family_focus),
            source="ashby_api",
        ))
    return discovered


def _extract_workday_posting_text(detail: Any) -> Optional[str]:
    if not isinstance(detail, dict):
        return None

    candidates = [
        detail.get("jobDescription"),
        detail.get("description"),
        detail.get("content"),
    ]
    info = detail.get("jobPostingInfo")
    if isinstance(info, dict):
        candidates.extend([
            info.get("jobDescription"),
            info.get("description"),
            info.get("jobPostingDescription"),
            info.get("content"),
        ])
    return _clean_text(" ".join(str(value) for value in candidates if value))


def _fetch_workday_jobs(company: WatchlistCompany, domain_profile: DomainProfile) -> List[DiscoveredJob]:
    if not company.slug or not company.workday_instance or not company.workday_board:
        return []

    base_url = (
        f"https://{company.slug}.{company.workday_instance}.myworkdayjobs.com"
        f"/wday/cxs/{company.slug}/{company.workday_board}"
    )
    discovered: List[DiscoveredJob] = []
    offset = 0
    limit = 20
    total = None

    while total is None or offset < total:
        payload = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
        data = _post_json(f"{base_url}/jobs", payload)
        postings = data.get("jobPostings", []) if isinstance(data, dict) else []
        total = data.get("total", len(postings)) if isinstance(data, dict) else len(postings)
        if not postings:
            break

        for posting in postings:
            if not isinstance(posting, dict):
                continue
            title = (posting.get("title") or posting.get("bulletFields", [{}])[0].get("label") or "").strip()
            if not title:
                continue
            external_path = posting.get("externalPath") or ""
            url = f"{base_url}{external_path}" if external_path else None
            detail = _get_json(url) if url else {}
            discovered.append(DiscoveredJob(
                external_job_id=str(posting.get("bulletFields", [{}])[0].get("id") or posting.get("id") or external_path or _stable_external_id(company.domain, title, posting.get("locationsText"), url)),
                title=title,
                url=url,
                location=_extract_location(posting.get("locationsText") or posting.get("location")),
                posting_text=_extract_workday_posting_text(detail),
                job_family=_infer_job_family(title, domain_profile, company.job_family_focus),
                source="workday_api",
            ))

        offset += limit

    return discovered


def _extract_smartrecruiters_posting_text(detail: Any) -> Optional[str]:
    if not isinstance(detail, dict):
        return None

    parts: List[str] = []
    if detail.get("jobAd"):
        parts.append(str(detail["jobAd"]))
    sections = detail.get("sections") or {}
    if isinstance(sections, dict):
        for value in sections.values():
            if isinstance(value, dict):
                parts.extend(str(v) for v in value.values() if v)
            elif value:
                parts.append(str(value))
    if detail.get("content"):
        parts.append(str(detail["content"]))
    return _clean_text(" ".join(parts))


def _fetch_smartrecruiters_jobs(company: WatchlistCompany, domain_profile: DomainProfile) -> List[DiscoveredJob]:
    if not company.slug:
        return []

    base_url = f"https://api.smartrecruiters.com/v1/companies/{company.slug}/postings"
    data = _get_json(base_url)
    postings = data.get("content", []) if isinstance(data, dict) else []
    discovered: List[DiscoveredJob] = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        title = (posting.get("name") or posting.get("title") or "").strip()
        if not title:
            continue
        posting_id = str(posting.get("id") or posting.get("ref") or "")
        detail = _get_json(f"{base_url}/{posting_id}") if posting_id else {}
        url = posting.get("ref") or posting.get("applyUrl")
        loc = posting.get("location")
        if isinstance(loc, dict):
            location = _extract_location(loc) or _extract_location(loc.get("city"))
        else:
            location = _extract_location(loc)
        discovered.append(DiscoveredJob(
            external_job_id=posting_id or _stable_external_id(company.domain, title, None, url),
            title=title,
            url=url if isinstance(url, str) and url.startswith("http") else None,
            location=location,
            posting_text=_extract_smartrecruiters_posting_text(detail),
            job_family=_infer_job_family(title, domain_profile, company.job_family_focus),
            source="smartrecruiters_api",
        ))
    return discovered


def _parse_html_jobs(
    html: str,
    company: WatchlistCompany,
    base_url: str,
    domain_profile: DomainProfile,
    discovery_keywords: List[str],
) -> List[DiscoveredJob]:
    if not discovery_keywords:
        logger.warning("[%s] No discovery_keywords configured — HTML scraper will return no jobs", company.domain)
        return []

    soup = BeautifulSoup(html, "html.parser")
    discovered: List[DiscoveredJob] = []
    seen_ids = set()

    for anchor in soup.find_all("a", href=True):
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "").strip()
        if not title or not href:
            continue

        title_lower = title.lower()
        href_lower = href.lower()
        if not any(keyword in title_lower or keyword in href_lower for keyword in discovery_keywords):
            continue

        url = urljoin(base_url, href)
        external_job_id = _stable_external_id(company.domain, title, None, url)
        if external_job_id in seen_ids:
            continue
        seen_ids.add(external_job_id)

        discovered.append(DiscoveredJob(
            external_job_id=external_job_id,
            title=title,
            url=url,
            location=None,
            posting_text=None,
            job_family=_infer_job_family(title, domain_profile, company.job_family_focus),
            source="html_scrape",
        ))

    return discovered


def discover_company_jobs(company: WatchlistCompany, config: Config) -> List[DiscoveredJob]:
    ats = (company.ats or "").strip().lower()
    domain_profile = config.domain_profile
    if ats == "greenhouse" and company.slug:
        data = _get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{company.slug}/jobs",
            params={"content": "true"},
        )
        return _parse_greenhouse_jobs(data, company, domain_profile)

    if ats == "lever" and company.slug:
        data = _get_json(f"https://api.lever.co/v0/postings/{company.slug}", params={"mode": "json"})
        return _parse_lever_jobs(data, company, domain_profile)

    if ats == "ashby" and company.slug:
        data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{company.slug}")
        return _parse_ashby_jobs(data, company, domain_profile)

    if ats == "workday" and company.slug and company.workday_instance and company.workday_board:
        return _fetch_workday_jobs(company, domain_profile)

    if ats == "smartrecruiters" and company.slug:
        return _fetch_smartrecruiters_jobs(company, domain_profile)

    html_url = company.jobs_url or company.careers_url
    if html_url:
        html = _get_text(html_url)
        return _parse_html_jobs(
            html,
            company,
            html_url,
            domain_profile,
            config.domain_profile.discovery_keywords,
        )

    logger.warning("[%s] No supported discovery source configured", company.domain)
    return []


def run(watchlist: Watchlist, db: Database, config: Config, dry_run: bool = False) -> int:
    """
    Discover jobs for all watchlist companies and upsert them into the database.

    Returns the number of newly inserted jobs.
    """
    new_jobs = 0

    for company in watchlist.companies:
        company_row = db.get_company_by_domain(company.domain)
        if not company_row:
            company_id = db.upsert_company(
                name=company.name,
                domain=company.domain,
                priority=company.priority,
                ats=company.ats,
                slug=company.slug,
                careers_url=company.careers_url,
                jobs_url=company.jobs_url,
                job_family_focus=company.job_family_focus,
                notes=company.notes,
            )
        else:
            company_id = company_row["id"]

        logger.info("[%s] Discovering jobs via %s", company.domain, company.ats or "html")
        try:
            discovered = discover_company_jobs(company, config)
        except Exception as exc:
            logger.warning("[%s] Discovery failed: %s", company.domain, exc)
            continue

        active_ids: List[str] = []
        newly_inserted_here = 0
        for job in discovered:
            active_ids.append(job.external_job_id)
            inserted = db.insert_job(
                company_id=company_id,
                external_job_id=job.external_job_id,
                title=job.title,
                url=job.url,
                location=job.location,
                posting_text=job.posting_text,
                job_family=job.job_family,
                source=job.source,
            )
            if inserted is not None:
                new_jobs += 1
                newly_inserted_here += 1

        db.mark_jobs_closed(company_id, active_ids)

        logger.info(
            "[%s] Found %s jobs (%s newly inserted)",
            company.domain,
            len(discovered),
            newly_inserted_here,
        )

    if new_jobs and not dry_run:
        db.increment_metric("jobs_discovered", new_jobs)

    return new_jobs
