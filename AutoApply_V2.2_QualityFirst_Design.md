# AutoApply V2.2

Quality-First Job Outreach Pipeline

Version: 2.2
Date: March 23, 2026
Status: Planning
Supersedes: `AutoApply_V2.1_MultiUser_Design.docx`

## 1. Purpose

AutoApply V2.2 is a local Python system for targeted full-time job outreach by email.

It is designed for personal use, not scale.

The system should automate the repetitive parts of a job search:

- checking target companies for new openings
- filtering for relevant roles
- finding the best recruiter, hiring manager, or recruiting inbox
- extracting a few real details for personalization
- assembling a short outreach email with the correct resume
- sending with pacing and suppression rules
- tracking replies, bounces, and follow-ups

The system should not try to replace judgment where quality would suffer.

## 2. Core Design Change From V2.1

V2.1 leaned very hard into automation.

V2.2 keeps the daily automation, but adds a narrow quality gate:

- high-confidence targets can auto-send
- medium-confidence targets can be reviewed in a small queue
- low-confidence targets are skipped

This is the right tradeoff for a personal project. The goal is not maximum throughput. The goal is to avoid sending weak or misrouted emails.

## 3. Practical Goal

After setup, the intended routine is:

1. Run one daily command or scheduled task.
2. Review a small queue of uncertain targets when needed.
3. Personally handle positive replies.

Expected human effort:

- 5 to 10 minutes on most days
- occasional watchlist maintenance
- occasional template and threshold tuning

## 4. Hard Constraints

- Total operating cost should remain at $0 for the default version.
- No LinkedIn scraping or login-gated scraping.
- No fabricated details in emails.
- No high-volume sending.
- All data stays local by default.
- Every automation decision should degrade safely when confidence is low.

## 5. What Is Automated vs. Reviewed

### Fully Automated

- polling watchlist companies for jobs
- deduplicating and closing vanished postings
- keyword-based job qualification
- collecting contact candidates from public pages
- resolving public or strongly supported email paths
- assembling messages from templates
- sending high-confidence messages
- reply detection
- follow-up scheduling
- logging and reporting

### Human-Reviewed

- `pattern_inferred` addresses when SMTP proof is unavailable
- `catch_all_guess` addresses
- companies whose career pages cannot be parsed reliably
- jobs whose fit score is borderline but strategically interesting
- messages with weak personalization evidence

## 6. Architecture Overview

The pipeline still runs from a single orchestrator:

1. Job discovery
2. Job filtering
3. Contact discovery
4. Message assembly
5. Review queue generation
6. Sending
7. Follow-up management
8. Reporting

The review queue is the main V2.2 addition.

## 7. Stage 1: Job Discovery

The system discovers jobs from a manually curated watchlist of target companies.

Primary sources:

- Greenhouse public board API
- Lever public postings API
- Ashby public job board API
- direct HTML careers pages

### Revised Fallback Strategy

V2.1 skipped JavaScript-rendered career pages in MVP.

V2.2 adds a better fallback order:

1. public ATS endpoint
2. direct `jobs_url` override in `watchlist.yaml`
3. HTML scraping of a provided careers page
4. mark as `needs_manual_source_override`

This matters because many high-value companies will otherwise disappear from the pipeline.

### Watchlist Fields

Recommended company fields:

- `name`
- `domain`
- `priority`
- `ats`
- `slug`
- `careers_url`
- `jobs_url`
- `job_family_focus`
- `notes`

The `jobs_url` field is important for companies with awkward or dynamic job pages.

## 8. Stage 2: Job Filtering

The keyword-based scorer from V2.1 remains a good MVP choice.

It should continue to score on:

- title match
- skill match
- seniority fit
- location fit
- experience fit
- visa or authorization disqualifiers
- company priority

### Revised Output Classes

- `qualified_auto`
- `qualified_review`
- `reject`

Rules:

- clear high-scoring jobs go to `qualified_auto`
- borderline or partially ambiguous jobs go to `qualified_review`
- obvious misses go to `reject`

This is slightly better than a plain `qualified / borderline / reject` split because it aligns with sending behavior.

## 9. Stage 3: Contact Discovery

This remains the hardest stage and the biggest source of quality risk.

The contact resolution cascade should be:

1. recruiter name or email directly on the job page
2. company recruiting or talent team page
3. engineering manager, hiring manager, or department lead from public team pages
4. public recruiting inbox found on a public page
5. name-based email inference with SMTP support
6. skip or review

### Important Rule

Do not treat "we can guess an address" and "we found a real contact path" as the same thing.

## 10. Revised Contact Confidence Policy

This is the most important V2.2 change.

### Confidence Tiers

- `public_exact`
  - email found directly on an official or clearly trustworthy public page
  - auto-send allowed

- `public_generic_inbox`
  - a real inbox like `careers@`, `recruiting@`, or `jobs@` was found on a real company page
  - auto-send allowed

- `pattern_verified`
  - person name found, address inferred, SMTP check supports mailbox existence
  - auto-send allowed

- `pattern_inferred`
  - domain pattern is known or strongly suspected, but SMTP proof is unavailable
  - review required

- `catch_all_guess`
  - catch-all domain, best-guess personal address
  - review required, and only for high-priority companies

- `generic_guess`
  - inbox name guessed but not found publicly
  - do not auto-send

- `contact_failed`
  - no credible route found
  - skip

### Why This Matters

