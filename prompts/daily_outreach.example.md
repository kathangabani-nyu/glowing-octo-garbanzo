# Daily outreach — generic CS / ML template (safe to commit)

**Customize:** Copy this file to `prompts/daily_outreach.md` (gitignored) and replace every `YOUR_*` placeholder with your real details. The committed copy stays generic so the repo stays shareable.

---

Find companies actively hiring for **entry-level / new-grad** software engineering, ML engineering, backend engineering, or full-stack roles in the **US** (remote OK if that matches your search).

Use **real sources** — recent HN "Who is Hiring" threads, company career pages, job boards, LinkedIn job posts, or any other public source. Prioritize:

- **Startups and mid-size tech** over megacaps when you want higher reply rates (adjust to taste)
- Companies where **your stack is a genuine fit** — e.g. Python, PyTorch, FastAPI, AWS, ML/DL, MLOps, containerized deployments, LLM integrations
- Postings that mention tech you have used (e.g. Redis, Kubernetes, Airflow, RAG, transformer fine-tuning, computer vision) — useful signal, not a hard filter

For each company, find a **real person** to email — a recruiter, hiring manager, engineering lead, or founder. **Not** `careers@`, `hr@`, `info@`, or other generic inboxes. Confirm the name looks like an actual human, not a mailbox label.

## Toolkit bootstrap (before any outreach)

```python
import sys
sys.path.insert(0, "autoapply")
from toolkit import *
from toolkit_db import ToolkitDB
db = ToolkitDB("autoapply/autoapply.db")  # use "autoapply.db" if your cwd is autoapply/
```

## Per-contact checks

For each contact:

1. `check_already_contacted(db, email)` — skip if `True`
2. `check_company_contacted_recently(db, "domain.com", days=30)` — skip if `True`
3. `verify_email(email)` — skip if rejected; warn me if the domain looks **catch-all**
4. If you only have a name but no email, use `generate_email_guesses(first, last, domain)` and verify each guess until one passes

## Email structure and tone

**Subject:** Clear and specific, not corny — e.g. `"Full-stack interest in [Company]"` or `"Interest in the [Role] at [Company]"`.

**Paragraph 1 — Who you are + why this role/company caught your attention**  
Open with your real one-liner, then a natural reason tied to the role or team. Example shape (replace with my facts):

> "I'm YOUR_NAME, a YOUR_DEGREE candidate in YOUR_FIELD at YOUR_SCHOOL (YOUR_GRAD_DATE)..."

**Paragraph 2 — Why this company specifically**  
One sharp, specific line about what stands out (product, technical bet, team mandate) and why that work is interesting to you — not generic praise.

**Paragraph 3 — Relevant experience (exactly two concrete items)**  
Two concrete things you have built or shipped (e.g. production API + caching, training/fine-tuning pipeline, K8s batch jobs, RAG service) and a one-line tie to their stack or problem.

**Close (always include links)**  
Mention the attached resume and point to GitHub for projects.

Sign-off:

```
Best,
YOUR_FULL_NAME
LinkedIn: YOUR_LINKEDIN_URL
GitHub: YOUR_GITHUB_URL
```

## Quality rules

- Do **not** use filler like "it aligns closely with the kind of engineering I've been doing"
- Do **not** open with a company pitch — start with **who you are**
- The company paragraph should feel like **substance**, not fluff
- Every email should read like a human wrote it, not a template
- **Max four short paragraphs** (intro, company-specific, two-bullet experience, close)
- **Always** attach the resume when sending — the close must reference it
- **Always** include both LinkedIn and GitHub in the sign-off

## My details for personalization (fill in your private copy)

Replace this block with your real bullets; pick **only the two most relevant** lines per email — never dump the whole list.

- **Identity:** YOUR_FULL_NAME | YOUR_EMAIL | YOUR_DEGREE in YOUR_FIELD @ YOUR_SCHOOL (YOUR_GRAD_DATE)
- **Current:** YOUR_CURRENT_ROLE_OR_INTERNSHIP — stack keywords (e.g. FastAPI, Redis, ML endpoints, CI/CD, containers)
- **Research / secondary role (if any):** YOUR_LAB_OR_PROJECT — topic (e.g. multimodal models, systems for ML)
- **Past:** YOUR_PRIOR_ROLES — tools (e.g. PyTorch, cloud, MLflow)
- **Public work (optional):** YOUR_PUBLICATION_OR_TALK
- **Projects:** YOUR_FLAGSHIP_PROJECTS — short labels (e.g. serverless LLM + RAG, MLOps: ONNX, K8s, orchestration)
- **GitHub:** YOUR_GITHUB_URL

## Approval and sending

Show **all** drafts before sending. For each:

- `Company | Role | Contact Name | Email | Verification status`
- Full email body

Send **only** after I say **"approved"** or **"send"**.

Use `send_email()` with `resume_path="resumes/YOUR_RESUME_FILENAME.pdf"` (path must match where the file lives relative to how you resolve paths), then `record_send()` for each successful send. Use `dry_run=True` until I confirm live send.

Report a short summary when done.
