"""
Email assembly engine for AutoApply V2.2.

For each qualified job with a resolved contact:
- Select template by role bucket
- Select resume variant
- Extract dynamic fields via detail_extractor
- Render Jinja2 template
- Compute message_quality_score
- Route to auto-send or review queue

Module owner: Claude Code
"""

import os
import re
from typing import Optional, Dict, Tuple

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from src.db import Database
from src.config import Config, DomainProfile
from src.utils import get_logger

logger = get_logger("email_assembler")

def _classify_role_bucket(job_title: str, domain_profile: DomainProfile) -> str:
    """Classify a job title into a role bucket for template selection."""
    title_lower = job_title.lower()
    for bucket, keywords in domain_profile.role_buckets.items():
        if any(kw in title_lower for kw in keywords):
            return bucket
    return domain_profile.default_bucket


def _select_resume_variant(
    role_bucket: str,
    resume_variants: Dict[str, str],
    domain_profile: DomainProfile,
    resume_path: str = "",
) -> Optional[str]:
    """Select the best resume variant for this role bucket."""
    if resume_path:
        return resume_path
    if role_bucket in resume_variants:
        return resume_variants[role_bucket]
    # Fallback through preference order
    for bucket in domain_profile.role_buckets.keys():
        if bucket in resume_variants:
            return resume_variants[bucket]
    # Return first available
    if resume_variants:
        return next(iter(resume_variants.values()))
    return None


def _compute_quality_score(
    job_score: int,
    confidence_tier: str,
    has_personalization: bool,
    is_named_recipient: bool,
) -> int:
    """
    Compute message quality score (0-100).

    Factors:
    - Job fit strength (from qualification score)
    - Contact confidence tier
    - Presence of a real personalization detail
    - Whether recipient is named vs generic
    """
    score = 0

    # Job fit: normalize to 0-30 range
    score += min(30, int(job_score * 0.3)) if job_score else 0

    # Contact confidence: 0-35
    tier_scores = {
        "public_exact": 35,
        "public_generic_inbox": 25,
        "pattern_verified": 30,
        "pattern_inferred": 15,
        "catch_all_guess": 10,
        "generic_guess": 5,
    }
    score += tier_scores.get(confidence_tier, 0)

    # Personalization: 0-20
    if has_personalization:
        score += 20

    # Named recipient: 0-15
    if is_named_recipient:
        score += 15

    return min(100, score)


def _should_attach_resume(confidence_tier: str, message_type: str = "initial") -> bool:
    """
    Determine resume attachment policy per V2.2 rules.

    Current policy:
    - Always attach on initial outreach
    - Always attach on follow-ups
    """
    return True


def _determine_review_reason(
    confidence_tier: str,
    quality_score: int,
    auto_send_threshold: int,
    has_personalization: bool,
) -> Optional[str]:
    """Determine if and why a message should go to review."""
    if confidence_tier == "pattern_inferred":
        return "pattern_inferred"
    if confidence_tier == "catch_all_guess":
        return "catch_all_guess"
    if quality_score < auto_send_threshold and quality_score > 0:
        if not has_personalization:
            return "weak_personalization"
        return "borderline_fit"
    return None