V2.1 was still a bit too generous here. For a personal project, sending fewer emails with better contact confidence is much better than sending more weak guesses.

## 11. Stage 4: Message Assembly

The template-based design remains correct.

The system should keep using:

- role buckets
- template selection
- extracted team/product details
- extracted technology alignment
- fallback-safe wording

### New Quality Gate

Each assembled message should receive a `message_quality_score` based on:

- job fit strength
- contact confidence tier
- presence of a real personalization detail
- whether the recipient is named or generic

Messages below the threshold move to review instead of sending automatically.

## 12. Resume Attachment Policy

V2.1 said to always attach the resume on first touch.

V2.2 makes this conditional.

### Attachment Rules

- attach by default for `public_exact`
- attach for `public_generic_inbox` only if the inbox was explicitly listed on the company site for jobs or recruiting
- for `pattern_verified`, do not attach on the first touch by default; include a resume link instead, and allow attachment on follow-up or manual override
- for reviewed `pattern_inferred` targets, let the reviewer decide
- do not attach to guessed generic inboxes

This reduces risk when contact confidence is weaker.

## 13. Cooldown and Suppression Policy

The company-wide 30-day cooldown in V2.1 was too broad.

### Revised Cooldown Rules

- person-level cooldown: 90 days
- exact company + contact cooldown: 90 days
- company + job family cooldown: 30 days
- exact job posting cooldown: permanent once contacted

Examples:

- emailing two different roles at the same company in different job families can be allowed
- emailing the same recruiter repeatedly for similar roles should be blocked
- recontacting for the exact same posting should not happen

## 14. Review Queue

The review queue is deliberately small and should only contain uncertain targets.

Each queued item should show:

- company
- role title
- posting URL
- selected contact
- confidence tier
- why it was queued
- rendered email preview
- resume choice

Allowed actions:

- approve and send
- approve but edit settings
- skip
- suppress company or address

This keeps the system mostly automatic without sacrificing judgment.

## 15. Stage 5: Sending Engine

The Gmail API approach remains fine.

Recommended limits:

- 10 to 15 initial emails per day
- 5 to 10 follow-ups per day
- 45 to 90 second randomized delay
- business-hour sends only

### Safety Stops

Pause sending automatically if:

- bounce rate exceeds 5 percent over the rolling last 50 sends
- provider warnings suggest abusive or suspicious sending
- review skip rate exceeds 40 percent across the last 20 reviewed targets

That last signal helps detect a bad discovery configuration before it harms sender reputation. Only targets that actually entered the review queue count toward this metric.

## 16. Stage 6: Replies and Follow-Ups

The keyword-based reply detection is a good MVP.

Reply classes should remain:

- positive interest
- referral
- rejection
- auto-reply
- bounce
- unclassified

### Follow-Up Policy

- follow-up 1 after 5 business days
- follow-up 2 after 12 business days
- stop after two follow-ups
- stop immediately after rejection
- stop and notify after referral

For referrals, the system should create a suggested new target rather than trying to auto-contact the referred person immediately.

## 17. Compliance and Risk Language

The legal/compliance section in V2.1 was too absolute.

V2.2 should frame this more carefully:

- design conservatively
- respect company instructions on the posting
- honor opt-outs permanently
- avoid jurisdictions or contact patterns you are not comfortable with
- treat legal interpretations as context-dependent, not guaranteed

This is a design document, not legal advice.

## 18. Metrics

Track:

- jobs discovered
- jobs auto-qualified
- jobs sent to review
- contacts resolved by confidence tier
- initial sends
- follow-ups
- replies
- positive replies
- bounces
- reviewed approvals
- reviewed skips

Two particularly useful ratios:

- `auto_send_rate`
- `review_approval_rate`

If review approval is consistently low, the pipeline is being too aggressive upstream.

## 19. Data Model Changes

V2.1’s schema is close, but V2.2 should add a review-oriented table.

### New Table: `review_queue`

- `id`
- `job_id`
- `person_id`
- `message_id`
- `queue_reason`
- `confidence_tier`
- `created_at`
- `review_status`
- `reviewed_at`
- `review_notes`

Possible `queue_reason` values:

- `pattern_inferred`
- `catch_all_guess`
- `borderline_fit`
- `weak_personalization`
- `dynamic_careers_page`

### Suggested Additions to Existing Tables

Add to `jobs`:

- `job_family`
- `qualification_mode`

Add to `people`:

- `contact_source_type`
- `evidence_snippet`

Add to `messages`:

- `message_quality_score`
- `review_required`

## 20. Local LLM Integration

The optional local LLM section from V2.1 is good and should stay.

The same rule still applies:

- use LLMs for extraction only
- validate extracted fields
- never let the LLM freely author the final email

That is a strong design choice and should not be loosened.

## 21. Build Priority

### Phase 1

- watchlist loader
- ATS pollers
- database schema
- job filtering
- contact discovery

### Phase 2

- template assembly
- review queue
- simple message quality score
- Gmail sending
- reply tracking
- suppression rules

### Phase 3

- local LLM extraction
- richer reporting

If you want the best MVP outcome, build the review queue and simple scoring before trying to make personalization smarter.

## 22. Final Recommendation

V2.2 is the right direction.

It keeps the project personal, lightweight, and automatable while protecting quality where automation is weakest. The right operating model is:

`automate discovery and delivery, review uncertainty, personally handle outcomes`

That gives you a system that still saves major time, but is much less likely to send weak outreach.
