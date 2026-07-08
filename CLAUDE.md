# project

a personalised **public** portfolio site + a CV automation tool (**jac**), in one monorepo. the
portfolio is the product; jac feeds it.

- **portfolio**: no static page. content is rendered per visitor based on how they arrived —
  e.g. a visitor from a business-card QR sees something different than one arriving from a CV on a
  job post; organic visitors introduce themselves via a questionnaire and get content matched to
  their answers. it renders from the **same career-DB entries** jac maintains, and it is public.
- **jac** (Job Application Creator): a pipeline that digests a job posting and renders a CV +
  cover letter tailored to it with the help of LLMs. auth-gated **today** (signup closed = launch
  toggle), but the plan is to open it as part of the portfolio — a "create your own CV here!"
  showcase. **security posture: treat every authenticated surface as internet-facing**, because
  open signup is a roadmap destination, not an accident; public endpoints opt in via explicit
  `AllowAny` on top of the deny-by-default DRF setting, never public-by-omission.

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

shipped (lean inventory — mechanism + *why* live in the code and the linked memories, not here):
- **auth / MFA** — full flow, backend + frontend.
- **jac career DB** — models (`Domain`, `Location`, `Skill`, `Job`, `Project`, `Education`,
  `Certification`, `Language`, `ResumeSnippet`) + full CRUD API/serializers + frontend UI.
- **`llm_connector`** — multi-provider connector (Anthropic, OpenAI, Google, Ollama, custom),
  per-user Fernet-encrypted configs. Optional capabilities gated by class flags: `embed()` (Ollama)
  and `web_search()`/`supports_web_search` (Anthropic; OpenAI via Responses API; Google via Gemini
  grounding). All LLM I/O is line-format, not JSON. See [[no-json-llm-io]].
- **jac CV pipeline** (`cv.py`, `filter.py`) — `CV` flattens entries (`refs` edges); `CVFilter`
  selects across **three rungs**: `light` (`Embed` → propagation + per-section floor/min-keep),
  `standard` (`Instruct` 0–3 labels → keep-by-verdict), `strong` (`Conversational` → holistic ordered
  set, guardrails only); `output()` degrades strong→standard→light. Alias threads through;
  `embed_floors` overridable; **favourite** flag = small capped ranking nudge. See [[project_jac]],
  [[selection-size-is-intentional]].
- **jac eval tooling** — `cv_test` / `cv_eval` commands (model×grade matrix, `--all-models`,
  interactive pick, colour-graded `findings.md` artifacts; `--analyze` → `TheJudge` + `TheAnalyst`).
- **jac cover-letter** (`cover_letter.py`, `llm_prompts.py`) — `SnippetSelector` picks intro/body/
  closing `ResumeSnippet`s by relevance; `CoverLetterWriter` only *weaves* (posting stripped — the
  fabrication vector); bilingual `de`/`en` furniture; `JobPosting`/`JobPostAddress` +
  `AddressExtract`. Two orthogonal metrics: **`ai_share`** (provenance) and **`FaithfulnessCheck`**
  grounding (`count=None` on failure, never 0). `cover_letter` command smoke-tests a corpus.
  See [[cover-letter-language-strategy]], [[cover-letter-grounding-metric]].
- **spa personality questionnaire** (`PersonalityProfile`) — ~5-of-12 oblique free-text answers
  (≤280 chars) distilled into one cached `dossier` (`ensure_dossier`, rebuilt only on change).
- **jac cover-letter personal paragraph** (`cover_letter.py` `_personal_paragraph`, `research.py`,
  `PersonalParagraphWriter`) — one researched, company-specific paragraph (web research × personality
  dossier) after the body. Opt-in (`--personal`), **capability-driven not grade-gated**: real only
  when grade≠light + alias can web-search + research ok + personality present, else a loud
  `PERSONAL_STUB`. Own `ParagraphGroundingCheck`; words fold into `ai_share`. *(Tests green; live LLM
  verification pending.)* See [[project-purpose-cv-showcase]].
