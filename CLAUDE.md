# lukehirsch

Portfolio website + Job Application Creator (JAC). Django backend, React frontend, monorepo structure.

## Project Layout

| Path                     | Purpose                                                                                                                                                                    |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`               | Django project root                                                                                                                                                        |
| `backend/lukehirsch/`    | Django config package (settings, urls, wsgi, asgi) + shared DRF utilities (`permissions.py`, `mixin.py`, `AdminRequireMfaMiddleware`, `HarassmentResistantAccountAdapter`, `ReadableConsoleEmailBackend`) |
| `backend/jac/`           | JAC Django app — career DB + CV filtering/tailoring pipeline + full DRF CRUD at `/api/jac/`. `JobPosting`/`Application`/`CoverLetter`/`FollowUp` models pending (Phase 6). |
| `backend/spa/`           | Portfolio + per-user profile app. `UserProfile` + `/api/spa/profile/` shipped; `PortfolioLink` / `VisitorResponse` still planned.                                          |
| `backend/llm_connector/` | Reusable multi-provider LLM connector with per-user encrypted configs                                                                                                      |
| `backend/manage.py`      | Django management entrypoint                                                                                                                                               |
| `frontend/`              | React 19 + Vite + TypeScript — still the default Vite starter (no router, no auth UI yet)                                                                                  |
| `config/`                | nginx config                                                                                                                                                               |
| `requirements.txt`       | Python dependencies                                                                                                                                                        |

## Tech Stack

| Layer             | Technology                                                                                                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend framework | Django 6.x                                                                                                                                                                  |
| API layer         | Django REST Framework — jac CRUD + llm_connector + spa profile wired at `/api/jac/` + `/api/llm/` + `/api/spa/profile/`. Pagination + django-filter + drf-spectacular (OpenAPI at `/api/schema/`, Swagger at `/api/docs/`). Tailor action pending. |
| Auth              | `django-allauth[mfa,usersessions]` in **headless mode** (TOTP + WebAuthn passkeys + recovery codes + multi-session management at `/_allauth/browser/v1/auth/sessions`). Mandatory email verification. Admin MFA gate enforced by `lukehirsch.middleware.AdminRequireMfaMiddleware`. |
| ASGI / streaming  | Daphne + Channels (Redis layer) — wired in settings, no consumers / routing yet                                                                                             |
| Frontend          | React 19 + Vite + TypeScript. Stack locked 2026-06-02: TanStack Router + Query + Form + Table, Tailwind v4, shadcn/ui. Phase 2a (foundation + auth guard) + 2b (full auth + account pages) + 2c (JAC CRUD UI) shipped per [.claude/plans/phase-2c-setup-guide.md](.claude/plans/phase-2c-setup-guide.md). |
| Database          | SQLite (dev) → PostgreSQL (prod, env-configurable) — `settings.DATABASES` branches on `DEBUG`                                                                               |
| Task queue        | Celery + Redis — full settings block in place; no `celery.py` / tasks yet                                                                                                   |
| Cache             | Redis (`django.core.cache.backends.redis.RedisCache`)                                                                                                                       |
| LLM               | `llm_connector` app — Anthropic, OpenAI, Google, custom (Ollama). Per-user configs with Fernet-encrypted API keys.                                                          |
| Email             | Console backend in dev (DEBUG=True); SMTP via env in prod (Strato by default — `no-reply@luke-hirsch.de`)                                                                   |
| Python env        | pyenv (`jac` virtualenv)                                                                                                                                                    |
| Deployment        | Docker Compose + GitHub Actions (planned — see roadmap Phase 5)                                                                                                             |

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
python manage.py cv_import --user 1 ...          # bulk-import career entries
```

### Frontend (run from `frontend/`)

```bash
npm run dev
npm run build
npm run lint
```

## JAC App

