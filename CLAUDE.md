# AutoApply - AI-Assisted Job Outreach

## What this is
A toolkit for AI-assisted cold email outreach. You, the AI agent, research
companies and contacts, draft personalized emails, verify addresses, and
send via Gmail with human approval before every send.

## Daily workflow
When the user asks you to find companies and send outreach emails:

1. Research companies. Browse the web, check job boards, HN "Who is Hiring"
   threads, LinkedIn company pages, and similar sources. Find companies that
   match the user's target criteria.
2. Find real contacts. For each company, identify an actual person such as a
   recruiter, hiring manager, engineering lead, or founder with a verifiable
   name and email. Do not use generic inboxes like `careers@`, `hr@`, `info@`,
   `jobs@`, or guessed placeholders that are not clearly tied to a person.
3. Check dedup. Before drafting, call `check_already_contacted()` and
   `check_company_contacted_recently()`. Skip anyone already contacted.
4. Verify emails. Call `verify_email()` on every address before drafting. If it
   returns `rejected`, do not send. If it returns `catch_all`, warn the user.
5. Draft emails. Write genuinely personalized outreach that references specific
   work, products, roles, or team context. Avoid robotic filler and generic
   phrasing.
6. Present drafts for review. Show all drafts before sending, including
   recipient name, email, company, job title, and full email text. Wait for
   explicit user approval.
7. Send approved emails. Use `send_email()` from the toolkit. Record each send
   with `record_send()`.
8. Report results. Summarize who was emailed, what was skipped, and any
   verification concerns or delivery problems.

## User profile
- Name: Kathan Gabani
- Email: `kdg7224@nyu.edu`
- Master's in Computer Engineering at NYU, graduating May 2026
- Recent experience: Fullstack AI/ML Engineer Intern using FastAPI, Redis, AWS,
  and CI/CD
- GitHub: [kathangabani-nyu](https://github.com/kathangabani-nyu)
- Target roles: Software Engineer, ML Engineer, Backend Engineer,
  Full-Stack Engineer, Platform Engineer, Research Engineer
- Target seniority: Entry-level, New Grad
- Location: Open to US-based roles, remote OK

## Toolkit usage
```python
import sys
sys.path.insert(0, "autoapply")

from toolkit import *
from toolkit_db import ToolkitDB

db = ToolkitDB("autoapply.db")

# Check if already contacted
check_already_contacted(db, "jane@company.com")
check_company_contacted_recently(db, "company.com", days=30)

# Verify email before sending
result = verify_email("jane@company.com")
# result.status: "verified", "rejected", "catch_all", "greylisted", "timeout", "error"

# Generate email guesses from a name
guesses = generate_email_guesses("Jane", "Smith", "company.com")

# Send only after user approval
send_result = send_email(
    "jane@company.com",
    "Subject",
    "Body",
    "Kathan Gabani",
    "kdg7224@nyu.edu",
    resume_path="resumes/Kathan Gabani's resume.pdf",
)

# Record for dedup
record_send(
    db,
    "jane@company.com",
    "Jane Smith",
    "company.com",
    "Company Inc",
    "Software Engineer",
    "https://example.com/jobs/123",
    "Subject",
    "Body",
    send_result["gmail_message_id"],
    gmail_thread_id=send_result.get("gmail_thread_id"),
    resume_used="resumes/Kathan Gabani's resume.pdf",
)

# Rate-limit awareness
get_today_send_count(db)
```

## Rules
- Never send without user approval.
- Never email generic inboxes like `careers@`, `hr@`, `info@`, or `jobs@`.
- Never greet with a non-name such as "Hi Program" or "Hi Careers".
- Always verify emails before sending.
- Always check dedup before drafting.
- No hard daily send limit — use judgment based on email quality.
- If a domain is catch-all, warn the user and let them decide.