- **jac frontend LLM-config tab** (`frontend/src/lib/queries/llm.ts`,
  `frontend/src/routes/_authenticated/account/llm.tsx`) — account tab doing owner-scoped CRUD over
  `/api/llm/configs/` (the aliases the pipeline resolves through). Picking a commercial provider
  opens a **structured mask** that assembles the `extra` JSON from discrete inputs; `custom`/`ollama`
  get a raw JSON textarea. `api_key` is write-only — the server only ever returns `has_api_key`. The
  pure form↔payload helpers (`toPayload`/`rowToState`/`switchProvider`/…) are unit-tested. See
  [[frontend-test-layout]].
- **jac async generation plumbing** (`lukehirsch/celery.py`, `lukehirsch/asgi.py`, `jac/models.py`
  `GenerationRun`, `jac/tasks.py`, `jac/consumers.py`, `jac/ws_routing.py`, viewset in `jac/views.py`)
  — the async loop carrying a generation to the SPA: REST `POST` persists a `pending` `GenerationRun`
  + `JobPosting` and enqueues a Celery task; the task streams progress to the `gen_<pk>` channel
  group; a Channels WS (`GenerationConsumer`, session-auth + ownership) forwards events and pushes a
  snapshot on connect; a REST `GET` rehydrates on refresh. **Stub task body** for now — guide 2 swaps
  the real pipeline in behind the stable event contract (`snapshot`/`progress`/`done`/`failed`).
  Channel layer + Celery broker run on Redis/Valkey even in `DEBUG`. See [[generation-async-loop]].

# roadmap

> this is the **moving part** of this file. it changes as goalposts move; keep it honest.
> granular, code-bearing plans for each item live in `.claude/plans/to-do/` (see "how we work").
> `/wrap-up` refreshes this section at the end of a coding phase.

1. **wire the pipeline to the frontend** — a 3-guide effort: (1) async generation plumbing
   **[done — see current state]**; (2) **generation pipeline** — swap the stub `generate_run` body
   for the real CV + cover-letter run (`jac.generation_result.serialize_cv_selection`, patched
   `CV`/`CoverLetter`/`AddressExtract`/`get_alias_strength`), producing the real `result` shape;
   (3) **frontend render** of the tailored CV + cover letter — `grounding` next to `ai_share`
   (green ✓ / amber "N claims" badge, claim list on hover) **and the `personal_paragraph`** (real
   vs `is_stub` styled distinctly, sources + own grounding badge). The result dict already carries
   everything. Guides 2 + 3 plans live in `to-do/`; pre-written red tests already on disk
   (`test_generation_task.py`, `frontend/tests/lib/generations.test.ts`).
2. **portfolio generator** — per-visitor portfolio rendering, frontend + backend.
3. **self-hosted web-search agent** (parked) — let a self-hosted *standard* run produce a real
   personal paragraph: wire a tool-capable local model to a **self-hostable** search backend
   (SearXNG / Tavily / Firecrawl-style) via a tool-calling loop, folding in the parked `scraper`
   app. The personal-paragraph guide leaves `ollama`/`custom` at `supports_web_search=False` and
   stubs until this lands (Ollama's hosted `/api/web_search` is cloud + key — quick but doesn't
   prove the self-hosted thesis). See [[project-purpose-cv-showcase]].

> **Async generation plumbing — done (guide 1 of 3).** End-to-end REST→Celery→Channels-WS loop with
> a **stub** task body, proving Redis/Valkey + Celery + Channels + WS session-auth + ownership before
> the slow LLM pipeline goes on top (guide 2). Unknown `grade` is **coerced to `light` + warned**, not
> rejected (a 400 would punish a typo; the run still succeeds). See [[generation-async-loop]].
>
> **Frontend LLM-config tab — done.** Owner-scoped CRUD UI over `/api/llm/configs/` (provider masks
> for the commercial providers, JSON textarea for `custom`/`ollama`; write-only `api_key`). Landed
> with the **first frontend test harness**: vitest, tests in a separate `frontend/tests/` tree that
> mirrors `src/` (not colocated) and is excluded from the `tsc -b` build. See [[frontend-test-layout]].

