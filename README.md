# AutoApply

**AI-assisted, human-approved job outreach.** You use a coding agent (Cursor, Claude Code, etc.) with a **private prompt** that describes your background and rules. The agent researches real roles and people, drafts personalized emails, and uses a small **Python toolkit** on your machine for verification, deduplication, Gmail sending, and a local SQLite log.

The older **fully automated pipeline** (`run_daily.py`: ATS discovery → filter → assemble → send) is still in the repo for advanced use, but the default workflow is: **prompt + toolkit + your approval**.

---

## How the new workflow works

1. **You** maintain `prompts/daily_outreach.md` (gitignored) with your story, constraints, and email structure — start from `prompts/daily_outreach.example.md`.
2. **Agent** finds companies and contacts using public sources, runs checks through `toolkit` / `ToolkitDB`, and drafts messages.
3. **You** review drafts; only after you say to send does the agent call `send_email()` and `record_send()`.

Mechanical pieces live under `autoapply/`:

| Piece | Role |
|--------|------|
| `toolkit.py` | `verify_email`, `generate_email_guesses`, `send_email`, `check_*`, `record_send`, … |
| `toolkit_db.py` | SQLite: outreach log, suppressions, domain patterns |
| `src/sender.py` | Gmail API: MIME build, resume attachment, send |
| `block_company.py` | CLI to suppress a company domain (e.g. interview in progress) |

Database file (local, not committed): e.g. `autoapply/autoapply.db` (see `.gitignore`).

---

## Requirements

- **Python 3.10+**
- **Google Cloud** project with **Gmail API** enabled and **OAuth desktop** credentials
- **Git**

Optional (only if you use the **legacy** `run_daily.py` pipeline): **Ollama** with a local model for job-detail extraction — see [ollama.com](https://ollama.com).

---

## Quick setup (new users)

### 1. Clone and enter the repo

```bash
git clone https://github.com/YOUR_USERNAME/AutoApply.git
cd AutoApply
```

### 2. Virtual environment and dependencies

**Windows:**

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Gmail API credentials

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Gmail API** for a project.
2. Create **OAuth client ID** → application type **Desktop app**.
3. Download the JSON and save it as **`autoapply/credentials.json`**.
4. First time you send mail, complete the browser consent flow; **`autoapply/token.json`** is created automatically.

Both files are listed in `.gitignore` — do not commit them.

### 4. Private files you must create locally (not in Git)

These paths are ignored or should stay local so the public repo never contains your identity, targets, or tokens:

| File / directory | Purpose |
|------------------|---------|
| `prompts/daily_outreach.md` | Your full agent instructions (copy from `prompts/daily_outreach.example.md`) |
| `autoapply/credentials.json` | Gmail OAuth client secret JSON |
| `autoapply/token.json` | Gmail OAuth token (created after first auth) |
| `autoapply/config.yaml` | Sender identity and settings for legacy pipeline / tooling that reads config |
| `autoapply/watchlist.yaml` | Company list for legacy pipeline |
| `autoapply/*.db` | SQLite outreach database |
| `autoapply/resumes/` | PDF resume(s) |
| `logs/` | Optional local logs or exports at repo root |

**Minimum for the prompt + toolkit workflow:** `credentials.json`, `token.json` (after first auth), `prompts/daily_outreach.md`, and a resume file whose path you pass into `send_email(..., resume_path=...)`.

**Templates for YAML config:**

```bash
cp autoapply/config.example.yaml autoapply/config.yaml
cp autoapply/watchlist.example.yaml autoapply/watchlist.yaml
```

Edit `config.yaml` with your sender name, email, and `resume_path`.

### 5. Run an outreach session

1. Open `prompts/daily_outreach.md` in your editor.
2. Start an agent chat with that file as context (or paste its contents).
3. Ensure the agent’s Python cwd is the **repo root** (or adjust `sys.path` / DB path as in the example prompt).

The example prompt shows importing from `autoapply` and opening `ToolkitDB` — match paths to where you run code from.

---

## Toolkit usage (summary)

From repository root, typical bootstrap:

```python
import sys
sys.path.insert(0, "autoapply")
from toolkit import *
from toolkit_db import ToolkitDB

db = ToolkitDB("autoapply/autoapply.db")
```

Then use `check_already_contacted`, `check_company_contacted_recently`, `verify_email`, `generate_email_guesses`, `send_email`, and `record_send` as described in `prompts/daily_outreach.example.md`.

**Dry run:** `send_email(..., dry_run=True)` builds the message without sending.

**Block a company** (optional):

```bash
cd autoapply
python block_company.py --domain example.com --company "Example Inc" --reason "Interview scheduled"
```

---

## Legacy automated pipeline (optional)

End-to-end ATS discovery, filtering, templated assembly, and scheduled sending:

```bash
cd autoapply
python run_daily.py --help
```

That path expects `config.yaml`, `watchlist.yaml`, Ollama (if using local LLM extraction), and the same Gmail credentials. See `config.example.yaml` and `watchlist.example.yaml` for shape.

---

## Project layout (abbreviated)

```
AutoApply/
├── README.md
├── requirements.txt              # wraps autoapply/requirements.txt
├── prompts/
│   ├── daily_outreach.example.md # safe to commit; copy → daily_outreach.md
│   └── daily_outreach.md         # gitignored — your private prompt
├── autoapply/
│   ├── toolkit.py
│   ├── toolkit_db.py
│   ├── block_company.py
│   ├── credentials.json          # gitignored — you create
│   ├── token.json                # gitignored — created on first OAuth
│   ├── config.example.yaml
│   ├── watchlist.example.yaml
│   ├── run_daily.py              # legacy orchestrator
│   ├── src/                      # Gmail, SMTP verify, DB, …
│   └── templates/                # Jinja2 — used by legacy assembler
└── logs/                         # gitignored if under repo root
```

---

## Testing

```bash
cd autoapply
pytest
```

---

## Safety & ethics

Use conservative volume, real identity, accurate subjects, and honor opt-outs. The toolkit records sends and supports suppressions so you do not double-contact people or domains. You remain responsible for compliance with applicable law and platform terms.

---

## License

Code is provided for personal use. Add a `LICENSE` file if you need standard open-source terms.
