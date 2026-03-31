"""
YAML config loader and validation for AutoApply V2.2.
Loads config.local.yaml and watchlist.local.yaml by default, validates required
fields, and provides typed access via dataclasses.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import yaml


# ── Dataclasses ──

@dataclass
class SenderConfig:
    name: str
    email: str
    signature: str = ""


@dataclass
class SendingConfig:
    max_initial_per_day: int = 12
    max_followups_per_day: int = 8
    min_delay_seconds: int = 45
    max_delay_seconds: int = 90
    business_hours_start: int = 8
    business_hours_end: int = 18
    warm_up_days: int = 7
    warm_up_initial_limit: int = 3


@dataclass
class SafetyConfig:
    max_bounce_rate: float = 0.05
    bounce_window: int = 50
    max_review_skip_rate: float = 0.40
    review_skip_window: int = 20


@dataclass
class CooldownConfig:
    person_days: int = 90
    company_job_family_days: int = 30


@dataclass
class JobTarget:
    title_keywords: List[str] = field(default_factory=list)
    title_exclude: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    seniority: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    remote_ok: bool = True
    skill_only_requires_engineering_title: bool = True
    us_only: bool = False
    location_reject_keywords: List[str] = field(default_factory=list)
    min_experience_years: int = 0
    max_experience_years: int = 99
    visa_reject_keywords: List[str] = field(default_factory=list)


@dataclass
class QualificationConfig:
    auto_threshold: int = 70
    review_threshold: int = 40


@dataclass
class MessageQualityConfig:
    auto_send_threshold: int = 60


@dataclass
class ReplyKeywords:
    positive: List[str] = field(default_factory=lambda: [
        "interested", "schedule", "interview", "call", "chat",
        "love to", "great fit", "resume looks"
    ])
    referral: List[str] = field(default_factory=lambda: [
        "forward", "refer", "passed along", "colleague", "better suited"
    ])
    rejection: List[str] = field(default_factory=lambda: [
        "not hiring", "no openings", "filled", "not a fit",
        "unfortunately", "decided to", "move forward with"
    ])


@dataclass
class LLMConfig:
    use_local_llm: bool = False
    ollama_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: int = 30
    assembly_gate_enabled: bool = False
    assembly_gate_mode: str = "advisory"
    assembly_gate_min_confidence: float = 0.75
    generic_team_words: List[str] = field(default_factory=lambda: [
        "team", "engineering", "company", "organization", "group"
    ])


@dataclass
class DatabaseConfig:
    path: str = "autoapply.db"


@dataclass
class DiscoveryConfig:
    hn_enabled: bool = True
    hn_max_per_run: int = 50
    github_trending_enabled: bool = False
    builtin_cities: List[str] = field(default_factory=lambda: ["nyc", "sf", "chicago", "boston", "la"])


@dataclass
class DomainProfile:
    name: str = "cs"
    role_buckets: Dict[str, List[str]] = field(default_factory=lambda: {
        "ml": ["machine learning", "ml engineer", "ai engineer", "deep learning", "nlp", "computer vision"],
        "research": ["research scientist", "research engineer", "applied scientist"],
        "software": ["software engineer", "backend engineer", "systems engineer", "platform engineer"],
        "fullstack": ["fullstack", "full stack", "full-stack", "frontend", "front-end", "web developer"],
    })
    default_bucket: str = "software"
    discovery_keywords: List[str] = field(default_factory=lambda: [
        "engineer", "developer", "scientist", "machine learning", "ml ",
        " ai", "data", "research", "platform", "backend", "frontend",
        "full stack", "full-stack",
    ])
    reject_roles: List[str] = field(default_factory=lambda: [
        "analyst", "operations", "benefits", "compliance", "legal",
        "counsel", "attorney", "paralegal", "accountant", "accounting",
        "finance manager", "financial", "controller", "tax",
        "recruiter", "recruiting", "talent acquisition", "human resources",
        "people operations", "people partner", "hr ",
        "sales", "account executive", "account manager", "business development",
        "account development", "account development representative", "adr",
        "inbound adr", "specialist seller", "partner specialist",
        "mid-market", "public sector", "implementation consultant",
        "renewals specialist",
        "marketing", "content", "copywriter", "communications", "pr ",
        "public relations", "social media", "growth marketing",
        "product designer", "ux designer", "ui designer", "graphic designer",
        "brand designer", "visual designer", "design director",
        "product manager", "program manager", "project manager",
        "customer success", "customer support", "support engineer",
        "support agent", "product support specialist",
        "office manager", "executive assistant", "administrative",
        "facilities", "real estate", "workplace",
        "rtl", "asic", "fpga", "hardware engineer", "mechanical engineer",
        "electrical engineer", "civil engineer", "chemical engineer",
        "verification engineer", "codesign", "physical design",
        "layout engineer", "silicon", "chip",
        "policy", "government affairs", "lobbyist",
        "supply chain", "logistics", "procurement", "inventory",
        "warehouse", "shipping",
        "lab technician", "technician",
        "nurse", "physician", "clinician", "therapist", "pharmacist",
        "kyc", "sanctions", "aml", "anti-money", "fraud analyst",
        "risk analyst", "audit", "auditor",
    ])
    generic_team_words: List[str] = field(default_factory=lambda: [
        "team", "engineering", "company", "organization", "group"
    ])


@dataclass
class Config:
    sender: SenderConfig
    sending: SendingConfig = field(default_factory=SendingConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    cooldowns: CooldownConfig = field(default_factory=CooldownConfig)
    job_targets: JobTarget = field(default_factory=JobTarget)
    qualification: QualificationConfig = field(default_factory=QualificationConfig)
    message_quality: MessageQualityConfig = field(default_factory=MessageQualityConfig)
    reply_keywords: ReplyKeywords = field(default_factory=ReplyKeywords)
    llm: LLMConfig = field(default_factory=LLMConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    domain_profile: DomainProfile = field(default_factory=DomainProfile)
    resume_path: str = ""
    resume_variants: Dict[str, str] = field(default_factory=dict)
    template_dir: str = "templates"


@dataclass
class WatchlistCompany:
    name: str
    domain: str
    priority: int = 5
    ats: Optional[str] = None
    slug: Optional[str] = None
    workday_instance: str = ""
    workday_board: str = ""
    careers_url: Optional[str] = None
    jobs_url: Optional[str] = None
    job_family_focus: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Watchlist:
    companies: List[WatchlistCompany]


# ── Loader helpers ──

def _build_dataclass(cls, data: dict):
    """Recursively build a dataclass from a dict, ignoring unknown keys."""
    if data is None:
        data = {}
    fieldnames = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in fieldnames}
    return cls(**filtered)


def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        print(f"ERROR: Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        print(f"ERROR: Config file must be a YAML mapping: {path}", file=sys.stderr)
        sys.exit(1)
    return data


def load_config(config_path: str = "config.local.yaml") -> Config:
    data = _load_yaml(config_path)

    # Sender is required
    if "sender" not in data:
        print("ERROR: config must contain a 'sender' section with 'name' and 'email'.",
              file=sys.stderr)
        sys.exit(1)

    sender_data = data["sender"]
    if not sender_data.get("name") or not sender_data.get("email"):
        print("ERROR: sender.name and sender.email are required.", file=sys.stderr)
        sys.exit(1)

    sender = _build_dataclass(SenderConfig, sender_data)
    sending = _build_dataclass(SendingConfig, data.get("sending", {}))
    safety = _build_dataclass(SafetyConfig, data.get("safety", {}))
    cooldowns = _build_dataclass(CooldownConfig, data.get("cooldowns", {}))
    job_targets = _build_dataclass(JobTarget, data.get("job_targets", {}))
    qualification = _build_dataclass(QualificationConfig, data.get("qualification", {}))
    message_quality = _build_dataclass(MessageQualityConfig, data.get("message_quality", {}))
    reply_keywords = _build_dataclass(ReplyKeywords, data.get("reply_keywords", {}))
    llm_data = data.get("llm", {})
    llm = _build_dataclass(LLMConfig, llm_data)
    database = _build_dataclass(DatabaseConfig, data.get("database", {}))
    discovery = _build_dataclass(DiscoveryConfig, data.get("discovery", {}))
    domain_profile = _build_dataclass(DomainProfile, data.get("domain_profile", {}))
    if "generic_team_words" not in llm_data:
        llm.generic_team_words = list(domain_profile.generic_team_words)

    return Config(
        sender=sender,
        sending=sending,
        safety=safety,
        cooldowns=cooldowns,
        job_targets=job_targets,
        qualification=qualification,
        message_quality=message_quality,
        reply_keywords=reply_keywords,
        llm=llm,
        database=database,
        discovery=discovery,
        domain_profile=domain_profile,
        resume_path=data.get("resume_path", ""),
        resume_variants=data.get("resume_variants", {}),
        template_dir=data.get("template_dir", "templates"),
    )


def load_watchlist(watchlist_path: str = "watchlist.local.yaml") -> Watchlist:
    data = _load_yaml(watchlist_path)

    raw_companies = data.get("companies", [])
    if not raw_companies:
        print("ERROR: watchlist must contain a 'companies' list.", file=sys.stderr)
        sys.exit(1)

    companies = []
    for i, entry in enumerate(raw_companies):
        if not entry.get("name") or not entry.get("domain"):
            print(f"ERROR: watchlist company #{i+1} must have 'name' and 'domain'.",
                  file=sys.stderr)
            sys.exit(1)
        companies.append(_build_dataclass(WatchlistCompany, entry))

    return Watchlist(companies=companies)
