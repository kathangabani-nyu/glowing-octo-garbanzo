"""
SMTP-level email verification for AutoApply V2.2.

Connects to domain MX server, issues RCPT TO, interprets response.
Handles: verified (250), rejected (550), greylisting (4xx with retry),
catch-all detection, timeouts. Includes MX record caching.

Module owner: Codex
"""

import smtplib
import socket
import time
from dataclasses import dataclass
from typing import Optional, Dict

try:
    import dns.resolver
    DNS_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - depends on environment
    dns = None
    DNS_IMPORT_ERROR = exc


@dataclass
class VerificationResult:
    status: str       # "verified", "rejected", "catch_all", "greylisted", "timeout", "error"
    mx_host: str = ""
    response_code: int = 0
    message: str = ""


# Module-level MX cache
_mx_cache: Dict[str, str] = {}

# Rate limiting: track last connection time per domain
_last_connect: Dict[str, float] = {}
MIN_CONNECT_INTERVAL = 2.0


def _get_mx(domain: str) -> Optional[str]:
    """Resolve MX record for domain, with caching."""
    if DNS_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Missing dependency 'dnspython'. Install project requirements before "
            "running SMTP verification."
        ) from DNS_IMPORT_ERROR
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        records = dns.resolver.resolve(domain, "MX")
        mx_host = str(sorted(records, key=lambda r: r.preference)[0].exchange).rstrip(".")
        _mx_cache[domain] = mx_host
        return mx_host
    except Exception:
        return None


def _rate_limit(domain: str):
    """Enforce minimum interval between SMTP connections to the same domain."""
    now = time.time()
    last = _last_connect.get(domain, 0)
    wait = MIN_CONNECT_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_connect[domain] = time.time()


def verify_email(email: str, timeout: int = 10, sender_email: str = "verify@example.com") -> VerificationResult:
    """
    Verify an email address via SMTP RCPT TO.

    Args:
        email: Email address to verify
        timeout: Connection timeout in seconds
        sender_email: MAIL FROM address for the SMTP session

    Returns:
        VerificationResult with status and details
    """
    domain = email.split("@")[-1]
    mx_host = _get_mx(domain)

    if not mx_host:
        return VerificationResult(status="error", message=f"No MX record for {domain}")

    _rate_limit(domain)

    try:
        smtp = smtplib.SMTP(timeout=timeout)
        smtp.connect(mx_host, 25)
        smtp.helo("autoapply.local")
        smtp.mail(sender_email)
        code, msg = smtp.rcpt(email)
        smtp.quit()

        msg_str = msg.decode("utf-8", errors="replace") if isinstance(msg, bytes) else str(msg)

        if code == 250:
            return VerificationResult(status="verified", mx_host=mx_host,
                                      response_code=code, message=msg_str)
        elif code == 550 or code == 551 or code == 553:
            return VerificationResult(status="rejected", mx_host=mx_host,
                                      response_code=code, message=msg_str)
        elif 400 <= code < 500:
            return VerificationResult(status="greylisted", mx_host=mx_host,
                                      response_code=code, message=msg_str)
        else:
            return VerificationResult(status="error", mx_host=mx_host,
                                      response_code=code, message=msg_str)

    except socket.timeout:
        return VerificationResult(status="timeout", mx_host=mx_host,
                                  message="Connection timed out")
    except smtplib.SMTPServerDisconnected:
        return VerificationResult(status="error", mx_host=mx_host,
                                  message="Server disconnected")
    except Exception as e:
        return VerificationResult(status="error", mx_host=mx_host,
                                  message=str(e))


def check_catch_all(domain: str, timeout: int = 10, sender_email: str = "verify@example.com") -> bool:
    """
    Detect if a domain is catch-all by verifying a known-fake address.
    If the fake address returns 250, the domain accepts all addresses.
    """
    fake_email = f"xzq98random7fake42@{domain}"
    result = verify_email(fake_email, timeout=timeout, sender_email=sender_email)
    return result.status == "verified"