def _extract_details(
    posting_text: str, skills: list, homepage_html: str = None,
    use_llm: bool = False, llm_config=None,
    company: str = "", role_title: str = "",
) -> Dict[str, Optional[str]]:
    """
    Extract dynamic template fields.

    If use_llm=True and llm_config is provided, uses the LLM extractor
    (Ollama) for higher-quality extraction with hallucination guards.
    Otherwise falls back to the regex detail_extractor.
    """
    # LLM path: use Ollama for richer extraction when available
    if use_llm and llm_config:
        try:
            from src.llm_extractor import extract_details_llm
            result = extract_details_llm(
                posting_text, skills, company, role_title, llm_config
            )
            return {
                "team_or_product": result.team_or_product,
                "key_technology": result.key_technology,
                "company_blurb": result.company_blurb,
            }
        except Exception as e:
            logger.warning(f"LLM extraction failed, falling back to regex: {e}")

    # Regex path: use Codex's detail_extractor module
    try:
        from src.detail_extractor import extract_details
        result = extract_details(posting_text, skills, homepage_html or "")
        return {
            "team_or_product": result.team_or_product,
            "key_technology": result.key_technology,
            "company_blurb": result.company_blurb,
        }
    except (ImportError, Exception):
        # Fallback: basic inline regex extraction
        details = {"team_or_product": None, "key_technology": None, "company_blurb": None}

        # Try to extract team name
        team_match = re.search(
            r"(?:our|the|join)\s+((?:[A-Z][A-Za-z&]+\s*)+?)\s+team",
            posting_text or ""
        )
        if team_match:
            details["team_or_product"] = team_match.group(1).strip()

        # Match best skill from posting
        if skills and posting_text:
            posting_lower = posting_text.lower()
            for skill in skills:
                if skill.lower() in posting_lower:
                    details["key_technology"] = skill
                    break

        return details


def _load_template_env(template_dir: str) -> Optional[Environment]:
    """Load Jinja2 template environment."""
    if not os.path.isdir(template_dir):
        return None
    return Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render_email(
    env: Optional[Environment],
    role_bucket: str,
    context: dict,
    default_bucket: str,
) -> Tuple[str, str]:
    """
    Render email subject and body from template.
    Falls back to a basic built-in template if Jinja templates aren't available.
    """
    if env:
        # Try role-specific template, then fallback
        for template_name in [f"{role_bucket}.j2", f"{default_bucket}.j2", "default.j2"]:
            try:
                template = env.get_template(template_name)
                body = template.render(**context)
                subject = context.get("subject", f"Re: {context.get('job_title', 'Open Role')}")
                return subject, body
            except TemplateNotFound:
                continue

    # Built-in fallback template
    contact_name = context.get("contact_name")
    greeting = f"Hi {contact_name.split()[0]}," if contact_name else "Hi,"

    team_line = ""
    if context.get("team_or_product"):
        team_line = f" on the {context['team_or_product']} team"

    tech_line = ""
    if context.get("key_technology"):
        tech_line = f" My recent work has focused on {context['key_technology']}."

    body = f"""{greeting}

I saw the {context.get('job_title', 'open role')} posting{team_line} at {context.get('company_name', 'your company')} and wanted to reach out.{tech_line}

I have experience that aligns well with what you're looking for. I'd welcome the chance to discuss how I could contribute.

Would you be open to a brief conversation?

Best,
{context.get('sender_name', '')}
{context.get('sender_email', '')}

If you'd prefer not to receive messages like this, just let me know and I'll remove you from my list."""

    subject = f"Interest in {context.get('job_title', 'Open Role')} at {context.get('company_name', 'Your Company')}"
    return subject, body