> **Cover-letter generation — done.** Selection (`SnippetSelector`) + writer (`CoverLetterWriter`,
> posting stripped) + `ai_share` provenance + `FaithfulnessCheck` grounding all landed and merged to
> `main`. The previously-failing cover-letter tests are now green (the breakage was a missing test
> import + a `langauge` typo in `ResumeSnippetSerializer` that 500'd every snippet endpoint — both
> fixed). all suites pass clean (no stray log/stdout noise). **Tests now live in a per-app
> `tests/` package** (`backend/<app>/tests/`), split by concern into `test_*.py` files with shared
> fixtures in a non-collected `_helpers.py` — not the old single `tests.py` per app.
>
> **CV ladder — done.** All three rungs landed: `light` (embeddings →
> propagation + floors), `standard` (`Instruct` `0–3` labels → keep-by-verdict), `strong`
> (`Conversational` holistic ordered selection → guardrails only). All LLM I/O is **line-format,
> not JSON** (id-anchored, truncation-robust — see `no-json-llm-io`). Model/grade selection wired
> end-to-end: `cv_eval --llm/--grade` matrix + alias threading + embedder autodetect +
> per-embedder `embed_floors`.

# how we work

hands-on coding for a human who stays on top of their own codebase. unless stated otherwise, a
conversation runs through these phases:

1. human prompts what they want done.
2. AI plans the steps, gives feedback, sharpens the idea.
3. human clarifies.
4. AI writes a detailed, **code-bearing setup guide** (see `/setup-guide`) so an intermediate
   engineer could implement it.
5. AI writes the tests **to disk** (real, runnable files, not just blocks in the guide) — they land
   **before** coding and start **red**, so they double as the guide's acceptance criteria. the human
   can adapt them but sees when the goal is met (red → green).
6. **human types the code** (essential — this is how the human stays on top of the codebase).
7. AI corrects and improves.
8. **human tests and debugs.**

## who does what (default-strict)

- **AI**: diagnoses (probes / experiments to decide *what* to build), and writes code-bearing
  setup guides + tests. AI also maintains the Claude-meta docs (this file, `.claude/skills/*`,
  memory, plan files).
- **human (Lukas)**: types the application/repo **non-test** source code, runs it, and does **all**
  testing and verification.
- AI does **not** edit non-test application source, and does **not** run the test suite to "prove" a
  change. the human implements and reports results back.
- **tests are the AI's to write** — actual files on disk, landed before the human codes and starting
  red (see phase 5). the human still runs and debugs them.

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
- **branch per guide**: `main` is the integration/merge branch (live runs under `stage` / `prod`).
  `/setup-guide` cuts a `<area>/<slug>` branch off `main`; the work happens there; `/wrap-up` merges
  it back (`--no-ff`) and deletes it when the guide moves to `done/`.
- **memory** (`/Users/lukas/.claude/projects/-Users-lukas-Projects-jac/memory/`) = durable
  cross-session facts the repo doesn't record. distilled, not a session log.

## skills (slash commands)

these live at the **user level** (`~/.claude/skills/`), so they're available in every project — not
checked into this repo. listed here because the working style above leans on them.

- **`/setup-guide`** — write a code-bearing implementation guide for a roadmap item into
  `.claude/plans/to-do/`. does not touch repo source.
- **`/wrap-up`** — end-of-session combo. self-contained: refreshes the durable docs (this file's
  roadmap + current-state, memory, moves finished plans `to-do/` → `done/`), writes a handover
  snapshot, commits both (no Claude co-author trailer), then hands off to `/clear`. folds in what
  used to be separate `/update-claude`, `/handover`, and `/commit-message` skills.
- **`/pickup`** — the inverse of wrap-up: read the latest handover + git state + open to-do plans
  and give the lay of the land when returning to a dormant project. read-only.
