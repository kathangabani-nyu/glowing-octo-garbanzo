# Daily outreach — agent instructions (copy to `prompts/daily_outreach.md`)

Use this file as your **private** working copy. It is gitignored so your name, email, school, and links never land in the remote repository.

## How to use

1. Copy this file to `prompts/daily_outreach.md` and fill in every `YOUR_*` placeholder.
2. In your AI assistant (Cursor, Claude Code, etc.), open `prompts/daily_outreach.md` and run a session against it.
3. The assistant researches companies and drafts emails; you approve before anything is sent.

---

Find companies actively hiring for roles that match **your** target (e.g. entry-level / new-grad software, ML, backend, full-stack — adjust as needed). Prefer US roles; remote OK if that matches your search.

Use real sources — recent HN "Who is Hiring" threads, career pages, job boards, LinkedIn, or other public listings. Prioritize companies where your stack and interests are a genuine fit.

For each company, find a **real person** to email — recruiter, hiring manager, engineering lead, or founder. Avoid generic inboxes (`careers@`, `hr@`, `info@`). Verify the name reads like an actual human, not a mailbox label.

## Toolkit bootstrap (run in the assistant’s Python context)

Working directory should be the **repository root** (parent of `autoapply/`), or ensure `autoapply` is on `sys.path`:

```python
import sys
sys.path.insert(0, "autoapply")
from toolkit import *
from toolkit_db import ToolkitDB
db = ToolkitDB("autoapply/autoapply.db")  # or "autoapply.db" if cwd is autoapply/
```

## Per-contact checks

For each contact:

1. `check_already_contacted(db, email)` — skip if `True`
2. `check_company_contacted_recently(db, "domain.com", days=30)` — skip if `True`
3. `verify_email(email)` — skip if rejected; warn if catch-all (`check_catch_all` / verifier behavior as documented in code)
4. If you only have a name, use `generate_email_guesses(first, last, domain)` and verify each guess until one passes

## Email style

Draft short, personalized messages. Your `daily_outreach.md` copy should spell out:

- Your opening line (name, role, school or background)
- Subject-line rules
- Paragraph structure and tone
- Mandatory sign-off (LinkedIn, GitHub, etc.)
- What **not** to do (filler phrases, generic company praise, etc.)

Always attach your resume when sending; the close should reference it.

## Before sending

Show **all** drafts first. For each:

- Company | Role | Contact name | Email | verification status  
- Full email text  

Send only after you explicitly say **approved** / **send**.

Use `send_email()` from the toolkit with your resume path, then `record_send()` for each successful send. Use `dry_run=True` on `send_email()` until you are ready for real delivery.

Report a short summary when finished.

---

## Your details (replace placeholders in your private `daily_outreach.md`)

- **Name / email / headline:** YOUR_NAME | YOUR_EMAIL | YOUR_HEADLINE
- **Current roles / projects:** YOUR_CURRENT_BULLETS
- **Past experience:** YOUR_PAST_BULLETS
- **Links:** YOUR_LINKEDIN_URL | YOUR_GITHUB_URL
- **Resume file:** `resumes/YOUR_RESUME.pdf` (path relative to repo or `autoapply/` — match how you call `send_email`)
