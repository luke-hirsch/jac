# lukehirsch

Portfolio website + Job Application Creator (JAC). Django backend, React frontend, monorepo structure.

## Project Layout

| Path                     | Purpose                                                                                                                                                                                                                                                          |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`               | Django project root                                                                                                                                                                                                                                              |
| `backend/lukehirsch/`    | Django config package (settings, urls, wsgi, asgi) + shared DRF utilities (`permissions.py`, `mixin.py`, `AdminRequireMfaMiddleware`, `HarassmentResistantAccountAdapter`, `ReadableConsoleEmailBackend`)                                                        |
| `backend/jac/`           | JAC Django app — career DB + CV filtering/tailoring pipeline + full DRF CRUD at `/api/jac/`. Cover-letter generation + German output are the next backend work (roadmap Phase 4); `JobPosting`/`Application`/`FollowUp` tracking models deferred to the outview. |
| `backend/spa/`           | Portfolio + per-user profile app. `UserProfile` + `/api/spa/profile/` shipped; `PortfolioLink` / `VisitorResponse` still planned.                                                                                                                                |
| `backend/llm_connector/` | Reusable multi-provider LLM connector with per-user encrypted configs                                                                                                                                                                                            |
| `backend/manage.py`      | Django management entrypoint                                                                                                                                                                                                                                     |
| `frontend/`              | React 19 + Vite + TypeScript — TanStack Router/Query/Form/Table + Tailwind v4 + shadcn/ui; auth + account pages + full `/cv/*` CRUD UI shipped                                                                                                                   |
| `config/`                | nginx config                                                                                                                                                                                                                                                     |
| `requirements.txt`       | Python dependencies                                                                                                                                                                                                                                              |

## Tech Stack

| Layer             | Technology                                                                                                                                                                                                                                                                                                |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend framework | Django 6.x                                                                                                                                                                                                                                                                                                |
| API layer         | Django REST Framework — jac CRUD + llm_connector + spa profile wired at `/api/jac/` + `/api/llm/` + `/api/spa/profile/`. Pagination + django-filter + drf-spectacular (OpenAPI at `/api/schema/`, Swagger at `/api/docs/`). Tailor / cover-letter generation endpoints pending (roadmap Phase 4).         |
| Auth              | `django-allauth[mfa,usersessions]` in **headless mode** (TOTP + WebAuthn passkeys + recovery codes + multi-session management at `/_allauth/browser/v1/auth/sessions`). Mandatory email verification. Admin MFA gate enforced by `lukehirsch.middleware.AdminRequireMfaMiddleware`.                       |
| ASGI / streaming  | Daphne + Channels (Redis layer) — wired in settings, no consumers / routing yet                                                                                                                                                                                                                           |
| Frontend          | React 19 + Vite + TypeScript. Stack locked 2026-06-02: TanStack Router + Query + Form + Table, Tailwind v4, shadcn/ui. Phase 2a (foundation + auth guard) + 2b (full auth + account pages) + 2c (JAC CRUD UI) shipped per [.claude/plans/phase-2c-setup-guide.md](.claude/plans/phase-2c-setup-guide.md). |
| Database          | SQLite (dev) → PostgreSQL (prod, env-configurable) — `settings.DATABASES` branches on `DEBUG`                                                                                                                                                                                                             |
| Task queue        | Celery + Redis — full settings block in place; no `celery.py` / tasks yet                                                                                                                                                                                                                                 |
| Cache             | Redis (`django.core.cache.backends.redis.RedisCache`)                                                                                                                                                                                                                                                     |
| LLM               | `llm_connector` app — Anthropic, OpenAI, Google, custom (Ollama). Per-user configs with Fernet-encrypted API keys.                                                                                                                                                                                        |
| Email             | Console backend in dev (DEBUG=True); SMTP via env in prod (Strato by default — `no-reply@luke-hirsch.de`)                                                                                                                                                                                                 |
| Python env        | pyenv (`jac` virtualenv)                                                                                                                                                                                                                                                                                  |
| Deployment        | Docker Compose + GitHub Actions → IONOS (planned — see roadmap Phase 7)                                                                                                                                                                                                                                   |

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
python manage.py cv_import --user 1 ...          # bulk-import career entries from JSON
python manage.py cv_export --user 1 --file cv.json  # dump a user's CV to JSON (round-trips with cv_import)
python manage.py seed_default_domains            # idempotently create the shared system-default Domain taxonomy (--prune to trim)
```

### Frontend (run from `frontend/`)

```bash
npm run dev
npm run build
npm run lint
```

## JAC App

Django app at `backend/jac/`. Today: career DB (8 entry types + `ResumeSnippet`) + the CV filtering/tailoring pipeline + Markdown rendering + full DRF CRUD at `/api/jac/`. **Next backend work (roadmap Phase 4):** dogfood + refine the CV pipeline against real postings, then **cover-letter generation** — a `CoverLetter` class (new `backend/jac/letter.py`) that stitches the user's hand-written `ResumeSnippet`s onto the tailored CV with the same AI-escalation tiers, rendered via a `LetterRender` — and **German output localization** (Django i18n labels + a glossary-protected LLM `translate_entries` wrapper cached in a `CvEntry.translations` JSONField; selection already matches cross-language, only rendering needs it). The `JobPosting`/`Application`/`FollowUp` tracking models and the CV import wizard are deferred (PDF export → Phase 5c; tracking + import → outview).

Key files:
| File | Purpose |
|------|---------|
| [backend/jac/models.py](backend/jac/models.py) | Career DB: `Domain`, `Location`, `CvEntry` (abstract base), `Education`, `Certification`, `Skill`, `Job`, `Project`, `Language` — every entry user-scoped |
| [backend/jac/cv.py](backend/jac/cv.py) | `CV` class — loads entries per user, deterministic + LLM filtering/ranking, **tiered fallback pipeline** (`ai_tailor_with_fallback`) covering conversational → filter → keyword → deterministic → unfiltered |
| [backend/jac/stopwords.py](backend/jac/stopwords.py) | Multi-language stopword sets (strict + loose) used by `extract_keywords` |
| [backend/jac/llm.py](backend/jac/llm.py) | Prompt wrappers (`extract_job_keywords`, `analyze_job`, `score_entries_for_job`, `score_entries_with_analysis`, `tailor_cv_conversationally`) — imports from `llm_connector` only |
| [backend/jac/render.py](backend/jac/render.py) | `CvRender.export_md()` — structural Markdown only (no prose). A `LetterRender` + PDF export land in roadmap Phase 4c / 5c |
| [backend/jac/serializers.py](backend/jac/serializers.py) | DRF serializers for all 8 career models + `ResumeSnippet` (read-only computed `Skill.years_of_experience` alongside the writable `years_of_experience_override`); `ScopeDomainsToUserMixin` extending the project-level base |
| [backend/jac/admin.py](backend/jac/admin.py) | Admin registrations for all career models |
| [backend/jac/tests.py](backend/jac/tests.py) | Tests covering models, CV pipeline (mocked LLMs), llm wrappers, and rendering |
| [backend/jac/management/commands/cv_test.py](backend/jac/management/commands/cv_test.py) | Smoke-test CLI: unfiltered → deterministic → ai_filter → agentic_tailor |
| [backend/jac/management/commands/cv_import.py](backend/jac/management/commands/cv_import.py) | Bulk-import career entries |

## SPA App (in progress)

Django app at `backend/spa/`. Holds the per-user profile + auth-related signals today; will gain the portfolio link system in the roadmap outview (post-deployment).

| File                                                     | Purpose                                                                                                                                                                                                                                                                                                                                                         |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [backend/spa/models.py](backend/spa/models.py)           | `UserProfile` (one-to-one with `auth.User`, auto-created via signal): identity (display_name, avatar, bio), professional contact (phone, website, linkedin_url, github_url), locale (timezone), UI prefs (theme, contrast), notifications (email_reminders)                                                                                                     |
| [backend/spa/serializers.py](backend/spa/serializers.py) | `UserProfileSerializer` — exposes every profile field; `user` is hidden + defaulted to the request user                                                                                                                                                                                                                                                         |
| [backend/spa/views.py](backend/spa/views.py)             | `UserProfileView` (`RetrieveUpdateAPIView`) — GET/PUT/PATCH `request.user.profile`. `AccountDeleteView` — `DELETE /api/spa/account/`; gates on allauth's `did_recently_authenticate`, returning the 401 + `flows:[{id:"reauthenticate"}]` shape that the frontend's `withReauth` picks up. allauth headless has no account-delete endpoint, so this is our own. |
| [backend/spa/urls.py](backend/spa/urls.py)               | `/api/spa/profile/` → `UserProfileView`; `/api/spa/account/` → `AccountDeleteView`                                                                                                                                                                                                                                                                              |
| [backend/spa/signals.py](backend/spa/signals.py)         | `on_mfa_authenticator_used` — sets `session["mfa_authenticated"]=True` after any successful allauth authenticator use, so `AdminRequireMfaMiddleware` lets the staff user through to `/admin/`                                                                                                                                                                  |
| [backend/spa/apps.py](backend/spa/apps.py)               | `SpaConfig.ready()` wires `allauth.mfa.signals.authenticator_used` → `on_mfa_authenticator_used`                                                                                                                                                                                                                                                                |
| [backend/spa/admin.py](backend/spa/admin.py)             | `UserProfile` admin                                                                                                                                                                                                                                                                                                                                             |
| [backend/spa/tests.py](backend/spa/tests.py)             | Full allauth headless auth-flow tests (signup → verify → login, password reset/change, TOTP enroll w/ reauth, MFA login challenge, recovery codes, rate limit) + `AdminRequireMfaMiddleware` gate tests + `UserProfileView` scoping tests                                                                                                                       |

`PortfolioLink`, `VisitorResponse`, and public/personalized views are **not** built — see the roadmap outview.

## Frontend

React 19 + Vite + TS at `frontend/`. Phase 2a (foundation) + 2b (auth + account pages) + 2c (JAC CRUD UI) shipped per [.claude/plans/phase-2c-setup-guide.md](.claude/plans/phase-2c-setup-guide.md). Backend talks via same-origin Vite proxy (`/api`, `/_allauth`, `/admin`, `/media`, `/static` → `localhost:8000`); router auto-generates `src/routeTree.gen.ts` on file changes.

| Path                                                                                              | Purpose                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [frontend/src/routes/\_\_root.tsx](frontend/src/routes/__root.tsx)                                | Root route — `<Outlet />` + `<Toaster />` (sonner)                                                                                                                                                                                                                                                                                                                                                  |
| [frontend/src/routes/\_authenticated.tsx](frontend/src/routes/_authenticated.tsx)                 | Auth gate + `AuthedLayout` (header + sign-out). `beforeLoad` calls `fetchSession`; routes anonymous users to `/auth/login`, pending verify-email to `/auth/verify-email`, pending MFA to `/auth/mfa-challenge`                                                                                                                                                                                      |
| [frontend/src/routes/\_authenticated/account.tsx](frontend/src/routes/_authenticated/account.tsx) | `AccountLayout` — left nav (Profile / Email / Security / Danger) wrapping `<Outlet />` in a `Card`                                                                                                                                                                                                                                                                                                  |
| [frontend/src/routes/\_authenticated/account/](frontend/src/routes/_authenticated/account/)       | `profile.tsx` (PATCH `/api/spa/profile/`), `email.tsx` (allauth `/account/email` + inline verify-code form per unverified row → POST `/auth/email/verify`), `security.tsx` (composes the security panels), `danger.tsx` (sessions list via `/auth/sessions` + sign out + `DELETE /api/spa/account/`)                                                                                                |
| [frontend/src/routes/auth.tsx](frontend/src/routes/auth.tsx)                                      | Public `auth` layout — centered card with "back to home" link. `beforeLoad` calls `fetchSession` to bootstrap the `csrftoken` cookie before any child route POSTs (allauth's `browser_view` decorator sets it via `get_token`).                                                                                                                                                                     |
| [frontend/src/routes/auth/](frontend/src/routes/auth/)                                            | `login.tsx`, `signup.tsx`, `verify-email.tsx`, `request-reset.tsx`, `reset-password.$key.tsx`, `mfa-challenge.tsx` (branches by `useAuth().status`: anonymous w/ pending stage → `/auth/2fa/authenticate`; already authenticated, e.g. admin gate step-up → `/auth/2fa/reauthenticate`)                                                                                                             |
| [frontend/src/lib/api.ts](frontend/src/lib/api.ts)                                                | `api<T>()` fetch wrapper — same-origin cookies, auto-CSRF for unsafe methods, throws typed `ApiError`; `allauthErrorsByField` flattens allauth error arrays                                                                                                                                                                                                                                         |
| [frontend/src/lib/auth.ts](frontend/src/lib/auth.ts)                                              | `fetchSession` (treats 401/410 as session payloads, not errors), `signOut` (swallows the 401-status post-signout payload), `useAuth` hook, `useInvalidateSession`, `SessionResponse` / `AuthStatus` types                                                                                                                                                                                           |
| [frontend/src/lib/auth-flow.ts](frontend/src/lib/auth-flow.ts)                                    | `resolveAuthOutcome` — decodes allauth success body OR thrown `ApiError` into a single discriminated `AuthOutcome` the call site can switch on                                                                                                                                                                                                                                                      |
| [frontend/src/lib/reauth.ts](frontend/src/lib/reauth.ts)                                          | `withReauth(fn)` — runs `fn`; on any 401 whose body lists `reauthenticate` as an _available_ flow (no `is_pending` gate, since allauth returns `{id:"reauthenticate"}` bare for step-up 401s), prompts for password, posts to `/auth/reauthenticate`, then retries. Wrap any destructive mutation.                                                                                                  |
| [frontend/src/lib/webauthn.ts](frontend/src/lib/webauthn.ts)                                      | base64url codec + `decode/encodeCreationOptions`/`RequestOptions` + `encodeAttestation`/`Assertion`. Exports `EncodedCreationOptions` / `EncodedRequestOptions` types                                                                                                                                                                                                                               |
| [frontend/src/lib/form.ts](frontend/src/lib/form.ts)                                              | `zodValidator(schema)` — adapts a Zod schema for TanStack Form's `validators.onChange`                                                                                                                                                                                                                                                                                                              |
| [frontend/src/lib/query.ts](frontend/src/lib/query.ts)                                            | The single `QueryClient`                                                                                                                                                                                                                                                                                                                                                                            |
| [frontend/src/components/security/](frontend/src/components/security/)                            | `change-password.tsx`, `totp-panel.tsx` (QR enroll + recovery codes; treats `GET /authenticators/totp` 404 as "not enrolled yet, here's `meta.secret`"), `passkey-panel.tsx` (lists via `/account/authenticators` filtered by `type==="webauthn"` — the webauthn-specific GET is _register_, not _list_, and the response wraps creation options in `data.creation_options`) — all use `withReauth` |
| [frontend/src/components/passkey-button.tsx](frontend/src/components/passkey-button.tsx)          | "Sign in with passkey" button on the login screen                                                                                                                                                                                                                                                                                                                                                   |
| [frontend/src/components/ui/](frontend/src/components/ui/)                                        | shadcn primitives: button, card, dialog, dropdown-menu, input, label, select, separator, sonner, table, textarea                                                                                                                                                                                                                                                                                    |
| [frontend/src/routeTree.gen.ts](frontend/src/routeTree.gen.ts)                                    | Auto-generated by `@tanstack/router-plugin` — don't hand-edit                                                                                                                                                                                                                                                                                                                                       |

## LLM Connector

Django app at `backend/llm_connector/`. Multi-provider gateway with per-user config and encrypted API keys.

| File                                                                         | Purpose                                                                                                                                              |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| [backend/llm_connector/models.py](backend/llm_connector/models.py)           | `LLMConfig(user, alias, provider, model, ...)` with Fernet-encrypted `api_key`; `LLMRequestLog` (nullable `user` FK) for spend audit                 |
| [backend/llm_connector/conf.py](backend/llm_connector/conf.py)               | `get_alias_config(alias, user=None)` — with user: per-user `LLMConfig` → fall back to `settings.LLM["default"]`; without user: `settings.LLM[alias]` |
| [backend/llm_connector/crypto.py](backend/llm_connector/crypto.py)           | Fernet helpers reading `settings.LLM_ENCRYPTION_KEY`                                                                                                 |
| [backend/llm_connector/client.py](backend/llm_connector/client.py)           | `LLMClient(alias, user=None)`; `complete()` / `stream()` accept `user=`; persists `LLMRequestLog` in `finally`                                       |
| [backend/llm_connector/serializers.py](backend/llm_connector/serializers.py) | `LLMConfigSerializer` (write-only `api_key`, `has_api_key` computed field); `LLMRequestLogSerializer` (all fields read-only)                         |
| [backend/llm_connector/views.py](backend/llm_connector/views.py)             | `LLMConfigViewSet` (full CRUD, user-scoped); `LLMRequestLogViewSet` (read-only spend audit)                                                          |
| [backend/llm_connector/urls.py](backend/llm_connector/urls.py)               | `DefaultRouter` — `/api/llm/configs/` + `/api/llm/request-logs/`                                                                                     |
| [backend/llm_connector/admin.py](backend/llm_connector/admin.py)             | `LLMConfigAdmin` with `PasswordInput` for `api_key`; never exposed in list view                                                                      |
| [backend/llm_connector/providers/](backend/llm_connector/providers/)         | One file per provider: anthropic, openai, google, custom (Ollama / OpenAI-compatible HTTP)                                                           |

`settings.LLM` only contains the global `default` (free Ollama). Paid providers live exclusively in per-user `LLMConfig` rows — encrypted, managed via Django admin and the `/api/llm/configs/` DRF endpoint.

### CV pipeline

```python
cv = CV(user_pk=user.pk, domains=[...], min_skill_proficiency="advanced")

cv.deterministic_filter(job_text)                     # stopword tokens, strict→loose retry
cv.ai_filter_entries(job_text, threshold=0.25)        # LLM scores + min-per-section floor
cv.ai_rank_entries(job_text)                          # LLM scores + sorts
analysis = cv.agentic_tailor(job_text)                # analyze + score_with_analysis + filter + sort
result = cv.ai_tailor_with_fallback(job_text)         # tiered: conversational → filter → keyword → det. → unfiltered
```

Scored entries get `relevance_score` / `relevance_reason` attached as in-memory attrs. `ai_tailor_with_fallback` returns `{"tier", "selection", "keywords"}` so callers know which rung succeeded.

## Working Style

This is how the project gets built — every phase from 2a onward followed this and the rest should too. The reference artifacts are [.claude/plans/phase-2a-setup-guide.md](.claude/plans/phase-2a-setup-guide.md), [.claude/plans/phase-2b-setup-guide.md](.claude/plans/phase-2b-setup-guide.md), and [.claude/plans/phase-2c-setup-guide.md](.claude/plans/phase-2c-setup-guide.md). When starting new work, write the next one in the same shape before touching code.

**Slice phases small.** A roadmap phase is split into letter-suffixed sub-phases (`2a`, `2b`, `2c`, `2d`) each small enough to finish, verify, and commit on its own. One sub-phase is one setup guide is one commit. Never let a sub-phase sprawl across two — log the overflow as a gap and pull it into the next one.

**Every sub-phase is a written setup guide** at `.claude/plans/phase-<id>-setup-guide.md`, authored _before_ the code and kept live as the code lands. It is a hands-on, no-shortcuts tutorial like walkthrough someone could follow start to finish. Fixed skeleton:

1. **Goal** — one paragraph: what you can _do_ at the end, stated as user-visible capability. Name what this sub-phase explicitly does _not_ touch.
2. **Preflight** — concrete checks that the previous sub-phase is committed + green (name the commit hash), the build is clean (`npx tsc -b` → zero output), the suite passes (`python manage.py test` → "Ran N tests … OK"), and the surface you're coding against actually answers (a `curl` with the expected status). If a check fails, stop and fix before proceeding.
3. **The contract you're coding against** — pin the API/data shapes, status codes, and serializer quirks _first_, before any UI. Point at the live spec mirror (`/api/docs/`) rather than restating it.
4. **Stack additions** — exact install commands, with a one-line _why each_ so no dependency is cargo-culted.
5. **Shared infra before pages** — write the small reusable helpers/factories/layout chrome first, then build features on top.
6. **One worked example, end to end** — build the first instance (e.g. `/cv/jobs`) completely, then explicitly "the next five are variations on this skeleton." Order the remaining items along a rising difficulty curve.
7. **Per-step Verify blocks** — every step ends with a concrete observable check (what to click, what request lands in the Network tab, what the toast says). "Stop and fix before moving on" is the rule, not a suggestion.
8. **End-to-end verification — the full loop** — a numbered click-through of the whole sub-phase, including persistence (reload / re-login) and multi-user isolation where relevant.
9. **What you should have at the end** — the resulting file tree, plus the commit checkpoint with its message.
10. **Known gaps to revisit** — explicitly _don't_ fix scope creep in-phase; log each deferral (with the "why it's fine for now") for the named later phase.
11. **What's next** — one paragraph pointing at the following sub-phase.

**Annotate the non-obvious, skip the obvious.** Wherever a choice could read as arbitrary, a short "two non-obvious choices:" note explains the why (e.g. `shouldFilter={false}` because the server already filtered). Don't narrate boilerplate.

**Commit per sub-phase**, code + its setup guide together, message `Phase <id>: <short summary>`. Re-run the suite right before committing. Keep the Roadmap "Shipped" list and this CLAUDE.md current as each sub-phase lands.

**Defer, don't sprawl.** When something tempting but out-of-scope surfaces mid-build, it goes in the "Known gaps" list with a target phase — never bolted onto the current one.

## Roadmap

Full plan in [.claude/plans/roadmap-2026-06-12.md](.claude/plans/roadmap-2026-06-12.md) (output-first revision). Superseded roadmaps + obsolete plan sketches are archived under [.claude/plans/archive/](.claude/plans/archive/).

**Shipped through 2026-06-12:** auth + MFA backend (allauth headless); DRF foundation + full jac CRUD at `/api/jac/` (pagination, filters, OpenAPI, bulk endpoints); llm_connector at `/api/llm/`; spa `UserProfile`; the 5-rung `ai_tailor_with_fallback` CV pipeline; the full career-data model (`related_skills` / `builds_on` / `years_of_experience_override` / `ResumeSnippet` + cross-entry relations); JSON `cv_export`/`cv_import` round-trip; and the frontend through Phase 2c + 3c/3d/3d-bis (auth + account pages + the full `/cv/*` CRUD UI). Per-phase history lives in the roadmap + the `phase-*-setup-guide.md` files.

**Next, in order** (full detail + verification in the roadmap):

- **Phase 4 — Backend: CV validation + cover-letter generation (German output).** (4a) Add a real paid `LLMConfig` to user 1 + a `cv_eval` command to dogfood `ai_tailor_with_fallback` over a real postings corpus → go/no-go. (4b) Refine [cv.py](backend/jac/cv.py) per findings (consume the `builds_on`/`related_skills` edges, multi-pass). (4c) Cover-letter generator — a `CoverLetter` class in new `backend/jac/letter.py` stitching `ResumeSnippet`s onto the tailored CV with the same AI-escalation tiers, rendered via `LetterRender`. (4d) German output localization — Django i18n labels + glossary-protected LLM `translate_entries` cached in a `CvEntry.translations` JSONField; letter generated in the target language. First setup guide: [.claude/plans/phase-4a-setup-guide.md](.claude/plans/phase-4a-setup-guide.md).
- **Phase 5 — Frontend: tailoring UI + document editor + PDF export.** (5a) `/settings/llm` LLM-config CRUD + usage. (5b) Paste-a-posting tailoring editor (edit CV selection + generated letter). (5c) Template-railguarded layout editor + PDF export of the CV+letter package.
- **Phase 6 — Revision.** Harden the whole paste-posting → German CV + letter → PDF loop until send-ready without backend hand-holding; expand tests incl. a first frontend smoke.
- **Phase 7 — DevOps + first deployment.** Security hardening → Dockerfiles + docker-compose → GitHub Actions CI/CD deploying to the IONOS server.

**Outview (later):** portfolio / personalized-link system (`PortfolioLink` + `VisitorResponse` + public views); application-tracking backend (`JobPosting`/`Application`/`CoverLetter` model/`FollowUp` + the `new-application`/`followups` skills); CV import/onboarding (PDF/DOCX → parsed entries).

## Portfolio / personalized link system (outview — after first deployment)

To live in `backend/spa/`. Two surfaces:

1. **Public landing** (`/`) — choose-your-path conversation cards; anonymised answers recorded (`VisitorResponse`).
2. **Personalized views** — curated subset per recipient:
   - `/for/<slug>` — stable, human-readable (business cards, LinkedIn)
   - `/t/<token>` — UUID token, private and revocable (email, DMs)

`PortfolioLink` will store both identifiers; `sections_json` controls which content sections appear and optional message/filter overrides. JAC will create these automatically when an `Application` is sent.

### Planned React routing

```
/           → PublicLanding     (choose-your-path)
/for/:slug  → PersonalizedView  (human-readable link)
/t/:token   → PersonalizedView  (private token link)
```

## Conventions

- **`backend/llm_connector/`** is the LLM gateway — `settings.LLM["default"]` is the free fallback; paid configs live in per-user `LLMConfig` rows. Call via `from llm_connector import complete, stream, get_client` and **always pass `user=`** for user-driven flows so personal configs apply.
- **`backend/jac/llm.py`** holds JAC-specific prompt wrappers — imports from `llm_connector`, never from `anthropic` / `openai` SDKs directly. Every wrapper accepts `user=` and forwards it.
- All career models inherit from `CvEntry` (abstract): every entry is scoped to a `User` and carries `description`, `created_at`, `updated_at`, `updated_by`.
- LLM prompt wrappers use a **line-oriented plain-text protocol, never JSON** (small models mangle JSON envelopes; a line protocol can't). Each wrapper parses its completion with a stdlib `_parse_*` helper in [backend/jac/llm.py](backend/jac/llm.py) (`_parse_keyword_lines`, `_parse_scored_lines` = `id | score | reason`, `_parse_selection_lines` = `id | reason`, `_parse_analysis_block` = labeled block) — each id-first and tolerant (a malformed row is skipped, never fatal). The only contract is that the model echoes each entry's `type:pk` id back; everything else is re-hydrated from the DB. No `json-repair`-style dependency.
- All settings secrets use `os.getenv()` — never hardcoded values. Variables: `SECRET_KEY`, `LLM_ENCRYPTION_KEY` (Fernet key for `LLMConfig.api_key` encryption), `OLLAMA_URL`, `ALLOWED_HOST`, `FRONTEND_URL`, `POSTGRES_*`, `REDIS_URL`, `DEFAULT_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`. Paid-provider API keys are stored per-user in `LLMConfig`, not in env.
- Use Django ORM exclusively — no raw SQL.
- Register every model in the app's `admin.py` so Django Admin is always usable.
- API endpoints: JAC at `/api/jac/`, LLM configs at `/api/llm/`, user profile at `/api/spa/profile/`, account delete at `/api/spa/account/`. Public portfolio + personalized-link endpoints under `/api/spa/` are still planned (outview).
- Secrets in `.env` at repo root — never commit them.
- Auth: `_allauth/browser/v1/...` is the headless endpoint surface; the SPA drives it via fetch + cookies + CSRF (same-origin via Vite proxy in dev, same nginx host in prod).