Django app at `backend/jac/`. Today: career DB + CV filtering/tailoring pipeline + Markdown rendering + full DRF CRUD at `/api/jac/`. `JobPosting`/`Application`/`CoverLetter`/`FollowUp` models, cover-letter generation, PDF/DOCX export, and the CV import wizard are not yet built (Phases 6 + 10). Phase 3 evolves the career model first: a manual `Skill.years_of_experience_override` (the computed property over-counts intermittent skills), a symmetric `Skill.related_skills` M2M, and a `ResumeSnippet` model (hand-written prose the generator stitches together) — see roadmap Phase 3a. Phase 3f adds **output localization**: career data is authored in English but applications target corporate Germany, so CV *rendering* gets a target-language variant (filtering already matches cross-language). Deterministic labels (section headings + `gettext_lazy` enums) via Django i18n + a `de` `.po`; free-text prose (`description`, job `title`, `ResumeSnippet`) via an LLM `translate_entries` wrapper, glossary-protected (tech terms + proper nouns pass through verbatim), stored in a `CvEntry.translations` JSONField (lazy-cached + editable, not regenerated per run), target language detected from the posting with an override — see roadmap Phase 3f.

Key files:
| File | Purpose |
|------|---------|
| [backend/jac/models.py](backend/jac/models.py) | Career DB: `Domain`, `Location`, `CvEntry` (abstract base), `Education`, `Certification`, `Skill`, `Job`, `Project`, `Language` — every entry user-scoped |
| [backend/jac/cv.py](backend/jac/cv.py) | `CV` class — loads entries per user, deterministic + LLM filtering/ranking, **tiered fallback pipeline** (`ai_tailor_with_fallback`) covering conversational → filter → keyword → deterministic → unfiltered |
| [backend/jac/stopwords.py](backend/jac/stopwords.py) | Multi-language stopword sets (strict + loose) used by `extract_keywords` |
| [backend/jac/llm.py](backend/jac/llm.py) | Prompt wrappers (`extract_job_keywords`, `analyze_job`, `score_entries_for_job`, `score_entries_with_analysis`, `tailor_cv_conversationally`) — imports from `llm_connector` only |
| [backend/jac/render.py](backend/jac/render.py) | `CvRender.export_md()`. PDF/DOCX still TBD (handwaved as "frontend" but no plan yet) |
| [backend/jac/serializers.py](backend/jac/serializers.py) | DRF serializers for all 8 career models (incl. `Skill.years_of_experience` computed field — Phase 3 adds a writable `years_of_experience_override` since the computed value over-counts intermittent skills); `ScopeDomainsToUserMixin` extending the project-level base |
| [backend/jac/admin.py](backend/jac/admin.py) | Admin registrations for all career models |
| [backend/jac/tests.py](backend/jac/tests.py) | Tests covering models, CV pipeline (mocked LLMs), llm wrappers, and rendering |
| [backend/jac/management/commands/cv_test.py](backend/jac/management/commands/cv_test.py) | Smoke-test CLI: unfiltered → deterministic → ai_filter → agentic_tailor |
| [backend/jac/management/commands/cv_import.py](backend/jac/management/commands/cv_import.py) | Bulk-import career entries |

## SPA App (in progress)

Django app at `backend/spa/`. Holds the per-user profile + auth-related signals today; will gain the portfolio link system in roadmap Phase 3.

| File                                                   | Purpose                                                                                                                                                                                                                                                     |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [backend/spa/models.py](backend/spa/models.py)         | `UserProfile` (one-to-one with `auth.User`, auto-created via signal): identity (display_name, avatar, bio), professional contact (phone, website, linkedin_url, github_url), locale (timezone), UI prefs (theme, contrast), notifications (email_reminders) |
| [backend/spa/serializers.py](backend/spa/serializers.py) | `UserProfileSerializer` — exposes every profile field; `user` is hidden + defaulted to the request user                                                                                                                                                  |
| [backend/spa/views.py](backend/spa/views.py)           | `UserProfileView` (`RetrieveUpdateAPIView`) — GET/PUT/PATCH `request.user.profile`. `AccountDeleteView` — `DELETE /api/spa/account/`; gates on allauth's `did_recently_authenticate`, returning the 401 + `flows:[{id:"reauthenticate"}]` shape that the frontend's `withReauth` picks up. allauth headless has no account-delete endpoint, so this is our own. |
| [backend/spa/urls.py](backend/spa/urls.py)             | `/api/spa/profile/` → `UserProfileView`; `/api/spa/account/` → `AccountDeleteView`                                                                                                                                                                          |
| [backend/spa/signals.py](backend/spa/signals.py)       | `on_mfa_authenticator_used` — sets `session["mfa_authenticated"]=True` after any successful allauth authenticator use, so `AdminRequireMfaMiddleware` lets the staff user through to `/admin/`                                                              |
| [backend/spa/apps.py](backend/spa/apps.py)             | `SpaConfig.ready()` wires `allauth.mfa.signals.authenticator_used` → `on_mfa_authenticator_used`                                                                                                                                                            |
| [backend/spa/admin.py](backend/spa/admin.py)           | `UserProfile` admin                                                                                                                                                                                                                                         |
| [backend/spa/tests.py](backend/spa/tests.py)           | Full allauth headless auth-flow tests (signup → verify → login, password reset/change, TOTP enroll w/ reauth, MFA login challenge, recovery codes, rate limit) + `AdminRequireMfaMiddleware` gate tests + `UserProfileView` scoping tests                  |