def run(config: Config, db: Database, dry_run: bool = False) -> int:
    """
    Assemble emails for all qualified jobs with resolved contacts.
    Returns count of messages created.
    """
    template_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        config.template_dir
    )
    env = _load_template_env(template_dir)

    if env:
        logger.info(f"Loaded templates from {template_dir}")
    else:
        logger.warning(f"No templates found at {template_dir}, using built-in fallback")

    # Check LLM availability once at start
    use_llm = False
    if config.llm.use_local_llm:
        try:
            from src.llm_extractor import check_ollama_available
            use_llm = check_ollama_available(config.llm)
            if use_llm:
                logger.info(f"LLM extraction enabled ({config.llm.model} via Ollama)")
            else:
                logger.info("LLM configured but Ollama not available, using regex extraction")
        except ImportError:
            logger.info("llm_extractor not available, using regex extraction")

    messages_created = 0
    qualified_jobs = db.get_qualified_jobs("qualified_auto") + db.get_qualified_jobs("qualified_review")

    for job in qualified_jobs:
        company_id = job["company_id"]
        job_id = job["id"]

        # Skip if we already assembled an initial outreach for this exact posting.
        if db.check_exact_posting_already_assembled(job_id):
            continue

        # Skip if already contacted for this exact posting
        if db.check_exact_posting_contacted(job_id):
            continue

        # Get best contact
        contact = db.get_best_contact(company_id)
        if not contact:
            logger.debug(f"No contact for job {job_id} at company {company_id}")
            continue

        # Check cooldowns
        if db.check_person_cooldown(contact["id"], config.cooldowns.person_days):
            logger.debug(f"Person {contact['id']} in cooldown")
            continue

        job_family = job["job_family"] or ""
        if job_family and db.check_company_job_family_cooldown(
            company_id, job_family, config.cooldowns.company_job_family_days
        ):
            logger.debug(f"Company {company_id} + family '{job_family}' in cooldown")
            continue

        # Check suppression
        if contact["email"] and db.check_suppression(email=contact["email"]):
            continue
        company = db.get_company(company_id)
        if company and db.check_suppression(
            domain=company["domain"], company_name=company["name"]
        ):
            continue

        # Extract dynamic details
        details = _extract_details(
            job["posting_text"] or "",
            config.job_targets.skills,
            use_llm=use_llm,
            llm_config=config.llm if use_llm else None,
            company=company["name"] if company else "",
            role_title=job["title"],
        )

        # Classify and select template/resume
        role_bucket = _classify_role_bucket(job["title"], config.domain_profile)
        resume_variant = _select_resume_variant(
            role_bucket,
            config.resume_variants,
            config.domain_profile,
            config.resume_path,
        )

        has_personalization = bool(details.get("team_or_product") or details.get("key_technology"))
        is_named = bool(contact["name"])

        # Compute quality score
        quality_score = _compute_quality_score(
            job_score=job["qualification_score"] or 0,
            confidence_tier=contact["confidence_tier"],
            has_personalization=has_personalization,
            is_named_recipient=is_named,
        )

        # Build template context
        context = {
            "sender_name": config.sender.name,
            "sender_email": config.sender.email,
            "sender_signature": config.sender.signature,
            "contact_name": contact["name"],
            "contact_email": contact["email"],
            "company_name": company["name"] if company else "",
            "company_domain": company["domain"] if company else "",
            "job_title": job["title"],
            "job_url": job["url"],
            "team_or_product": details.get("team_or_product"),
            "key_technology": details.get("key_technology"),
            "company_blurb": details.get("company_blurb"),
            "resume_variant": resume_variant,
            "role_bucket": role_bucket,
        }

        # Render email
        subject, body = _render_email(
            env,
            role_bucket,
            context,
            config.domain_profile.default_bucket,
        )

        # Determine if review is needed
        review_reason = _determine_review_reason(
            contact["confidence_tier"],
            quality_score,
            config.message_quality.auto_send_threshold,
            has_personalization,
        )
        review_required = review_reason is not None

        # Determine resume attachment policy
        attach_resume = _should_attach_resume(contact["confidence_tier"])

        # Insert message
        message_id = db.insert_message(
            job_id=job_id,
            person_id=contact["id"],
            company_id=company_id,
            template_used=f"{role_bucket}.j2",
            resume_variant=resume_variant if attach_resume else None,
            subject=subject,
            body=body,
            message_type="initial",
            message_quality_score=quality_score,
            review_required=review_required,
        )

        # If review required, add to review queue
        if review_required:
            from src.review_queue import insert_for_review
            insert_for_review(
                db, job_id=job_id, person_id=contact["id"],
                message_id=message_id, queue_reason=review_reason,
                confidence_tier=contact["confidence_tier"],
            )

        messages_created += 1
        tier = contact["confidence_tier"]
        status = "REVIEW" if review_required else "READY"
        logger.info(
            f"[{status}] {company['name'] if company else '?'} / {job['title']} "
            f"-> {contact['email']} ({tier}) score={quality_score}"
        )

    logger.info(f"Assembled {messages_created} messages")
    return messages_created
