"""
Daily reporting for AutoApply V2.2.

Builds a transparent status snapshot from SQLite so the user can see:
- what happened today
- what is queued
- whether safety-stop signals are rising
- how the last 7 and 30 days are trending

Module owner: Codex
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from src.db import Database
from src.utils import get_logger


logger = get_logger("reporter")


@dataclass
class TrendWindow:
    days: int
    jobs_discovered: int = 0
    emails_sent: int = 0
    replies_received: int = 0
    bounces: int = 0
    reply_rate: float = 0.0
    bounce_rate: float = 0.0
    discovery_to_send_rate: float = 0.0


@dataclass
class CompanyResponseTrend:
    company_name: str
    replies: int


@dataclass
class SourceFunnelRow:
    discovery_source: str
    companies: int
    jobs: int
    qualified: int
    contacts: int
    sent: int
    replies: int


@dataclass
class ReportSnapshot:
    metric_date: str
    jobs_discovered: int = 0
    jobs_qualified_auto: int = 0
    jobs_qualified_review: int = 0
    jobs_rejected: int = 0
    contacts_resolved: int = 0
    contacts_public_exact: int = 0
    contacts_pattern_verified: int = 0
    contacts_generic_inbox: int = 0
    emails_sent: int = 0
    followups_sent: int = 0
    replies_received: int = 0
    replies_positive: int = 0
    bounces: int = 0
    reviews_approved: int = 0
    reviews_skipped: int = 0
    pending_reviews: int = 0
    ready_messages: int = 0
    bounce_rate: float = 0.0
    review_approval_rate: Optional[float] = None
    trend_7d: TrendWindow = field(default_factory=lambda: TrendWindow(days=7))
    trend_30d: TrendWindow = field(default_factory=lambda: TrendWindow(days=30))
    top_responding_companies_30d: List[CompanyResponseTrend] = field(default_factory=list)
    source_funnel: List[SourceFunnelRow] = field(default_factory=list)


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _build_trend_window(db: Database, days: int, metric_date: Optional[str] = None) -> TrendWindow:
    if metric_date is None:
        metric_date = date.today().isoformat()

    row = db.conn.execute(
        """
        SELECT
            COALESCE(SUM(jobs_discovered), 0) AS jobs_discovered,
            COALESCE(SUM(emails_sent), 0) AS emails_sent,
            COALESCE(SUM(replies_received), 0) AS replies_received,
            COALESCE(SUM(bounces), 0) AS bounces
        FROM daily_metrics
        WHERE metric_date BETWEEN date(?, ?) AND date(?)
        """,
        (metric_date, f"-{days - 1} days", metric_date),
    ).fetchone()

    jobs_discovered = row["jobs_discovered"] if row else 0
    emails_sent = row["emails_sent"] if row else 0
    replies_received = row["replies_received"] if row else 0
    bounces = row["bounces"] if row else 0

    return TrendWindow(
        days=days,
        jobs_discovered=jobs_discovered,
        emails_sent=emails_sent,
        replies_received=replies_received,
        bounces=bounces,
        reply_rate=_safe_rate(replies_received, emails_sent),
        bounce_rate=_safe_rate(bounces, emails_sent),
        discovery_to_send_rate=_safe_rate(emails_sent, jobs_discovered),
    )


def _get_top_responding_companies(
    db: Database,
    days: int = 30,
    metric_date: Optional[str] = None,
    limit: int = 5,
) -> List[CompanyResponseTrend]:
    if metric_date is None:
        metric_date = date.today().isoformat()

    rows = db.conn.execute(
        """
        SELECT c.name AS company_name, COUNT(*) AS replies
        FROM messages m
        JOIN companies c ON m.company_id = c.id
        WHERE m.status LIKE 'replied_%'
          AND m.sent_at IS NOT NULL
          AND date(m.sent_at) BETWEEN date(?, ?) AND date(?)
        GROUP BY c.name
        ORDER BY replies DESC, c.name ASC
        LIMIT ?
        """,
        (metric_date, f"-{days - 1} days", metric_date, limit),
    ).fetchall()

    return [
        CompanyResponseTrend(company_name=row["company_name"], replies=row["replies"])
        for row in rows
    ]


def build_snapshot(db: Database, metric_date: Optional[str] = None) -> ReportSnapshot:
    if metric_date is None:
        metric_date = date.today().isoformat()

    metrics = db.get_daily_metrics(metric_date)
    snapshot = ReportSnapshot(metric_date=metric_date)

    if metrics:
        for field in (
            "jobs_discovered",
            "jobs_qualified_auto",
            "jobs_qualified_review",
            "jobs_rejected",
            "contacts_resolved",
            "contacts_public_exact",
            "contacts_pattern_verified",
            "contacts_generic_inbox",
            "emails_sent",
            "followups_sent",
            "replies_received",
            "replies_positive",
            "bounces",
            "reviews_approved",
            "reviews_skipped",
        ):
            setattr(snapshot, field, metrics[field])

    pending_row = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM review_queue WHERE review_status = 'pending'"
    ).fetchone()
    ready_row = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM messages WHERE status = 'ready'"
    ).fetchone()

    snapshot.pending_reviews = pending_row["cnt"] if pending_row else 0
    snapshot.ready_messages = ready_row["cnt"] if ready_row else 0
    snapshot.bounce_rate = db.get_recent_bounce_rate()
    snapshot.review_approval_rate = db.get_review_approval_rate()
    snapshot.trend_7d = _build_trend_window(db, 7, metric_date=metric_date)
    snapshot.trend_30d = _build_trend_window(db, 30, metric_date=metric_date)
    snapshot.top_responding_companies_30d = _get_top_responding_companies(
        db, 30, metric_date=metric_date
    )
    snapshot.source_funnel = [
        SourceFunnelRow(
            discovery_source=row["discovery_source"],
            companies=row["companies"],
            jobs=row["jobs"],
            qualified=row["qualified"],
            contacts=row["contacts"],
            sent=row["sent"],
            replies=row["replies"],
        )
        for row in db.get_pipeline_funnel()
    ]

    return snapshot


def _format_rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_top_companies(items: List[CompanyResponseTrend]) -> str:
    if not items:
        return "none"
    return ", ".join(f"{item.company_name}({item.replies})" for item in items)


def _format_source_funnel(items: List[SourceFunnelRow]) -> str:
    if not items:
        return "none"
    return ", ".join(
        (
            f"{item.discovery_source}: companies={item.companies} "
            f"jobs={item.jobs} qualified={item.qualified} contacts={item.contacts} "
            f"sent={item.sent} replies={item.replies}"
        )
        for item in items
    )


def render_report(snapshot: ReportSnapshot) -> str:
    review_rate = (
        _format_rate(snapshot.review_approval_rate)
        if snapshot.review_approval_rate is not None
        else "n/a"
    )

    lines = [
        f"AutoApply report for {snapshot.metric_date}",
        "Today:",
        f"  jobs discovered={snapshot.jobs_discovered}, auto={snapshot.jobs_qualified_auto}, review={snapshot.jobs_qualified_review}, rejected={snapshot.jobs_rejected}",
        f"  contacts resolved={snapshot.contacts_resolved}, ready={snapshot.ready_messages}, sent={snapshot.emails_sent}, followups={snapshot.followups_sent}",
        f"  replies={snapshot.replies_received}, positive={snapshot.replies_positive}, bounces={snapshot.bounces}",
        "Review queue:",
        f"  pending={snapshot.pending_reviews}, approved={snapshot.reviews_approved}, skipped={snapshot.reviews_skipped}, approval_rate={review_rate}",
        "Trends:",
        f"  7d  reply_rate={_format_rate(snapshot.trend_7d.reply_rate)}  bounce_rate={_format_rate(snapshot.trend_7d.bounce_rate)}  discover_to_send={_format_rate(snapshot.trend_7d.discovery_to_send_rate)}",
        f"  30d reply_rate={_format_rate(snapshot.trend_30d.reply_rate)}  bounce_rate={_format_rate(snapshot.trend_30d.bounce_rate)}  discover_to_send={_format_rate(snapshot.trend_30d.discovery_to_send_rate)}",
        f"  top responding companies (30d): {_format_top_companies(snapshot.top_responding_companies_30d)}",
        f"  source funnel: {_format_source_funnel(snapshot.source_funnel)}",
        "Safety:",
        f"  rolling_bounce_rate={_format_rate(snapshot.bounce_rate)}",
    ]
    return "\n".join(lines)


def run(db: Database, metric_date: Optional[str] = None, emit: bool = True) -> ReportSnapshot:
    snapshot = build_snapshot(db, metric_date=metric_date)
    report_text = render_report(snapshot)
    if emit:
        print(report_text)
    logger.info("Generated report for %s", snapshot.metric_date)
    return snapshot


def snapshot_to_dict(snapshot: ReportSnapshot) -> Dict[str, object]:
    return {
        "metric_date": snapshot.metric_date,
        "jobs_discovered": snapshot.jobs_discovered,
        "jobs_qualified_auto": snapshot.jobs_qualified_auto,
        "jobs_qualified_review": snapshot.jobs_qualified_review,
        "jobs_rejected": snapshot.jobs_rejected,
        "contacts_resolved": snapshot.contacts_resolved,
        "contacts_public_exact": snapshot.contacts_public_exact,
        "contacts_pattern_verified": snapshot.contacts_pattern_verified,
        "contacts_generic_inbox": snapshot.contacts_generic_inbox,
        "emails_sent": snapshot.emails_sent,
        "followups_sent": snapshot.followups_sent,
        "replies_received": snapshot.replies_received,
        "replies_positive": snapshot.replies_positive,
        "bounces": snapshot.bounces,
        "reviews_approved": snapshot.reviews_approved,
        "reviews_skipped": snapshot.reviews_skipped,
        "pending_reviews": snapshot.pending_reviews,
        "ready_messages": snapshot.ready_messages,
        "bounce_rate": snapshot.bounce_rate,
        "review_approval_rate": snapshot.review_approval_rate,
        "trend_7d": {
            "days": snapshot.trend_7d.days,
            "jobs_discovered": snapshot.trend_7d.jobs_discovered,
            "emails_sent": snapshot.trend_7d.emails_sent,
            "replies_received": snapshot.trend_7d.replies_received,
            "bounces": snapshot.trend_7d.bounces,
            "reply_rate": snapshot.trend_7d.reply_rate,
            "bounce_rate": snapshot.trend_7d.bounce_rate,
            "discovery_to_send_rate": snapshot.trend_7d.discovery_to_send_rate,
        },
        "trend_30d": {
            "days": snapshot.trend_30d.days,
            "jobs_discovered": snapshot.trend_30d.jobs_discovered,
            "emails_sent": snapshot.trend_30d.emails_sent,
            "replies_received": snapshot.trend_30d.replies_received,
            "bounces": snapshot.trend_30d.bounces,
            "reply_rate": snapshot.trend_30d.reply_rate,
            "bounce_rate": snapshot.trend_30d.bounce_rate,
            "discovery_to_send_rate": snapshot.trend_30d.discovery_to_send_rate,
        },
        "top_responding_companies_30d": [
            {"company_name": item.company_name, "replies": item.replies}
            for item in snapshot.top_responding_companies_30d
        ],
        "source_funnel": [
            {
                "discovery_source": item.discovery_source,
                "companies": item.companies,
                "jobs": item.jobs,
                "qualified": item.qualified,
                "contacts": item.contacts,
                "sent": item.sent,
                "replies": item.replies,
            }
            for item in snapshot.source_funnel
        ],
    }


def render_markdown_report(snapshot: ReportSnapshot) -> str:
    review_rate = (
        _format_rate(snapshot.review_approval_rate)
        if snapshot.review_approval_rate is not None
        else "n/a"
    )
    lines = [
        f"# AutoApply Report ({snapshot.metric_date})",
        "",
        "## Today",
        f"- Jobs: discovered `{snapshot.jobs_discovered}`, auto `{snapshot.jobs_qualified_auto}`, review `{snapshot.jobs_qualified_review}`, rejected `{snapshot.jobs_rejected}`",
        f"- Contacts: resolved `{snapshot.contacts_resolved}` (public_exact `{snapshot.contacts_public_exact}`, pattern_verified `{snapshot.contacts_pattern_verified}`, generic_inbox `{snapshot.contacts_generic_inbox}`)",
        f"- Outbound: ready `{snapshot.ready_messages}`, sent `{snapshot.emails_sent}`, follow-ups `{snapshot.followups_sent}`",
        f"- Replies: total `{snapshot.replies_received}`, positive `{snapshot.replies_positive}`, bounces `{snapshot.bounces}`",
        "",
        "## Review Queue",
        f"- Pending `{snapshot.pending_reviews}`",
        f"- Approved `{snapshot.reviews_approved}`",
        f"- Skipped `{snapshot.reviews_skipped}`",
        f"- Approval rate `{review_rate}`",
        "",
        "## Trends",
        f"- 7d: reply `{_format_rate(snapshot.trend_7d.reply_rate)}`, bounce `{_format_rate(snapshot.trend_7d.bounce_rate)}`, discovery->send `{_format_rate(snapshot.trend_7d.discovery_to_send_rate)}`",
        f"- 30d: reply `{_format_rate(snapshot.trend_30d.reply_rate)}`, bounce `{_format_rate(snapshot.trend_30d.bounce_rate)}`, discovery->send `{_format_rate(snapshot.trend_30d.discovery_to_send_rate)}`",
        f"- Top responding companies (30d): {_format_top_companies(snapshot.top_responding_companies_30d)}",
        f"- Source funnel: {_format_source_funnel(snapshot.source_funnel)}",
        "",
        "## Safety",
        f"- Rolling bounce rate: `{_format_rate(snapshot.bounce_rate)}`",
    ]
    return "\n".join(lines)


def write_report_files(snapshot: ReportSnapshot, output_dir: str) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    txt_path = os.path.join(output_dir, f"report_{snapshot.metric_date}.txt")
    md_path = os.path.join(output_dir, f"report_{snapshot.metric_date}.md")
    json_path = os.path.join(output_dir, f"report_{snapshot.metric_date}.json")

    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write(render_report(snapshot))
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown_report(snapshot))
        handle.write("\n")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot_to_dict(snapshot), handle, indent=2, ensure_ascii=True)
        handle.write("\n")

    return {"txt": txt_path, "md": md_path, "json": json_path}
