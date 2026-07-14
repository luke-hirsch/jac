# JAC — Job Application Creator

> A portfolio site and AI-powered job application engine. Feed it a job posting, get a tailored CV, cover letter, and follow-up plan — all grounded in your actual career history.

---

## What it does

Job hunting is repetitive. You copy-paste your CV, tweak bullet points, forget to follow up. JAC automates the mechanical parts so you can focus on the parts that matter.

**Career database** — a structured store of every job, project, skill, certification, education, and language you've had. Not a flat document — a queryable DB you own.

**CV pipeline** — given a job posting, JAC:

1. Runs a deterministic keyword filter (fast, no API calls)
2. Scores every entry with an LLM for relevance
3. Ranks and tailors the shortlist using a deeper job analysis pass

**Personalized portfolio links** — send `/for/acme-corp` on a business card, `/t/<token>` in a DM. Each link shows a curated view of your work, tuned for the recipient. _(in progress)_

**Application automation** — cover letter drafting, follow-up reminders via Celery. _(planned)_

---

## Stack

| Layer      | Tech                                                         |
| ---------- | ------------------------------------------------------------ |
| Backend    | Django 6, Django REST Framework                              |
| Frontend   | React 19 + Vite + TypeScript                                 |
| Database   | SQLite → PostgreSQL                                          |
| LLM        | Multi-provider connector (Anthropic, OpenAI, Google, Ollama) |
| Task queue | Celery + Redis/Valkey (async CV/letter generation)           |

---

## Project layout

```
backend/
  jac/              # career DB models + CV pipeline
  llm_connector/    # reusable multi-provider LLM adapter
  lukehirsch/       # Django config (settings, urls, wsgi)
  spa/              # portfolio / personalized links (in progress)
frontend/           # React app
config/             # nginx
```

---

## CV pipeline

```python
from jac.cv import CV

cv = CV(user_pk=1, domains=["Python"], min_skill_proficiency="advanced")

# Deterministic pass — no LLM, instant
cv.deterministic_filter(job_posting_text)

# LLM scoring — drops below threshold
cv.ai_filter_entries(job_posting_text, threshold=0.4)

# Full agentic pass — analyze job → score with context → filter + sort
analysis = cv.agentic_tailor(job_posting_text)
```

Scored entries get `relevance_score` and `relevance_reason` attached as in-memory attributes.

---

## LLM connector

A standalone Django app that wraps multiple LLM providers behind a single interface. Configure aliases in `settings.LLM`, call via:

```python
from llm_connector import complete, stream, get_client

complete("Summarize this job posting", alias="fast")
```

Supports: Anthropic, OpenAI (including o-series reasoning models), Google, Ollama (custom).

---

## Setup

```bash
# Python env
pyenv virtualenv 3.12 jac
pyenv local jac
pip install -r requirements.txt

# Config
cp .env.example .env   # fill in SECRET_KEY, API keys

# DB
cd backend
python manage.py migrate
python manage.py createsuperuser
```

### Run (dev)

The dev stack is **four processes** — generation runs are async, so the web server alone
is not enough. A run created without a worker sits `pending` until the UI flags it stale
(~30 s) and offers Abort; the queued task expires after 15 min.

```bash
# 1. Redis/Valkey — Celery broker + Channels layer (once, keeps running)
brew services start valkey

# 2. Ollama — the zero-cost default LLM (the desktop app also works)
ollama serve

# 3. Web + WebSockets (daphne rides on runserver)
cd backend && python manage.py runserver

# 4. Generation worker — REQUIRED for CV / cover-letter runs
cd backend && celery -A lukehirsch worker -l info
#    With VECTOR_STORE set to a path (embedded Qdrant), the worker must be the
#    ONLY process touching that path: run it --pool=solo.
cd backend && celery -A lukehirsch worker -l info --pool=solo

```

```bash
# Frontend
cd frontend
npm install
npm run dev
```

Vector store (optional): set VECTOR_STORE to enable the RAG read path — a filesystem path (e.g. ~/.jac-qdrant) runs Qdrant embedded (single process: worker only, --pool=solo), an http(s):// URL targets a Qdrant server (docker-compose phase; dashboard at :6333/dashboard). Unset = every run embeds the full corpus per request. Backfill with python manage.py vector_sync (stop the worker first in embedded mode — the dir is locked).

### Smoke-test the CV pipeline

```bash
python manage.py cv_test --user 1 --job-file path/to/posting.txt
```

Runs all four passes (unfiltered → deterministic → AI filter → agentic) and prints the results.

### Check LLM config

```bash
python manage.py llm_check          # all aliases
python manage.py llm_check default  # specific alias
```

---

## Tests

```bash
cd backend
python manage.py test
```

46 tests covering models, the CV pipeline (mocked LLMs), and LLM wrapper JSON parsing.

---

## License

[CC BY-NC 4.0](LICENSE) — free to use and modify, not for commercial purposes.
