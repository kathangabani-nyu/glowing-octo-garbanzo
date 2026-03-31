Find companies actively hiring for entry-level / new-grad software engineering, ML engineering, backend engineering, or full-stack roles in the US (remote OK).

Use real sources — check recent HN "Who is Hiring" threads, company career pages, job boards, LinkedIn job posts, or any other public source. Prioritize:
- Startups and mid-size tech companies over FAANG (higher response rates)
- Companies where my skills are a genuine fit (Python, PyTorch, FastAPI, AWS,
  ML/DL, MLOps, containerized deployments, LLM integrations)
- Companies that mention specific tech I've used (Redis, Kubernetes, Airflow,
  RAG pipelines, transformer fine-tuning, computer vision) - not a hard rule

For each company, find a REAL person to email — a recruiter, hiring manager,
engineering lead, or founder. Not careers@, hr@, info@, or any generic inbox.
Verify their name is an actual human name, not a mailbox label.

Before doing anything:
```python
import sys; sys.path.insert(0, "autoapply")
from toolkit import *
from toolkit_db import ToolkitDB
db = ToolkitDB("autoapply.db")
```

For each contact:
1. `check_already_contacted(db, email)` — skip if True
2. `check_company_contacted_recently(db, "domain.com", days=30)` — skip if True
3. `verify_email(email)` — skip if rejected, warn me if catch_all
4. If you only have a name but no email, use `generate_email_guesses(first, last, domain)`
   and verify each guess until one passes

Draft a short, personalized email for each. Follow this structure:

**Subject:** [Clear, specific, not corny — e.g. "Full-stack interest in [Company]"
or "Interest in the [Role] at [Company]"]

**Paragraph 1 — Who you are + why you're reaching out:**
I'm Kathan Gabani, currently [status], and I'm reaching out because [role/company/team]
caught my attention.

**Paragraph 2 — What you've been doing (2 concrete things):**
Recently, I've been working on [specific technical work], and I'm especially interested
in [type of work].

**Paragraph 3 — Why this company specifically (1 sharp, specific line):**
What stood out to me about [Company] is [specific observation about their product,
tech stack, problem space, or engineering culture]. That's why this role feels like
a strong fit.

**Close:**
I've attached my resume for your reference — happy to share any relevant projects as well.

Best,
Kathan

Rules for email quality:
- Do NOT use filler like "it aligns closely with the kind of engineering I've been doing"
- Do NOT lead with a company pitch — start with who you are
- The company-specific paragraph should feel like support, not fluff
- Every email should sound like a real person wrote it, not a generated template
- Keep it to 4 short paragraphs max (intro, experience, company-specific, close)
- Always attach resume — the close references it

My details for personalization:
- Kathan Gabani | kdg7224@nyu.edu | MS Computer Engineering @ NYU (May 2026)
- Currently: Fullstack AI/ML Engineer at RoughCut (FastAPI, Redis, ML endpoints,
  CI/CD, containerized deployment) + DL Researcher at NYU Video Lab (fMRI-to-vision,
  multimodal models)
- Past: ML Researcher (hyperspectral imaging, PyTorch, QGIS), Solutions Architect Intern
  (BERT/RoBERTa fine-tuning, AWS, MLflow)
- Published: Dynamic Terrain Generation using Deep GANs (ICAIR 2023)
- Projects: Serverless LLM agent (AWS Lex, RAG, Pinecone, Bedrock), MLOps pipeline
  (ONNX, K8s, Airflow, Ray)
- GitHub: https://github.com/kathangabani-nyu

Pick which experience/projects to highlight based on what's relevant to each specific
company and role. Do not dump everything — choose the 2 most relevant things.

Show me ALL drafts before sending. For each one show:
  -> Company | Role | Contact Name | Email | Verification Status
  -> Full email text

Only send after I say "approved" or "send". Use `send_email()` from the toolkit with
`resume_path="resumes/Kathan Gabani's resume.pdf"`, then `record_send()` for each.
Report a summary when done.
