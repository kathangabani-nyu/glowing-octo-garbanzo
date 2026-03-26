# AutoApply

**Fully automated, quality-first job outreach pipeline.** AutoApply discovers open roles from company career pages, filters them against your profile, finds the right people to contact, writes personalized emails from Jinja2 templates, sends via Gmail with pacing and follow-ups, and reports results — all locally, all configurable.

> Built for new grads and career switchers who want to reach hiring teams directly, not just submit into the void.

---

## How It Works

```
                            AutoApply Pipeline
 ┌─────────────────────────────────────────────────────────────────────┐
 │                                                                     │
 │   watchlist.yaml         config.yaml          templates/*.j2        │
 │        │                      │                      │              │
 │        v                      v                      v              │
 │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
 │  │   1.     │  │   2.     │  │   3.     │  │   4.             │    │
 │  │ DISCOVER │─>│  FILTER  │─>│ CONTACTS │─>│    ASSEMBLE      │    │
 │  │   JOBS   │  │   JOBS   │  │          │  │    EMAILS        │    │
 │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘    │
 │   Greenhouse     Title,        SMTP           Role-specific         │
 │   Lever          skills,       verified       Jinja2 templates      │
 │   Ashby          location,     recruiter &    + job context          │
 │   Workday        seniority     hiring mgr                           │
 │   SmartRecr.     filtering     emails                               │
 │        │              │             │               │               │
 │        v              v             v               v               │
 │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
 │  │   5.     │  │   6.     │  │   7.     │  │   8.             │    │
 │  │  REVIEW  │─>│   SEND   │─>│ FOLLOWUP │─>│    REPORT        │    │
 │  │  QUEUE   │  │          │  │          │  │                  │    │
 │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘    │
 │   Human-in-     Gmail API      Reply          Daily metrics,        │
 │   the-loop      OAuth2,        detection,     bounce tracking,      │
 │   approval      daily caps,    scheduled      CSV exports            │
 │   for medium    pacing &       follow-ups                           │
 │   confidence    delays         (Day 3, 7)                           │
 │                                                                     │
 └─────────────────────────────────────────────────────────────────────┘
```

### Company Auto-Discovery (V2.2)

```
  External Sources                    Staging                      Pipeline
 ┌──────────────┐     ┌─────────────────────────────┐     ┌──────────────────┐
 │  YC / Work   │     │    discovered_companies      │     │                  │
 │  at a Startup│────>│                              │     │   companies      │
 │              │     │  ┌─────────┐  ┌───────────┐  │     │   table          │
 ├──────────────┤     │  │  Dedup  │─>│    ATS    │  │     │                  │
 │  BuiltIn     │────>│  │ against │  │ Detection │──────> │  Auto-promoted   │
 │  City Lists  │     │  │ pipeline│  │ (careers  │  │     │  companies join  │
 ├──────────────┤     │  │ + suppr.│  │  page     │  │     │  the daily run   │
 │  (More V2+)  │     │  │  list   │  │  scan)    │  │     │                  │
 └──────────────┘     │  └─────────┘  └───────────┘  │     └──────────────────┘
                      └─────────────────────────────┘
```

---

## Features

| Stage | What it does |
|-------|-------------|
| **Discovery** | Pulls job listings from Greenhouse, Lever, Ashby, Workday, and SmartRecruiters APIs/boards for each watchlist company |
| **Filtering** | Scores jobs against configurable title keywords, exclusion lists, skills, seniority, location, visa requirements, and experience bands |
| **Contacts** | Finds recruiter and hiring manager emails via name permutation + SMTP verification (no paid APIs needed) |
| **Assembly** | Renders personalized emails from role-specific Jinja2 templates with extracted job context (team, technology, company info) |
| **Review Queue** | Human-in-the-loop approval for medium-confidence messages; high-confidence sends automatically |
| **Sending** | Gmail OAuth2 with daily caps (default 12/day), randomized delays (45-90s), and business-hours pacing |
| **Follow-ups** | Reply detection via Gmail API; automated follow-up emails on Day 3 and Day 7 if no response |
| **Reporting** | Daily metrics, bounce rate tracking, CSV exports, and safety circuit breakers |

### Additional Capabilities

