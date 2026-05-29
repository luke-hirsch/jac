# lukehirsch

Portfolio website + Job Application Creator (JAC). Django backend, React frontend, monorepo structure.

## Project Layout

| Path | Purpose |
|------|---------|
| `backend/` | Django project root |
| `backend/lukehirsch/` | Django config package (settings, urls, wsgi, asgi) |
| `backend/jac/` | JAC Django app — career DB models + CV filtering/tailoring pipeline |
| `backend/spa/` | Stub app reserved for the portfolio / personalized-link system (currently empty) |
| `backend/llm_connector/` | Reusable LLM connector Django app (multi-provider) |
| `backend/manage.py` | Django management entrypoint |
| `frontend/` | React 19 + Vite + TypeScript (still the default Vite starter — no routing yet) |
| `config/` | nginx config |
| `requirements.txt` | Python dependencies |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend framework | Django 6.x |
| API layer | Django REST Framework (next up — not yet installed) |
| ASGI / streaming | Daphne (planned, Phase 4) |
| Frontend | React 19 + Vite + TypeScript (react-router planned) |
| Database | SQLite (dev) → PostgreSQL (prod) |
| Task queue | Celery + Redis (planned — follow-up reminders) |
| LLM | `llm_connector` app — Anthropic, OpenAI, Google, custom (Ollama). Per-user configs with Fernet-encrypted API keys. |
| Auth | Django sessions; 2FA planned (Phase 1) |
| Python env | pyenv (`jac` virtualenv) |
| Deployment | Docker Compose + GitHub Actions (planned, Phase 6) |

## Common Commands

### Backend (run from `backend/`)
```bash
python manage.py runserver
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py shell
python manage.py test
python manage.py llm_check                       # check the global default (settings.LLM)
python manage.py llm_check --user 1              # check user 1's LLMConfig rows
python manage.py llm_check --user 1 reasoning    # check a specific alias for user 1
python manage.py cv_test --user 1 --job-file path/to/posting.txt
```

### Frontend (run from `frontend/`)
```bash
npm run dev
npm run build
npm run lint
```

## JAC App (built)

Django app at `backend/jac/`. Today: career DB + CV filtering/tailoring pipeline + Markdown rendering. CRUD API, Application/cover-letter, and follow-up logic not yet built.

Key files:
| File | Purpose |
|------|---------|
| `backend/jac/models.py` | Career DB: `Domain`, `Location`, `CvEntry` (abstract base), `Education`, `Certification`, `Skill`, `Job`, `Project`, `Language` |
| `backend/jac/cv.py` | `CV` class — loads entries per user, deterministic + LLM-based filtering, ranking, and agentic tailoring |
| `backend/jac/llm.py` | JAC-specific prompt wrappers (`extract_job_keywords`, `analyze_job`, `score_entries_for_job`, `score_entries_with_analysis`) — imports from `llm_connector` only |
| `backend/jac/render.py` | `CvRender` class — renders a filtered `CV` to Markdown via `export_md()`. PDF/DOCX will happen on the frontend from API payloads |
| `backend/jac/admin.py` | Admin registrations for all career models |
| `backend/jac/tests.py` | Tests covering models, CV pipeline (mocked LLMs), llm wrappers, and rendering |
| `backend/jac/management/commands/cv_test.py` | Smoke-test CLI: unfiltered → deterministic → ai_filter → agentic_tailor |

## LLM Connector (built)

Django app at `backend/llm_connector/`. Multi-provider gateway with per-user config and encrypted API keys.

Key files:
| File | Purpose |
|------|---------|
| `backend/llm_connector/models.py` | `LLMConfig(user, alias, provider, model, ...)` with Fernet-encrypted `api_key`; `LLMRequestLog` for audit |
| `backend/llm_connector/conf.py` | `get_alias_config(alias, user=None)` — with user: per-user `LLMConfig` → fall back to `settings.LLM["default"]`; without user: `settings.LLM[alias]` |
| `backend/llm_connector/crypto.py` | Fernet helpers reading `settings.LLM_ENCRYPTION_KEY` |
| `backend/llm_connector/client.py` | `LLMClient(alias, user=None)`; `complete()`/`stream()` accept `user=` |
| `backend/llm_connector/admin.py` | `LLMConfigAdmin` with `PasswordInput` for api_key; never exposed in list view |
| `backend/llm_connector/providers/` | One file per provider: anthropic, openai, google, custom (Ollama / OpenAI-compatible HTTP) |

