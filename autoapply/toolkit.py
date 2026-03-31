"""Thin wrapper exposing AutoApply's mechanical utilities to AI agents."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.permutator import generate_permutations
from src.sender import (
    _build_mime_message,
    _get_gmail_service,
    _render_html_body,
    _send_via_gmail,
)
from src.smtp_verifier import VerificationResult, check_catch_all as _check_catch_all
from src.smtp_verifier import verify_email as _verify_email


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path

    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate

    module_candidate = _project_dir() / path
    if module_candidate.exists():
        return module_candidate

    return cwd_candidate


def get_gmail_service(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
):
    """Authenticate with Gmail and return a Gmail API service client."""
    return _get_gmail_service(
        credentials_path=str(_resolve_path(credentials_path)),
        token_path=str(_resolve_path(token_path)),
    )


def send_email(
    to_email: str,
    subject: str,
    body: str,
    sender_name: str,
    sender_email: str,
    resume_path: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Build and send an email via Gmail.

    Returns Gmail ids after a live send, or preview metadata in dry-run mode.
    """
    resolved_resume_path = None
    if resume_path:
        resolved_resume_path = str(_resolve_path(resume_path))

    raw_message = _build_mime_message(
        sender_email=sender_email,
        sender_name=sender_name,
        to_email=to_email,
        subject=subject,
        body=body,
        resume_path=resolved_resume_path,
    )

    if dry_run:
        return {
            "dry_run": True,
            "to_email": to_email,
            "subject": subject,
            "body": body,
            "html_body": _render_html_body(body),
            "resume_path": resolved_resume_path,
            "raw_message": raw_message,
        }

    service = get_gmail_service()
    result = _send_via_gmail(service, raw_message)
    return {
        "gmail_message_id": result.get("id"),
        "gmail_thread_id": result.get("threadId"),
        "label_ids": result.get("labelIds", []),
        "to_email": to_email,
        "subject": subject,
    }


def verify_email(email: str, timeout: int = 10, sender_email: str = "verify@example.com") -> VerificationResult:
    """Verify an email address at SMTP level."""
    return _verify_email(email, timeout=timeout, sender_email=sender_email)


def check_catch_all(domain: str, timeout: int = 10, sender_email: str = "verify@example.com") -> bool:
    """Detect whether a domain behaves like a catch-all."""
    return _check_catch_all(domain, timeout=timeout, sender_email=sender_email)


def generate_email_guesses(first_name: str, last_name: str, domain: str) -> list[str]:
    """Generate likely email permutations for a named contact."""
    return generate_permutations(first_name, last_name, domain)


def check_already_contacted(db, email: str) -> bool:
    """Check suppression state and prior outreach for an email address."""
    return db.check_already_contacted(email)


def check_company_contacted_recently(db, domain: str, days: int = 30) -> bool:
    """Check whether any outreach was sent to a company domain recently."""
    return db.check_company_contacted_recently(domain, days=days)


def record_send(
    db,
    to_email: str,
    to_name: Optional[str],
    company_domain: str,
    company_name: Optional[str],
    job_title: Optional[str],
    job_url: Optional[str],
    subject: str,
    body: str,
    gmail_message_id: Optional[str],
    gmail_thread_id: Optional[str] = None,
    resume_used: Optional[str] = None,
    agent_session: Optional[str] = None,
) -> int:
    """Persist a sent email in the outreach log."""
    return db.record_send(
        to_email=to_email,
        to_name=to_name,
        company_domain=company_domain,
        company_name=company_name,
        job_title=job_title,
        job_url=job_url,
        subject=subject,
        body=body,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
        resume_used=resume_used,
        agent_session=agent_session,
    )


def add_suppression(
    db,
    email: Optional[str] = None,
    domain: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Add an email and/or domain to the suppression list."""
    db.add_suppression(email=email, domain=domain, reason=reason)


def get_send_history(db, days: int = 30) -> list[dict]:
    """Return recent outreach log rows as plain dictionaries."""
    return db.get_send_history(days=days)


def get_today_send_count(db) -> int:
    """Return today's count of sent outreach messages."""
    return db.get_today_send_count()


__all__ = [
    "VerificationResult",
    "add_suppression",
    "check_already_contacted",
    "check_catch_all",
    "check_company_contacted_recently",
    "generate_email_guesses",
    "get_gmail_service",
    "get_send_history",
    "get_today_send_count",
    "record_send",
    "send_email",
    "verify_email",
]