- **Domain profiles** — Switch between CS/ML and Finance/IB targeting via `config.domain_profile`
- **Company auto-discovery** — Automatically find companies hiring from YC, BuiltIn, and other sources
- **ATS detection** — Scans company career pages to identify which ATS they use
- **Suppression & cooldowns** — Per-person (90 day) and per-company-role (30 day) cooldowns prevent duplicate outreach
- **Bounce protection** — Automatically pauses sending if bounce rate exceeds threshold
- **Local LLM integration** — Uses Ollama (Llama 3.1) for job detail extraction; no cloud API keys needed
- **SQLite database** — All state tracked locally; zero external dependencies beyond Gmail

---

## Requirements

- **Python 3.10+**
- **Ollama** with `llama3.1:8b` model (for job detail extraction)
- **Google Cloud project** with Gmail API enabled + OAuth Desktop credentials
- **Git** (for version control)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/AutoApply.git
cd AutoApply/autoapply
```

### 2. Create and activate a virtual environment

**Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Ollama (for local LLM)

Download from [ollama.com](https://ollama.com), then pull the model:

```bash
ollama pull llama3.1:8b
```

Verify it's running:
```bash
ollama list
```

### 5. Set up Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Gmail API**:
   - Navigate to **APIs & Services > Library**
   - Search for "Gmail API" and click **Enable**
4. Create OAuth credentials:
   - Go to **APIs & Services > Credentials**
   - Click **Create Credentials > OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON file
5. Rename it to `credentials.json` and place it in the `autoapply/` directory
6. On first run, a browser window will open for OAuth consent — this creates `token.json` automatically

> **Important:** `credentials.json` and `token.json` are in `.gitignore` and should never be committed.

### 6. Configure your profile

Copy the example configs and edit with your details:

```bash
copy config.example.yaml config.yaml        # Windows
cp config.example.yaml config.yaml          # macOS/Linux

copy watchlist.example.yaml watchlist.yaml
cp watchlist.example.yaml watchlist.yaml
```

Edit `config.yaml`:
```yaml
sender:
  name: "Your Name"
  email: "your.email@gmail.com"
  signature: ""  # Optional: "Name | School | email"

job_targets:
  title_keywords:
    - "software engineer"
    - "machine learning engineer"
  title_exclude:
    - "intern"
    - "senior"
    - "director"
  skills:
    - "Python"
    - "PyTorch"
  # ... see config.example.yaml for all options

resume_path: "resumes/your_resume.pdf"
```

Edit `watchlist.yaml` with your target companies:
```yaml
companies:
  - name: "Anthropic"
    domain: "anthropic.com"
    priority: 1
    ats: "greenhouse"
    slug: "anthropic"
    careers_url: "https://anthropic.com/careers"
    jobs_url: "https://boards.greenhouse.io/anthropic"
    job_family_focus: "engineering"
    notes: "AI safety research lab"