`settings.LLM` only contains the global `default` (free Ollama). Paid providers live exclusively in per-user `LLMConfig` rows — encrypted, managed via Django admin (later: API/SPA).

### CV pipeline

```python
cv = CV(user_pk=user.pk, domains=[...], min_skill_proficiency="advanced")
cv.deterministic_filter(["python", "django"])      # vocabulary substring match
cv.ai_filter_entries(job_text, threshold=0.4)      # LLM scores + drops
cv.ai_rank_entries(job_text)                       # LLM scores + sorts
analysis = cv.agentic_tailor(job_text)             # analyze_job + score_with_analysis + filter + sort
```

Scored entries get `relevance_score` / `relevance_reason` attached as in-memory attrs.

## Roadmap (next, in order)

Full plan in [.claude/plans/roadmap-2026-05-29.md](.claude/plans/roadmap-2026-05-29.md).

1. **2FA / multi-factor auth** — `django-otp` + `django-two-factor-auth` on the admin first, then the API. Done before exposing more API surface (LLMConfig with encrypted API keys lives behind it).
2. **DRF serializers + CRUD APIs** for every career model under `/api/jac/`, user-scoped, `ModelViewSet`-based. Also exposes `LLMConfig` under `/api/llm/configs/`.
3. **Frontend CRUD** — react-router + HTTP client + TanStack Query; one list/edit page per model + an LLMConfig management screen.
4. **Async stack** — Daphne (ASGI), Celery + Redis. First job: scheduled follow-up reminders (requires designing the `Application` / `FollowUp` models).
5. **Comment & docstring cleanup** — strip every "what" comment in `backend/` and `frontend/`; replace with module/class/function docstrings that document the *why* and the public contract.
6. **Deployment** — Dockerfiles for backend (Daphne) and frontend (multi-stage Vite → nginx), `docker-compose.yml` with postgres / redis / celery worker / celery beat / nginx, GitHub Actions for CI (lint + tests + build) and deploy (push to registry → SSH → `docker compose pull && up -d`).

## Portfolio / personalized link system (planned)

To live in `backend/spa/`. Two surfaces:
1. **Public landing** (`/`) — choose-your-path conversation cards; anonymized answers recorded.
2. **Personalized views** — curated subset per recipient:
   - `/for/<slug>` — stable, human-readable (business cards, LinkedIn)
   - `/t/<token>` — UUID token, private and revocable (email, DMs)

`PortfolioLink` (planned) will store both identifiers; `sections` JSONField will control which content sections appear and optional message/filter overrides. JAC will create these automatically when generating applications.

### Planned React routing
```
/           → PublicLanding     (choose-your-path)
/for/:slug  → PersonalizedView  (human-readable link)
/t/:token   → PersonalizedView  (private token link)
```

## Conventions

- **`backend/llm_connector/`** is the LLM gateway — `settings.LLM["default"]` is the free fallback; paid configs live in per-user `LLMConfig` rows. Call via `from llm_connector import complete, stream, get_client` and always pass `user=` for user-driven flows so personal configs apply.
- **`backend/jac/llm.py`** holds JAC-specific prompt wrappers — imports from `llm_connector`, never from `anthropic`/`openai` SDKs directly. Every wrapper accepts `user=` and forwards it.
- All career models inherit from `CvEntry` (abstract): every entry is scoped to a `User` and carries `description`, `created_at`, `updated_at`, `updated_by`
- LLM prompt wrappers return parsed JSON (`_parse_json` strips ```json fences and raises with context on failure)
- All settings secrets use `os.getenv()` — never hardcoded values. Variables: `SECRET_KEY`, `LLM_ENCRYPTION_KEY` (Fernet key for LLMConfig api_key encryption), `OLLAMA_URL`, `ALLOWED_HOST`. Paid-provider API keys are stored per-user in `LLMConfig`, not in env.
- Use Django ORM exclusively — no raw SQL
- Register every model in the app's `admin.py` so Django Admin is always usable
- API endpoints (when added): JAC under `/api/jac/`, portfolio under `/api/portfolio/` (or `/api/spa/`)
- Secrets in `.env` at repo root — never commit them