`PortfolioLink`, `VisitorResponse`, and public/personalized views are **not** built — see roadmap Phase 3.

## Frontend

React 19 + Vite + TS at `frontend/`. Phase 2a (foundation) + 2b (auth + account pages) + 2c (JAC CRUD UI) shipped per [.claude/plans/phase-2c-setup-guide.md](.claude/plans/phase-2c-setup-guide.md). Backend talks via same-origin Vite proxy (`/api`, `/_allauth`, `/admin`, `/media`, `/static` → `localhost:8000`); router auto-generates `src/routeTree.gen.ts` on file changes.

| Path                                                                 | Purpose                                                                                                                                                                       |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [frontend/src/routes/__root.tsx](frontend/src/routes/__root.tsx)     | Root route — `<Outlet />` + `<Toaster />` (sonner)                                                                                                                            |
| [frontend/src/routes/_authenticated.tsx](frontend/src/routes/_authenticated.tsx) | Auth gate + `AuthedLayout` (header + sign-out). `beforeLoad` calls `fetchSession`; routes anonymous users to `/auth/login`, pending verify-email to `/auth/verify-email`, pending MFA to `/auth/mfa-challenge` |
| [frontend/src/routes/_authenticated/account.tsx](frontend/src/routes/_authenticated/account.tsx) | `AccountLayout` — left nav (Profile / Email / Security / Danger) wrapping `<Outlet />` in a `Card`                                                                            |
| [frontend/src/routes/_authenticated/account/](frontend/src/routes/_authenticated/account/) | `profile.tsx` (PATCH `/api/spa/profile/`), `email.tsx` (allauth `/account/email` + inline verify-code form per unverified row → POST `/auth/email/verify`), `security.tsx` (composes the security panels), `danger.tsx` (sessions list via `/auth/sessions` + sign out + `DELETE /api/spa/account/`) |
| [frontend/src/routes/auth.tsx](frontend/src/routes/auth.tsx)         | Public `auth` layout — centered card with "back to home" link. `beforeLoad` calls `fetchSession` to bootstrap the `csrftoken` cookie before any child route POSTs (allauth's `browser_view` decorator sets it via `get_token`). |
| [frontend/src/routes/auth/](frontend/src/routes/auth/)               | `login.tsx`, `signup.tsx`, `verify-email.tsx`, `request-reset.tsx`, `reset-password.$key.tsx`, `mfa-challenge.tsx` (branches by `useAuth().status`: anonymous w/ pending stage → `/auth/2fa/authenticate`; already authenticated, e.g. admin gate step-up → `/auth/2fa/reauthenticate`) |
| [frontend/src/lib/api.ts](frontend/src/lib/api.ts)                   | `api<T>()` fetch wrapper — same-origin cookies, auto-CSRF for unsafe methods, throws typed `ApiError`; `allauthErrorsByField` flattens allauth error arrays                  |
| [frontend/src/lib/auth.ts](frontend/src/lib/auth.ts)                 | `fetchSession` (treats 401/410 as session payloads, not errors), `signOut` (swallows the 401-status post-signout payload), `useAuth` hook, `useInvalidateSession`, `SessionResponse` / `AuthStatus` types |
| [frontend/src/lib/auth-flow.ts](frontend/src/lib/auth-flow.ts)       | `resolveAuthOutcome` — decodes allauth success body OR thrown `ApiError` into a single discriminated `AuthOutcome` the call site can switch on                               |
| [frontend/src/lib/reauth.ts](frontend/src/lib/reauth.ts)             | `withReauth(fn)` — runs `fn`; on any 401 whose body lists `reauthenticate` as an *available* flow (no `is_pending` gate, since allauth returns `{id:"reauthenticate"}` bare for step-up 401s), prompts for password, posts to `/auth/reauthenticate`, then retries. Wrap any destructive mutation. |
| [frontend/src/lib/webauthn.ts](frontend/src/lib/webauthn.ts)         | base64url codec + `decode/encodeCreationOptions`/`RequestOptions` + `encodeAttestation`/`Assertion`. Exports `EncodedCreationOptions` / `EncodedRequestOptions` types        |
| [frontend/src/lib/form.ts](frontend/src/lib/form.ts)                 | `zodValidator(schema)` — adapts a Zod schema for TanStack Form's `validators.onChange`                                                                                       |
| [frontend/src/lib/query.ts](frontend/src/lib/query.ts)               | The single `QueryClient`                                                                                                                                                      |
| [frontend/src/components/security/](frontend/src/components/security/) | `change-password.tsx`, `totp-panel.tsx` (QR enroll + recovery codes; treats `GET /authenticators/totp` 404 as "not enrolled yet, here's `meta.secret`"), `passkey-panel.tsx` (lists via `/account/authenticators` filtered by `type==="webauthn"` — the webauthn-specific GET is *register*, not *list*, and the response wraps creation options in `data.creation_options`) — all use `withReauth` |
| [frontend/src/components/passkey-button.tsx](frontend/src/components/passkey-button.tsx) | "Sign in with passkey" button on the login screen                                                                                                                            |
| [frontend/src/components/ui/](frontend/src/components/ui/)           | shadcn primitives: button, card, dialog, dropdown-menu, input, label, select, separator, sonner, table, textarea                                                              |
| [frontend/src/routeTree.gen.ts](frontend/src/routeTree.gen.ts)       | Auto-generated by `@tanstack/router-plugin` — don't hand-edit                                                                                                                  |

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

**Every sub-phase is a written setup guide** at `.claude/plans/phase-<id>-setup-guide.md`, authored *before* the code and kept live as the code lands. It is a hands-on, no-shortcuts walkthrough someone could follow start to finish. Fixed skeleton:

1. **Goal** — one paragraph: what you can *do* at the end, stated as user-visible capability. Name what this sub-phase explicitly does *not* touch.
2. **Preflight** — concrete checks that the previous sub-phase is committed + green (name the commit hash), the build is clean (`npx tsc -b` → zero output), the suite passes (`python manage.py test` → "Ran N tests … OK"), and the surface you're coding against actually answers (a `curl` with the expected status). If a check fails, stop and fix before proceeding.
3. **The contract you're coding against** — pin the API/data shapes, status codes, and serializer quirks *first*, before any UI. Point at the live spec mirror (`/api/docs/`) rather than restating it.
4. **Stack additions** — exact install commands, with a one-line *why each* so no dependency is cargo-culted.
5. **Shared infra before pages** — write the small reusable helpers/factories/layout chrome first, then build features on top.
6. **One worked example, end to end** — build the first instance (e.g. `/cv/jobs`) completely, then explicitly "the next five are variations on this skeleton." Order the remaining items along a rising difficulty curve.
7. **Per-step Verify blocks** — every step ends with a concrete observable check (what to click, what request lands in the Network tab, what the toast says). "Stop and fix before moving on" is the rule, not a suggestion.
8. **End-to-end verification — the full loop** — a numbered click-through of the whole sub-phase, including persistence (reload / re-login) and multi-user isolation where relevant.
9. **What you should have at the end** — the resulting file tree, plus the commit checkpoint with its message.
10. **Known gaps to revisit** — explicitly *don't* fix scope creep in-phase; log each deferral (with the "why it's fine for now") for the named later phase.
11. **What's next** — one paragraph pointing at the following sub-phase.

**Annotate the non-obvious, skip the obvious.** Wherever a choice could read as arbitrary, a short "two non-obvious choices:" note explains the why (e.g. `shouldFilter={false}` because the server already filtered). Don't narrate boilerplate.

**Commit per sub-phase**, code + its setup guide together, message `Phase <id>: <short summary>`. Re-run the suite right before committing. Keep the Roadmap "Shipped" list and this CLAUDE.md current as each sub-phase lands.

**Defer, don't sprawl.** When something tempting but out-of-scope surfaces mid-build, it goes in the "Known gaps" list with a target phase — never bolted onto the current one.

## Roadmap

Full plan in [.claude/plans/roadmap-2026-06-02.md](.claude/plans/roadmap-2026-06-02.md) (frontend-first revision). Earlier roadmaps ([2026-06-01](.claude/plans/roadmap-2026-06-01.md), [2026-05-29](.claude/plans/roadmap-2026-05-29.md)) are superseded.

**Shipped:**

- **Per-user LLM configs (2026-05-29).** `settings.LLM` shrank to the free Ollama fallback. CV pipeline threads `user=` end-to-end.
- **Backend docstring cleanup (2026-05-29).** Every `backend/` module leads with a "why" docstring; frontend pass deferred.
- **Auth + MFA backend (since 2026-05-29).** `django-allauth[mfa]` headless with email signup/login/verification/password-reset + TOTP + WebAuthn passkeys + recovery codes. `_allauth/` mounted. Frontend auth pages still pending (Phase 2).
- **DRF foundation + jac CRUD (2026-06-02).** `rest_framework`, `corsheaders`, `REST_FRAMEWORK` settings block; full jac serializers, `ModelViewSet`s, and `DefaultRouter` wiring at `/api/jac/`. Domain + Location gained user FKs + scoping migrations. `IsOwner`/`IsOwnerOrReadOnly` in `lukehirsch/permissions.py`.
- **llm_connector DRF wire-up (2026-06-02).** `LLMConfigViewSet` (write-only `api_key`) + read-only `LLMRequestLogViewSet` mounted at `/api/llm/`. `ScopeRelatedToUserMixin` extracted to `lukehirsch/mixin.py` for cross-app reuse.
- **Async infrastructure (since 2026-05-29).** `daphne`, `channels`, Redis `CHANNEL_LAYERS` + `CACHES`, full Celery config — wired in settings, **no `celery.py` / tasks / ASGI routing yet**.
- **`spa` UserProfile (2026-06-02).** Identity + contact + locale + UI prefs + notification opt-ins, auto-created via `post_save` signal. Migration committed.
- **CV pipeline extensions (since 2026-05-29).** 5-rung tiered fallback (`ai_tailor_with_fallback`), multi-language stopwords, `ai_conversational_tailor`, `ai_keyword_filter`, min-per-section floor, `cv_import` command.
- **Phase 1 — BE prep for frontend (2026-06-03).** Slim `UserProfileView` (`RetrieveUpdateAPIView`) at `/api/spa/profile/`. DRF pagination (`PageNumberPagination`, page size 50), `django-filter` + search/order backends on every list view, `drf-spectacular` schema at `/api/schema/` + Swagger at `/api/docs/`. URL paths reorganised under `/api/*`. `AdminRequireMfaMiddleware` redirects staff users with TOTP/WebAuthn enrolled to `FRONTEND_URL/auth/mfa-challenge` until `session["mfa_authenticated"]` is set by `spa.signals.on_mfa_authenticator_used`. Full allauth headless auth-flow tests + admin MFA gate tests + user-scoping tests for jac/llm_connector/spa viewsets — 163 tests green.
- **Phase 2a — frontend foundation (2026-06-06, commit `fa30c6e`).** Vite + Tailwind v4 + shadcn/ui + TanStack Router/Query/Form/Table scaffold. `src/lib/api.ts` (same-origin fetch + CSRF + typed `ApiError`), `src/lib/auth.ts` (`fetchSession`/`useAuth` over `_allauth`), `src/routes/_authenticated.tsx` auth-gate, root layout with sonner toaster. Vite dev proxy for `/api`, `/_allauth`, `/admin`, `/media`, `/static`. Backend additions: `HarassmentResistantAccountAdapter` (dedupes "account already exists" emails per-address with a 24h cache TTL) + `ReadableConsoleEmailBackend` (strips quoted-printable soft-breaks so reset URLs are pasteable). Email config now reads `EMAIL_HOST*` env in prod, console-backend in dev.
- **Phase 2b — auth + account pages (shipped 2026-06-08, commit `60e1754`).** Auth routes: `/auth/{login,signup,verify-email,request-reset,reset-password/$key,mfa-challenge}` driving headless allauth. Account routes under `/_authenticated/account/{profile,email,security,danger}` with shared `AccountLayout`. Security panels in `src/components/security/`: `ChangePassword`, `TotpPanel` (QR enroll + recovery codes), `PasskeyPanel`. Helpers: `auth-flow.ts` (`resolveAuthOutcome` discriminated union), `reauth.ts` (`withReauth` wraps destructive mutations and prompts for password on any `reauthenticate` flow), `webauthn.ts` (base64url codec + creation/assertion encoders with typed `Encoded*Options`), `form.ts` (zod ↔ TanStack Form bridge). End-to-end fixes after first walk-through (2026-06-08): `auth.tsx` `beforeLoad` fetches session so the csrftoken cookie is planted before the first POST; `signOut()` swallows allauth's 401-status post-signout payload; account/email gained an inline verify-code form; `withReauth` dropped the `is_pending` gate (allauth lists `reauthenticate` as an *available* flow, no pending flag); `PasskeyPanel` now lists via `/account/authenticators` and unwraps `data.creation_options`; mfa-challenge branches `2fa/authenticate` vs `2fa/reauthenticate` by auth status; `allauth.usersessions` wired in for the sessions list; `AccountDeleteView` added at `/api/spa/account/` (allauth has no built-in). Live setup guide at [.claude/plans/phase-2b-setup-guide.md](.claude/plans/phase-2b-setup-guide.md).

- **Phase 2c — JAC CRUD UI (shipped 2026-06-09).** `/cv` dashboard + per-section pages (`/cv/{jobs,education,skills,certifications,projects,languages}`) backed by `/api/jac/`. TanStack Table list with debounced search + filters; side `Sheet` editor driven by TanStack Form + Zod; inline `DomainPicker` / `LocationPicker` comboboxes with "create new" affordance; side-by-side Markdown live-preview on `description`; sticky `BulkBar` for multi-row delete + add/remove domains. Query factories (`useList`/`useCreate`/`useUpdate`/`useDestroy` + bulk variants) live in `src/lib/queries/jac.ts`. Post-login lands on `/cv` via a redirecting `/` route. Live setup guide at [.claude/plans/phase-2c-setup-guide.md](.claude/plans/phase-2c-setup-guide.md).

- **Phase 3a — career-data model evolution (2026-06-10, commit `5be0102`).** `Skill.years_of_experience_override` (nullable; the property returns it when set, else the computed delta that over-counts intermittent skills), symmetric self-referential `Skill.related_skills` M2M, and the `ResumeSnippet` model (`title`/`content`/`kind` + `domains`/`skills` M2M + `is_active`) at `/api/jac/resume-snippets/`. `SkillSerializer` exposes writable `years_of_experience_override` + `related_skills` (user-scoped, self-reference rejected); admin + tests updated.
- **Phase 3b — backend API ergonomics (2026-06-10, commit `def0c80`).** `POST /api/jac/<resource>/bulk/` (transactional delete + domain add/remove, all-or-nothing on foreign IDs) via `BulkActionMixin`; `useBulkDestroy`/`useBulkPatchDomains` rewired to it. Explicit `ordering_fields` allow-lists (incl. `updated_at`/`created_at`). Read-only `Domain.is_default` on `DomainSerializer` + `DomainRow`. Live setup guide at [.claude/plans/phase-3b-setup-guide.md](.claude/plans/phase-3b-setup-guide.md).
- **Phase 3c — editor chrome + certification fix (2026-06-10, commit `489e43a`).** Widened + padded the career-entry editor `Sheet` ([components/cv/section-page.tsx](frontend/src/components/cv/section-page.tsx) — the shared `Sheet`'s only consumer); fixed certifications never POSTing (the form required `issuer` but never rendered the field, so validation silently blocked submit) by rendering it + a form-level "some fields are invalid" guard. Live setup guide at [.claude/plans/phase-3c-setup-guide.md](.claude/plans/phase-3c-setup-guide.md).
- **Phase 3d — skill/relation editing + pickers (2026-06-10).** New `SkillPicker` (multi-select M2M, `excludeId`, no inline create) + `CertificationPicker` (single-select FK) in `src/components/cv/`. Wired the `skills` M2M into the Job + Project editors; added `related_skills` (symmetric self-M2M) + `years_of_experience_override` (with an "auto" hint) to the Skill editor, replacing its raw certification id input. `SkillRow` gained `related_skills` + `years_of_experience_override`. Live setup guide at [.claude/plans/phase-3d-setup-guide.md](.claude/plans/phase-3d-setup-guide.md).

**Next, in order** (full detail in the roadmap):

- **Phase 2d — LLM connector UI** — `/settings/llm` (CRUD over `LLMConfig` with write-only `api_key`) + `/settings/llm/usage` (read-only `LLMRequestLog` with date filter + aggregate spend).
- **Phase 3 (remaining) — data portability → JAC-core gate → hardening/SPA/localization.** Re-prioritised 2026-06-10; 3c/3d (editor completeness) shipped. (3e) CV JSON export/import — new `cv_export` command + extend `cv_import` to round-trip `related_skills`/`years_override`/`ResumeSnippet` and scope domains per-user, for migrating data onto a deployed box; (3f) JAC-core validation gate — dogfood `cv_test`/`ai_tailor_with_fallback` over real postings, log findings, Phase 4 go/no-go; then (3g) pre-deployment hardening — throttles, N+1 audit, security headers/secure cookies, validation, multipart/avatar, wider reauth; (3h) first SPA backend (`PortfolioLink` + `VisitorResponse` + public read API); (3i) `celery.py` + trivial task + first Playwright smoke; (3j) output localization — German-first CV *rendering* (filtering already matches cross-language): Django i18n labels + glossary-protected LLM `translate_entries` stored in a lazy-cached, editable `CvEntry.translations` JSONField, target language detected from the posting with an override.
- **Phase 4 — Frontend: first SPA views** — PublicLanding, PersonalizedView (`/for/:slug`, `/t/:token`), portfolio-link admin.
- **Phase 5 — First deployment** — Dockerfiles, docker-compose, GitHub Actions, Sentry, DB backups, GDPR endpoints.
- **Phase 6 — BE: CV generation** — `JobPosting` + `Application` + `CoverLetter` + `FollowUp` models; `/api/jac/cv/tailor/` action; cover-letter generation; PDF/DOCX export.
- **Phase 7 — Frontend: CV styling + export + application wizard** — preview component, theme selector, export menu, application wizard.
- **Phase 8 — BE: streaming + email + follow-ups + spend caps + more SPA** — streaming tailor endpoint, real SMTP, follow-up scheduler, per-user spend caps, JAC→link auto-creation.
- **Phase 9 — Frontend: long tail** — streaming progress UI, application list/board, spend-cap UI, accessibility audit, frontend docstring pass.
- **Phase 10 — CV import / onboarding** — PDF/DOCX → LLM-parsed entries → confirm/edit/save.

## Portfolio / personalized link system (planned — Phase F)

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
- LLM prompt wrappers return parsed JSON (`_parse_json` strips ` ```json ` fences and raises with the raw response on failure).
- All settings secrets use `os.getenv()` — never hardcoded values. Variables: `SECRET_KEY`, `LLM_ENCRYPTION_KEY` (Fernet key for `LLMConfig.api_key` encryption), `OLLAMA_URL`, `ALLOWED_HOST`, `FRONTEND_URL`, `POSTGRES_*`, `REDIS_URL`, `DEFAULT_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`. Paid-provider API keys are stored per-user in `LLMConfig`, not in env.
- Use Django ORM exclusively — no raw SQL.
- Register every model in the app's `admin.py` so Django Admin is always usable.
- API endpoints: JAC at `/api/jac/`, LLM configs at `/api/llm/`, user profile at `/api/spa/profile/`, account delete at `/api/spa/account/`. Public portfolio + personalized-link endpoints under `/api/spa/` are still planned (Phase 3).
- Secrets in `.env` at repo root — never commit them.
- Auth: `_allauth/browser/v1/...` is the headless endpoint surface; the SPA drives it via fetch + cookies + CSRF (same-origin via Vite proxy in dev, same nginx host in prod).