```

### 7. Add your resume

```bash
mkdir resumes
# Copy your resume PDF(s) into the resumes/ directory
# Update resume_path and resume_variants in config.yaml
```

---

## Usage

### Run the full pipeline

```bash
cd autoapply
python run_daily.py
```

### Dry run (no emails sent)

```bash
python run_daily.py --dry-run
```

### Run a single stage

```bash
python run_daily.py --stage discovery
python run_daily.py --stage filtering --dry-run
python run_daily.py --stage contacts
python run_daily.py --stage sending --send-limit 3
```

### Available stages

| Stage | Flag | Description |
|-------|------|-------------|
| `discovery` | `--stage discovery` | Pull jobs from ATS endpoints |
| `filtering` | `--stage filtering` | Score and filter jobs |
| `contacts` | `--stage contacts` | Find recruiter/hiring manager emails |
| `assembly` | `--stage assembly` | Build email messages from templates |
| `review` | `--stage review` | Process review queue |
| `sending` | `--stage sending` | Send approved emails via Gmail |
| `followups` | `--stage followups` | Check replies and send follow-ups |
| `reporting` | `--stage reporting` | Generate daily report |

### Company auto-discovery

```bash
python run_company_discovery.py --sources yc,builtin --dry-run   # Preview
python run_company_discovery.py --sources yc                      # Discover + stage
python run_company_discovery.py --promote                         # Promote to pipeline
```

### Review queue

```bash
python -m src.review_cli
```

### Reports

```bash
python -m src.report_cli
```

---

## Project Structure

```
AutoApply/
├── README.md
├── autoapply/
│   ├── config.example.yaml          # Template config (safe to commit)
│   ├── config.yaml                  # Your config (gitignored)
│   ├── watchlist.example.yaml       # Template watchlist (safe to commit)
│   ├── watchlist.yaml               # Your watchlist (gitignored)
│   ├── credentials.json             # Gmail OAuth creds (gitignored)
│   ├── token.json                   # Gmail OAuth token (gitignored)
│   ├── requirements.txt
│   ├── run_daily.py                 # Main pipeline orchestrator
│   ├── run_company_discovery.py     # Auto-discovery CLI
│   ├── src/
│   │   ├── job_discoverer.py        # ATS API integrations
│   │   ├── job_filter.py            # Scoring & filtering engine
│   │   ├── contact_discoverer.py    # Email permutation + SMTP verify
│   │   ├── email_assembler.py       # Jinja2 template rendering
│   │   ├── review_queue.py          # Human review system
│   │   ├── sender.py                # Gmail API sender with pacing
│   │   ├── followup_manager.py      # Reply detection & follow-ups
│   │   ├── reporter.py              # Metrics & reporting
│   │   ├── company_discoverer.py    # Auto-discovery engine
│   │   ├── db.py                    # SQLite database layer
│   │   ├── config.py                # Config/watchlist loaders
│   │   ├── llm_extractor.py         # Ollama LLM integration
│   │   ├── detail_extractor.py      # Job detail parsing
│   │   ├── permutator.py            # Email permutation patterns
│   │   ├── smtp_verifier.py         # SMTP email verification
│   │   └── utils.py                 # Logging, rate limiting
│   ├── templates/
│   │   ├── default.j2               # General outreach template
│   │   ├── software.j2              # SWE-specific template
│   │   ├── ml.j2                    # ML/AI role template
│   │   ├── research.j2              # Research role template
│   │   ├── fullstack.j2             # Fullstack role template
│   │   ├── followup_1.j2            # Day 3 follow-up
│   │   └── followup_2.j2            # Day 7 follow-up
│   └── tests/
│       ├── test_job_discoverer.py
│       ├── test_job_filter.py
│       ├── test_email_assembler.py
│       ├── test_sender.py
│       └── ...
└── logs/
    └── reports/                     # Daily report artifacts
```

---

## Configuration Reference

### `config.yaml`

| Section | Key Fields | Description |
|---------|-----------|-------------|
| `sender` | `name`, `email`, `signature` | Your identity for outgoing emails |
| `sending` | `max_initial_per_day`, `min_delay_seconds` | Sending caps and pacing |
| `safety` | `max_bounce_rate`, `bounce_window` | Circuit breaker thresholds |
| `cooldowns` | `person_days`, `company_job_family_days` | Anti-spam cooldown periods |
| `job_targets` | `title_keywords`, `title_exclude`, `skills`, `seniority`, `locations` | What jobs to target |
| `qualification` | `auto_threshold`, `review_threshold` | Confidence score cutoffs |
| `llm` | `use_local_llm`, `model`, `ollama_url` | Local LLM settings |
| `domain_profile` | `name`, `role_buckets`, `reject_roles` | CS vs Finance targeting |

### `watchlist.yaml`

Each company entry:
```yaml
- name: "Company Name"
  domain: "company.com"
  priority: 1-5          # 1 = highest priority
  ats: "greenhouse"      # greenhouse | lever | ashby | workday | smartrecruiters
  slug: "company-slug"   # ATS board identifier
  careers_url: "https://company.com/careers"
  jobs_url: "https://boards.greenhouse.io/company"
  job_family_focus: "engineering"  # Optional: filter to specific team
  notes: "Any notes"
```

### Supported ATS Platforms

| Platform | Job URL Pattern |
|----------|----------------|
| Greenhouse | `boards.greenhouse.io/{slug}` |
| Lever | `jobs.lever.co/{slug}` |
| Ashby | `jobs.ashbyhq.com/{slug}` |
| Workday | `{instance}.wd{N}.myworkdayjobs.com/{board}` |
| SmartRecruiters | `jobs.smartrecruiters.com/{slug}` |

---

## Safety & Ethics

- **Daily cap**: Default 12 initial emails/day — respectful volume
- **Bounce protection**: Auto-pauses if bounce rate exceeds 5%
- **Cooldowns**: 90-day per-person and 30-day per-company-role cooldowns
- **Suppression list**: Opted-out or dismissed contacts are permanently suppressed
- **Business hours**: Sends only during 8 AM - 6 PM window
- **CAN-SPAM compliance**: Real identity, honest subject lines, opt-out respected

---

## Testing

```bash
cd autoapply
pytest
```

---

## License

Code is provided for personal use. Add a `LICENSE` file if you need standard open-source terms.
