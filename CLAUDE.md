# project

a personalised portfolio site + a private CV automation tool (**jac**), in one monorepo.

- **portfolio**: no static page. content is rendered per visitor based on how they arrived.
  e.g. a visitor from a business-card QR sees something different than one arriving from a CV on a
  job post; organic visitors introduce themselves via a questionnaire and get content matched to
  their answers.
- **jac** (Job Application Creator): a private pipeline that digests a job posting and renders a
  CV + cover letter tailored to it with the help of LLMs.

# layout

| path                     | what                                                                                   |
| ------------------------ | -------------------------------------------------------------------------------------- |
| `backend/`               | Django project, mostly headless via DRF                                                |
| `backend/lukehirsch/`    | Django config package — `settings.py`, urls, asgi/wsgi, shared middleware/permissions  |
| `backend/jac/`           | the CV tool — career-DB models, CRUD API, and the CV tailoring pipeline (`cv.py`)      |
| `backend/llm_connector/` | reusable multi-provider LLM connector with per-user encrypted configs                  |
| `backend/spa/`           | portfolio / per-visitor profile app                                                    |
| `frontend/`              | Vite + React + TypeScript SPA                                                           |
| `config/`                | nginx config                                                                           |

don't maintain a deep file tree here — it drifts. open the dir.

# stack

- **backend**: Django + Django REST Framework, headless. JAC CRUD under `/api/jac/`.
- **auth**: `django-allauth` in headless mode — MFA (TOTP + WebAuthn passkeys + recovery codes),
  mandatory email verification, admin MFA gate via middleware.
- **frontend**: React 19 + Vite + TypeScript — TanStack Router/Query/Form/Table, Tailwind v4,
  shadcn/ui.
- **db**: SQLite (dev) → PostgreSQL (prod), env-configurable.
- **llm**: `llm_connector` app — providers for Anthropic, OpenAI, Google, Ollama, and custom;
  per-user configs with Fernet-encrypted keys. zero-cost server default is the `ollama` provider.
  - default chat/writer model: **`llama3.2:1b`** · default embedding model: **`qwen3-embedding:0.6b`**
- **python env**: pyenv (`jac` virtualenv).

# current state

shipped:
- auth / MFA flow (backend + frontend).
- jac: career-DB models (`Domain`, `Location`, `Skill`, `Job`, `Project`, `Education`,
  `Certification`, `Language`, `ResumeSnippet`), full CRUD API + serializers, frontend CRUD UI.
- `llm_connector`: multi-provider connector, per-user configs, native Ollama provider with `embed()`.

- jac CV pipeline (`backend/jac/cv.py`): `CV` loads/flattens career entries (each carries a `refs`
  edge list); `CVFilter` is a **scoring-agnostic** selection layer — directional tier propagation
  over the edges + per-section *absolute floor + min-keep* drop. The `light` rung (`Embed`,
  `llm_prompts.py`) is the working embedding-rank floor. `cv_test` / `cv_eval` management commands
  exercise the grade ladder offline (fake-score injection in tests); `cv_eval` reports per-entry
  ranks + colour-graded one-page target counts per section.
- jac: **favourite** flag on every `CvEntry` — pins an entry for a small post-propagation ranking
  nudge (`CVFilter._FAVOURITE_BONUS`, kept below the lowest section floor so it can't resurrect a
  ~0-scored entry), capped per type (`CvEntry.FAVOURITE_LIMIT`, enforced in `model.clean` + a
  serializer mixin). Wired through the API + CRUD UI (editor toggle + sortable star column).

active skeleton (the thing under construction):
- `standard` (`Instruct`) and `strong` (`Conversational`) rungs in `llm_prompts.py` are stubs;
  `CVFilter._standard_scores` / `_strong_scores` return `{}` and fall back to `light`.

# roadmap

> this is the **moving part** of this file. it changes as goalposts move; keep it honest.
> granular, code-bearing plans for each item live in `.claude/plans/to-do/` (see "how we work").
> `/update-claude` edits this section after a coding phase.

1. **CV ladder — remaining rungs** (`backend/jac/cv.py`, `llm_prompts.py`). The relationship-aware
   selection layer + `light` floor are *done*; what's left is the two LLM scorers (both just feed
   `{id: score}` into the shared `CVFilter` selection):
   - `light`: server embedding model (`qwen3-embedding:0.6b`) ranks entries. *(done)*
   - `standard`: `Instruct` LLM ranks entries — clean instruction approach, fits smaller models.
   - `strong`: `Conversational` LLM ranks entries — conversational approach, for bigger models.
2. **cover-letter generation** — a class that writes a cover letter for a job, using
   `ResumeSnippet` boilerplate stitched by the LLM (writer model: `llama3.2:1b`) to avoid AI slop.
3. **frontend render** of the tailored CV + cover letter.
4. **portfolio generator** — per-visitor portfolio rendering, frontend + backend.

# how we work

hands-on coding for a human who stays on top of their own codebase. unless stated otherwise, a
conversation runs through these phases:

1. human prompts what they want done.
2. AI plans the steps, gives feedback, sharpens the idea.
3. human clarifies.
4. AI writes a detailed, **code-bearing setup guide** (see `/setup-guide`) so an intermediate
   engineer could implement it.
5. AI writes the tests.
6. **human types the code** (essential — this is how the human stays on top of the codebase).
7. AI corrects and improves.
8. **human tests and debugs.**

## who does what (default-strict)

- **AI**: diagnoses (probes / experiments to decide *what* to build), and writes code-bearing
  setup guides + tests. AI also maintains the Claude-meta docs (this file, `.claude/skills/*`,
  memory, plan files).
- **human (Lukas)**: types the application/repo source code, runs it, and does **all** testing
  and verification.
- AI does **not** edit application source or run the test suite to "prove" a change. the human
  implements and reports results back.

**override**: the human can explicitly open a *volatile / exploration phase* ("just code it",
"spike this") — then the AI may write source directly. this is opt-in per task, not the default;
testing still stays with the human.

> note: "human types the code" governs **application/repo source**. Claude-meta files (CLAUDE.md,
> skills, memory, plans) are written by the AI directly.

## the file system that keeps this adaptable

- **`CLAUDE.md`** (this file) = stable source of truth: project, layout, stack, current state,
  roadmap, working style. keep it lean and accurate; no claim should contradict the code.
- **`.claude/plans/to-do/`** ↔ **`.claude/plans/done/`** = roadmap execution. one code-bearing
  guide per item, named `[area]-<slug>.md` (`[backend]` / `[frontend]` / …). completed guides move
  to `done/`.
- **memory** (`/Users/lukas/.claude/projects/-Users-lukas-Projects-jac/memory/`) = durable
  cross-session facts the repo doesn't record. distilled, not a session log.

## skills (slash commands)

- **`/setup-guide`** — write a code-bearing implementation guide for a roadmap item into
  `.claude/plans/to-do/`. does not touch repo source.
- **`/update-claude`** — after a coding phase, refresh this file's roadmap + current-state, distill
  memory, and move finished plans `to-do/` → `done/`.
- **`/handover`** — dump current working context to a durable file so a later session, or a person,
  can pick up cold.
- **`/commit-message`** — write a short commit message for the current changes and commit them.
