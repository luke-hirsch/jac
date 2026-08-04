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

| path                     | what                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------- |
| `backend/`               | Django project, mostly headless via DRF                                               |
| `backend/lukehirsch/`    | Django config package — `settings.py`, urls, asgi/wsgi, shared middleware/permissions |
| `backend/jac/`           | the CV tool — career-DB models, CRUD API, and the CV tailoring pipeline (`cv.py`)     |
| `backend/llm_connector/` | reusable multi-provider LLM connector with per-user encrypted configs                 |
| `backend/spa/`           | portfolio / per-visitor profile app                                                   |
| `frontend/`              | Vite + React + TypeScript SPA                                                         |
| `config/`                | nginx config                                                                          |

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

shipped (lean inventory — mechanism + _why_ live in the code and the linked memories, not here):

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
  closing `ResumeSnippet`s by relevance; `CoverLetterWriter` only _weaves_ (posting stripped — the
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
  `PERSONAL_STUB`. Own `ParagraphGroundingCheck`; words fold into `ai_share`. _(Tests green; live LLM
  verification pending.)_ See [[project-purpose-cv-showcase]].
- **jac frontend LLM-config tab** (`frontend/src/lib/queries/llm.ts`,
  `frontend/src/routes/_authenticated/account/llm.tsx`) — account tab doing owner-scoped CRUD over
  `/api/llm/configs/` (the aliases the pipeline resolves through). Picking a commercial provider
  opens a **structured mask** that assembles the `extra` JSON from discrete inputs; `custom`/`ollama`
  get a raw JSON textarea. `api_key` is write-only — the server only ever returns `has_api_key`. The
  pure form↔payload helpers (`toPayload`/`rowToState`/`switchProvider`/…) are unit-tested. See
  [[frontend-test-layout]].
- **jac generation loop — real pipeline, end to end, hardened** (`jac/tasks.py`, `jac/views.py`,
  `jac/consumers.py`, frontend `routes/_authenticated/applications/`) — REST `POST
/api/jac/generations/` (application pk) enqueues Celery `generate_run`, which runs the **real**
  CV + cover-letter pipeline and streams `snapshot`/`progress`/`done`/`failed` over the `gen_<pk>`
  WS (session-auth + ownership); REST `GET` rehydrates; the frontend renders the tailored CV +
  letter (ai_share/grounding badges, personal-paragraph stub styling) and applies runs to the
  application explicitly. **Hardened:** `POST …/<pk>/cancel/` revokes + fails a run (`task_id` on
  the model; the task claims only `pending` runs and finishes only `running` ones, so a cancel
  always wins); enqueue carries `expires=15 min`; task `soft_time_limit=25 min`; LLM
  transport failures (`LLMTransportError`) retry once and surface "retrying in Ns" progress events
  (`llm_connector.client.retry_reporter`); frontend has an Abort button, a stale-queue hint
  (pending >30 s ⇒ "worker may be down"), and WS auto-reconnect with backoff. Channel layer MUST be
  **`channels_redis.pubsub`** (core layer breaks vs redis-py ≥5.1). Dev stack = 4 processes
  (README "Run (dev)"): valkey, ollama, runserver, **celery worker**. See [[generation-async-loop]].
- **jac application editor + render/export** (frontend: `lib/cv-doc.ts`, `lib/letter-doc.ts`,
  `lib/render/{spec,fit,parts,templates}`, `lib/export.ts`; backend: `letter_meta` JSON +
  `/rewrite/` endpoint on applications) — the application is the editable artefact: CV editor
  (reorder / deselect / delete / add-from-career-DB per section), letter editor (meta fields,
  snippet append, AI rewrite of a text selection, stub replace), snippets CRUD UI. Export via
  **react-pdf**: `ApplicationLayout`'s JSON spec drives `CvDocument`/`LetterDocument`; `fitCv`
  drops lowest-ranked entries to the layout's page budget (favourites last), the letter is never
  cut, only flagged; md/json builders; `exportBlocker` = send-time stub gate (pdf/md refuse on
  letter-bearing scopes while the `PERSONAL_STUB` is in the body). Cached server-side `pdf` field
  still future work. See [[cv-render-export-decision]].
- **application detail page decomposed** (`components/applications/`) — the former 1.3k-line route
  is split per card: `posting-card` / `generate-panel` / `result-view` / `content-card` /
  `letter-editor` / `export-card` + a `use-run-lifecycle` hook (reducer + WS + snapshot seed +
  clock + abort); route file is ~70 lines of orchestration. Convention: feature components in
  `src/components/<feature>/`, page hooks live beside them (`lib/queries/` stays toast-free).
- **portfolio — per-visitor rendering + multi-user hosting** (**on `main`**). Owner is resolved from
  the **request host** (`<handle>.<BASE_DOMAIN>` → that user; apex → the configured owner): one SPA
  build serves apex / `app.` / `<handle>.`, and per-origin localStorage keeps each owner's visitor
  separate (no handle in the stamp). Anonymous flow = a **flat-form questionnaire** (real domains
  from `/native/meta/` + a technical↔soft / personal↔formal style axis reusing the
  `PersonalityProfile` vocab + free-text) → an embeddings-ranked selection, plus the **one**
  generative call in the anonymous path: a HirschAI-only, 6/h-throttled **AI intro** that degrades
  to no-intro (the rest is deterministic/free). Empty native result falls back to the owner's
  `is_default` link. `/` is a Django-rendered SEO landing; each user gets an editable `handle`
  (subdomain) + per-user-unique descriptive slugs; open signup is an env toggle
  (`ACCOUNT_ALLOW_SIGNUPS`) with a soft per-user daily generation cap. Two env knobs move the whole
  thing to a neutral domain (`BASE_DOMAIN` + `PORTFOLIO_ORIGIN_TEMPLATE`). Unit tests green both
  sides; **live/prod verification pending**. See [[portfolio-multiuser]].
- **portfolio owner-side authoring — creator UX + block links** (implemented, both guides still in
  `to-do/` pending their Results runs). Owner-side: a reworked create form + a reusable
  `ContentPicker` (`components/portfolio/content-picker.tsx`). Data side: `PortfolioBlock.links`, a
  JSON ordered `type:pk` id-list (the `content.featured` grammar reused — no FK, dead ids drop at
  resolve), resolved **one level deep, self-excluded** and rendered nested beneath the block; a
  career entry claimed by a rendered block no longer floats loose in `more` (`featured` untouched).
  Nested titles hyperlink **on-page-first** (anchor to the item's own card), else out to the entry
  url. See [[portfolio-block-links]].

# roadmap

> this is the **moving part** of this file. it changes as goalposts move; keep it honest.
> granular, code-bearing plans for each item live in `.claude/plans/to-do/` (see "how we work").
> `/wrap-up` refreshes this section at the end of a coding phase.

1. **signup default regression** (one line, do it first) — `settings.py:290` reads
   `env_bool("ACCOUNT_ALLOW_SIGNUPS", True)`; it must be `False` (flipped in `78c2d4c` during dev
   click-testing). Today's dev server has signup open to anyone who can reach it, and the two red
   `spa.tests.test_auth.SignupGateTests` are the guard reporting it — not stale tests. Open signup is
   an **env** flag in prod/stage, never a code default. See [[public-site-posture]].
2. **portfolio creator UX + block links — verification** — both guides sit in `to-do/` with the code
   implemented: guide 1 (`[frontend]-portfolio-creator-ux`) has an empty Results chapter, guide 2
   (`[fullstack]-block-links`) has one bug fixed (a stray line in a test) plus the unrun §7 hyperlink
   follow-up (branch `fullstack/block-links-hyperlinks`, verification steps 7–11).
3. **cover-letter refusal guard** (small) — `CoverLetterWriter` accepts any non-empty LLM
   response, so a spurious small-model refusal ("I can't assist…") can become the letter body.
4. **self-hosted web-search agent** (parked) — let a self-hosted _standard_ run produce a real
   personal paragraph: wire a tool-capable local model to a **self-hostable** search backend
   (SearXNG / Tavily / Firecrawl-style) via a tool-calling loop, folding in the parked `scraper`
   app. The personal-paragraph guide leaves `ollama`/`custom` at `supports_web_search=False` and
   stubs until this lands (Ollama's hosted `/api/web_search` is cloud + key — quick but doesn't
   prove the self-hosted thesis). See [[project-purpose-cv-showcase]].
5. **pricing calculator** (backlog, small) — pre-run cost estimate on the generate panel from
   per-model pricing metadata in the model catalog (see `[fullstack]-model-knobs`).

> **Portfolio generator — merged to `main`; six guides in `done/portfolio/`.** Flow rework
> (owner-fix + dynamic flat-form questionnaire + AI intro + Django landing) and multiuser (host-based
> owner resolution, wildcard subdomain hosting, host-aware SPA routing, open signup). Unit tests
> green both sides (11 frontend skips are the dormant executor-rework SPA-phase guides, unrelated).
> **Remaining is live/prod only** — the Results chapters don't log it: wildcard DNS + DNS-01 wildcard
> TLS + the nginx apex/`app.`/`*.` host-split deploy, flipping `ACCOUNT_ALLOW_SIGNUPS=true` **in the
> prod env** at launch (see roadmap #1 — the code default must go back to `False`), and the manual
> multi-owner + live-AI-intro (tower up) + signup click-through. See [[portfolio-multiuser]].

> **Single-executor redesign — backend landed (`456a72f`…`f738eaf`; rework guides 1–3 in
> `done/`, Results chapters not yet logged).** A run touches exactly one executor: **HirschAI**
> (system-owned ollama row; local MacBook ollama until the tower/VPS move — tower guide parked
> in `plans/backlog/`) or anthropic/openai on the user's key. Modes `manual`/`standard`/`high`
> (`high` commercial-only); per-run model validated against the curated catalog
> (`llm_connector/catalog.py` — the catalog IS the gate); `GET /api/llm/executors/` is the
> SPA's single source; auto-run on application create (backend-side, never retro); entry pins
> force-kept by every rung. **SPA phase = the current `to-do/` stack, in order — ALL guides
> activated (full contracts + tests on disk; 3–6 activated 2026-07-18):**
> `[fullstack]-llm-config-rework` (the former generate-panel + config-tab guides, **merged
> 2026-07-17** — one break, one branch, no frozen zones; step 1 carries every backend repair
> the rework missed, now seven: dead-column config/request-log serializers (also break
> `/api/schema/`), the module `complete()` helper silently **dropping the `executor=` kwarg**
> all eleven pipeline/distill call sites pass (mis-routes every rung to the default executor +
> crashes ollama's payload), chat passing `job_posting=` to a `posting_text=` class,
> `_ai_share`'s eager `_REWRITE_TAX["instruct"]` fallback KeyError-ing every letter,
> `LLMConfig.save()` never enforcing default exclusivity, and the spa dossier-rebuild view's
> old `ensure_dossier(alias=…)` call) → `[frontend]-manual-no-run-mode` →
> `[frontend]-entry-pins-ui` → `[fullstack]-model-knobs` → `[fullstack]-chat-assistant-rework`.
> Guides 3–6 shrank honestly at activation (pins UI/merge + manual seed already typed in the
> cv-editor era — the guides spec only the real deltas); their acceptance tests are on disk
> **skip-marked** with the guide slug (each guide's step 0 = unskip) so the active guide's
> red set stays unambiguous: backend 16 red = exactly the step-1 repairs, frontend 6 red =
> guide-2 libs, 31 backend + 18 frontend skips = guides 3–6.
> The SPA is knowingly broken until the first guide lands. The "current state" bullets above
> still describe the alias/grade era for llm_connector + pipeline — next `/wrap-up` refreshes
> them; the `done/` rework guides are the accurate spec meanwhile.

> **Application editor + render/export phase — done (2026-07-10 wrap-up).** All frontend guides
> (`cv-snippets`, `cv-editor`, `letter-editor`, `tailored-render`, `render-export`) are in
> `plans/done/`; `to-do/` is empty. Note: `render-export` moved to `done/` without a `## Results`
> chapter — no logged verification run; the detail-page component split (2026-07-10) is also
> pending Lukas's `tsc`/vitest/click-through.

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
8. **human tests and debugs**, logging the outcome in the guide's closing `## Results` chapter
   (raw test output + observed issues + what works). that chapter is the bug list follow-up work
   starts from — AI reads it first when debugging, and `/wrap-up` checks it before a guide moves
   to `done/`.

## who does what (default-strict)

- **AI**: diagnoses (probes / experiments to decide _what_ to build), and writes code-bearing
  setup guides + tests. AI also maintains the Claude-meta docs (this file, `.claude/skills/*`,
  memory, plan files).
- **human (Lukas)**: types the application/repo **non-test** source code, runs it, and does **all**
  testing and verification.
- AI does **not** edit non-test application source, and does **not** run the test suite to "prove" a
  change. the human implements and reports results back.
- **tests are the AI's to write** — actual files on disk, landed before the human codes and starting
  red (see phase 5). the human still runs and debugs them.

**override**: the human can explicitly open a _volatile / exploration phase_ ("just code it",
"spike this") — then the AI may write source directly. for that ai will switch on its own git branch this is opt-in per task, not the default;
testing adn merging still stays with the human.

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
