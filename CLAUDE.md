# project

a personalised **public** portfolio site + a CV automation tool (**jac**), in one monorepo. the
portfolio is the product; jac feeds it.

- **portfolio**: no static page. content is rendered per visitor based on how they arrived —
  e.g. a visitor from a business-card QR sees something different than one arriving from a CV on a
  job post; organic visitors introduce themselves via a questionnaire and get content matched to
  their answers. it renders from the **same career-DB entries** jac maintains, and it is public.
- **jac** (Job Application Creator): a pipeline that digests a job posting and renders a CV +
  cover letter tailored to it with the help of LLMs. it is Lukas's own tool _and_ the portfolio's
  proof of concept: a recruiter who sees jac as a CV entry and the portfolio rendering itself must
  be able to **try it on the spot** ("here, try it yourself"), not be sent to GitHub to self-host.
  so **signups are open by default** — `ACCOUNT_ALLOW_SIGNUPS` is a kill switch Lukas flips _shut_
  if funky business shows up, never a door that starts locked. **security posture: every
  authenticated surface is internet-facing, today, for real**; public endpoints opt in via explicit
  `AllowAny` on top of the deny-by-default DRF setting, never public-by-omission, and abuse control
  is rate limits + the soft per-user daily generation cap, not the signup door.

# layout

| path                     | what                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------- |
| `backend/`               | Django project, mostly headless via DRF                                               |
| `backend/lukehirsch/`    | Django config package — `settings.py`, urls, asgi/wsgi, shared middleware/permissions |
| `backend/jac/`           | the CV tool — career-DB models, CRUD API, and the CV tailoring pipeline (`cv.py`)     |
| `backend/llm_connector/` | reusable multi-provider LLM connector with per-user encrypted configs                 |
| `backend/spa/`           | portfolio / per-visitor profile app                                                   |
| `frontend/`              | Vite + React + TypeScript SPA                                                         |
| `deploy/`                | what the image carries (`migrate`, nginx) + the lab's compose override                |
| `config/`                | the lab board's exports (untracked: they name lab addresses)                          |

don't maintain a deep file tree here — it drifts. open the dir.

# stack

- **backend**: Django + Django REST Framework, headless. JAC CRUD under `/api/jac/`.
- **auth**: `django-allauth` in headless mode — MFA (TOTP + WebAuthn passkeys + recovery codes),
  mandatory email verification, admin MFA gate via middleware.
- **frontend**: React 19 + Vite + TypeScript — TanStack Router/Query/Form/Table, Tailwind v4,
  shadcn/ui.
- **db**: Postgres in dev, the suite, CI and prod (decided 2026-11-04: the lab already runs one, and
  prod runs the engine the suite tests). URL via `DATABASE_URL`; unset on a laptop means its own
  server over the socket. **The switch is on `backend/prod-readiness`, unmerged.** See
  [[postgres-everywhere]].
- **llm**: `llm_connector` app — providers for Anthropic, OpenAI and Ollama (= HirschAI);
  per-user configs with Fernet-encrypted keys. zero-cost server default is the `ollama` provider.
  - HirschAI defaults (`settings.HIRSCHAI`, env-overridable): chat/writer **`qwen3.5:9b`** ·
    embedding **`qwen3-embedding:0.6b`**
- **python env**: pyenv (`jac` virtualenv).
- **hosting**: self-hosted on the lab, nothing deployed yet. Contract: `docs/app-contract.md` in
  the server repo (the homelab skill; see the board test under "Current phase"). One image, generic
  `compose.yaml`; the only file that knows the lab is `deploy/compose.lab.yaml`. The app must also run
  on a plain rented VPS: [[normal-infra-test]].
- **queues** ([[queue-lanes]]): `jac-ai` (HirschAI runs, `-c 1`), `jac-cloud` (runs on a user's key),
  `jac-quick` (short AI jobs a page polls), `jac-mail` (mail, housekeeping, the scheduler).

# current state

shipped (lean inventory — mechanism + _why_ live in the code and the linked memories, not here):

- **auth / MFA** — full flow, backend + frontend.
- **jac career DB** — models (`Domain`, `Location`, `Skill`, `Job`, `Project`, `Education`,
  `Certification`, `Language`) + full CRUD API/serializers + frontend UI.
- **`llm_connector`** — multi-provider connector (Anthropic, OpenAI, Ollama; Google + custom were
  dropped in the executor rework), per-user Fernet-encrypted configs. One run touches exactly one **`Executor`** (HirschAI = the
  system ollama row, or a commercial provider on the user's key); the curated
  `llm_connector/catalog.py` is the gate for per-run model choice. Optional capabilities gated by
  class flags: `embed()` (Ollama) and `web_search()`/`supports_web_search` (Anthropic; OpenAI via
  Responses API). All LLM I/O is line-format, not JSON.
  See [[no-json-llm-io]], [[llm-executor-rework]].
- **jac CV pipeline** (`cv.py`, `filter.py`) — `CV` flattens entries (`refs` edges); `CVFilter`
  selects by **mode**: `manual` (no run), `standard` (`Embed` ranking, plus `Instruct` 0–3 labels →
  keep-by-verdict), `high` (commercial only — `Conversational` holistic ordered set, guardrails
  only); `output()` degrades within the run's single executor, HirschAI getting one attempt.
  `embed_floors` overridable; **favourite** flag = small capped ranking nudge; entry **pins** are
  force-kept by every rung. See [[project_jac]], [[llm-executor-rework]],
  [[selection-size-is-intentional]].
- **jac cover-letter** (`cover_letter.py`, `llm_prompts.py`) — the writer's only source of facts is
  the tailored CV (`ResumeSnippet` is gone), and the posting is stripped outside `high` (the
  fabrication vector); bilingual `de`/`en` furniture; `JobPosting`/`JobPostAddress` +
  `AddressExtract`. Two orthogonal metrics: **`ai_share`** (provenance) and **`FaithfulnessCheck`**
  grounding (`count=None` on failure, never 0). (The `cv_test` / `cv_eval` / `cover_letter`
  eval commands were deleted in `456a72f`; today's commands: `cv_export`, `cv_import`,
  `seed_system_defaults`, `vector_sync`, `llm_check`.)
  See [[cover-letter-language-strategy]], [[cover-letter-grounding-metric]].
- **spa personality questionnaire** (`PersonalityProfile`) — ~5-of-12 oblique free-text answers
  (≤280 chars) distilled into one cached `dossier` (`ensure_dossier`, rebuilt only on change).
- **jac cover-letter personal paragraph** (`cover_letter.py` `_personal_paragraph`, `research.py`,
  `PersonalParagraphWriter`) — one researched, company-specific paragraph (web research × personality
  dossier) after the body. Opt-in (`--personal`), **capability-driven not mode-gated**: real only
  when the run's executor can web-search + research ok + personality present, else a loud
  `PERSONAL_STUB`. Own `ParagraphGroundingCheck`; words fold into `ai_share`. _(Tests green; live LLM
  verification pending.)_ See [[project-purpose-cv-showcase]].
- **jac frontend LLM-config tab** (`frontend/src/lib/queries/llm.ts`,
  `frontend/src/routes/_authenticated/account/llm.tsx`) — account tab doing owner-scoped CRUD over
  `/api/llm/configs/`: one row per commercial provider (key + default flag) plus a live check,
  driven by `GET /api/llm/executors/`. `api_key` is write-only — the server only ever returns
  `has_api_key`. See [[frontend-test-layout]].
- **jac generation loop — real pipeline, end to end, hardened** (`jac/tasks.py`, `jac/views.py`,
  `jac/consumers.py`, frontend `routes/_authenticated/applications/`) — REST `POST
/api/jac/generations/` (application pk) enqueues Celery `generate_run`, which runs the **real**
  CV + cover-letter pipeline and streams `snapshot`/`progress`/`done`/`failed` over the `gen_<pk>`
  WS (session-auth + ownership); REST `GET` rehydrates; the frontend renders the tailored CV +
  letter (ai_share/grounding badges, personal-paragraph stub styling) and applies runs to the
  application explicitly. **Hardened:** `POST …/<pk>/cancel/` revokes + fails a run (`task_id` on
  the model; the task claims `pending` runs, or `running` ones a dead worker left behind, ≤3 attempts,
  and finishes only `running` ones, so a cancel always wins); enqueue carries `expires=3 h`; soft
  limit 40 min. A model server that is off or waking holds the run under a "waiting for compute"
  stage for up to 15 min; a commercial provider's transport failure (`LLMTransportError`) retries once
  and surfaces "retrying in Ns" progress events (`llm_connector.client.retry_reporter`); frontend has
  an Abort button, queue position, and WS auto-reconnect with backoff. Channel layer MUST be
  **`channels_redis.pubsub`** (core layer breaks vs redis-py ≥5.1). Dev stack: README "Run it";
  **the celery worker is required** for runs and the editor's AI tools. See
  [[generation-async-loop]].
- **jac application editor + render/export** (frontend: `lib/cv-doc.ts`, `lib/letter-doc.ts`,
  `lib/render/{spec,fit,parts,templates}`, `lib/export.ts`; backend: `letter_meta` JSON +
  `/rewrite/` endpoint on applications) — the application is the editable artefact: CV editor
  (reorder / deselect / delete / add-from-career-DB per section), letter editor (meta fields,
  AI rewrite of a text selection, stub replace). Export via
  **react-pdf**: `ApplicationLayout`'s JSON spec drives `CvDocument`/`LetterDocument`; the page fit
  (`lib/render/fit.ts`) **demotes, drops and grows** to the page budget, the letter is never cut,
  only flagged; md/json builders; `exportBlocker` = send-time stub gate (pdf/md refuse on
  letter-bearing scopes while the `PERSONAL_STUB` is in the body). Cached server-side `pdf` field
  still future work. See [[cv-render-export-decision]].
- **CV typography** (`lib/render/{parts,templates}.tsx`, layout JSONs) — per-entry skill clouds moved
  to the machine layer, unbreakable two-line date column (dash-aligned with the heading), markdown-ish
  bullets, and **two detail levels per entry** (`full` / `compact`): `cv.detailed` says how many
  entries a section describes, rank picks which, `entry.detail` overrides. Preview overlay
  (`lib/render/preview.ts` + `pdf-preview-dialog`) shares its notice list with the export toasts.
- **page-fit preflight + CV section toggles** (`components/applications/use-preflight.ts`,
  `JobApplication.sections_off`) — the editor measures the real react-pdf render ahead of export and
  reports what the export _will_ do (demote / drop / grow, page count, letter pages); a module-level
  cache keyed by `preflightKey` is shared with the export card. Whole sections switch off per
  application (`sections_off`, server-validated) and a section that is off counts as cut **on
  purpose**, so it never shows up as a fit warning. Sidebar entries pair into columns when the
  layout spec nests them (`sidebar: ["skills", ["certifications", "languages"]]`).
- **cover-letter fit** (`llm_prompts.py` `ShortenLetter`, `lib/render/letter-fit.ts`,
  `components/applications/highlighted-textarea.tsx`) — the writer targets **170–230 words** (a DIN
  page 1 holds ~230; the old 200–320 band was specified to overflow). The page fit lives in the
  browser, so `POST …/applications/<pk>/shorten/` only does the language part: it shortens to a
  **client-measured** word budget with the paragraph count held fixed (502 on an empty reply, like
  `/rewrite/`). The editor binary-searches word boundaries for where page 1 ends, highlights the
  overflowing tail in a mirror textarea, and "shorten to fit" loops ≤3 attempts. The letter PDF
  honours `cover_letter.din5008` (off = address/body move up ~25 mm), no longer repeats the address +
  date on page 2, and prints a long-form de/en date. _(Tests green; click-through pending.)_
- **German letter register + refusal guard** (`jac/register.py`, `llm_prompts.py`
  `address_clause`, `letter_meta.address_form`). Personal German letters say _ihr_, formal _Sie_,
  and neutral mirrors the posting's _ihr_/_Sie_ (a _du_ posting still gets _Sie_). The letter
  furniture follows the tone. One clause covers the writer, rewrite, shorten and chat, so the editor
  tools keep the form. A regex **flags** slipped pronouns live in the editor and never rewrites
  them. Refusals and one-liners become `LETTER_STUB`. _(Merged unverified: Results skipped.)_
- **education degree level** (`Education.degree_level`, `cv.py:highest_degree_id`) — an ordered
  `DegreeLevel` enum (`none` → `doctorate`) records what an entry **earned**, not what it aimed at,
  so "highest" is a `max()` rather than free-text guesswork. The highest completed degree is added
  to the pin set by `filter_cv`, so it survives every selection rung; an unfinished study period is
  ordinary rankable content that reads as one honest coursework line (`— no degree` in the editor
  label, `(no degree)` in the PDF heading). `grade` stays display-only. See [[education-degree-level]].
- **application detail page decomposed** (`components/applications/`) — the former 1.3k-line route
  is split per card: `posting-card` / `generate-panel` (run controls + the result badges) /
  `content-card` / `letter-editor` / `export-card` + a `use-run-lifecycle` hook (reducer + WS + snapshot seed +
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
  (subdomain) + per-user-unique descriptive slugs; signup ships open (`ACCOUNT_ALLOW_SIGNUPS` is the
  kill switch) with a soft per-user daily generation cap. Two env knobs move the whole
  thing to a neutral domain (`BASE_DOMAIN` + `PORTFOLIO_ORIGIN_TEMPLATE`). Unit tests green both
  sides on `main`; **live/prod verification pending**. See [[portfolio-multiuser]].
- **portfolio owner-side authoring — creator UX + block links** (implemented, both guides still in
  `to-do/` pending their Results runs). Owner-side: a reworked create form + a reusable
  `ContentPicker` (`components/portfolio/content-picker.tsx`). Data side: `PortfolioBlock.links`, a
  JSON ordered `type:pk` id-list (the `content.featured` grammar reused — no FK, dead ids drop at
  resolve), resolved **one level deep, self-excluded** and rendered nested beneath the block; a
  career entry claimed by a rendered block no longer floats loose in `more` (`featured` untouched).
  Nested titles hyperlink **on-page-first** (anchor to the item's own card), else out to the entry
  url. See [[portfolio-block-links]].
- **appearance settings** (`components/appearance-provider.tsx`, `lib/appearance.ts`,
  `lib/render/accent.ts`, `UserProfile.accent_color`). Profile theme/contrast become `<html>`
  classes (`.dark`, `.contrast-high`, which compose); `system` follows the OS live. An inline
  pre-paint script in `index.html` mirrors `lib/appearance.ts` from a localStorage copy of the
  profile, so a reload doesn't flash; it needs a hash if a CSP lands. The CV accent is a per-user
  override of the layout's `colors.accent`, clamped to WCAG AA on white, and every render path gets
  it through `useRenderSpec`. Profile PATCHes write the `["profile"]` cache, so changes apply
  without a reload.

- **The launch stack (2026-11-03, implemented by the AI, one commit on `main` since the repo was
  recreated).** See "Current phase". In one line each:
  - **prod-readiness:** `DEBUG` off unless set (laptops: `backend/.env.local`), Postgres, hashed lock,
    private uploads behind owner-checked views.
  - **remote model:** a model server that is off or waking is normal. Runs always queue, wait and say
    so, restart safely. No probes on page loads; env wins over the system row.
  - **queues:** short AI jobs (`llm_connector.models.AiJob`, 202 + polling), queued mail, one mail when
    a user's queue drains, batch generation, a scheduled reaper, housekeeping.
  - **image:** one image, nginx + daphne + workers as separate services.
  - **legal pages:** imprint, privacy, terms (**German drafts with 14 `TODO(Lukas)`, not legal advice**),
    account deletion removes files + logs.
  - **CI:** `.github/workflows/test.yml`; ruff + eslint at 0 errors.
  - **editor safety:** three-way draft merge (`lib/draft-merge.ts`), export saves first, leave guard.
  - **first run:** "Try it with a sample career" (`POST /api/jac/sample-career/`), a way out of the
    verify page, real signup errors, error states with retry, a11y + mobile fixes.
  - **abuse limits:** an append-only daily counter behind a row lock, input caps, upload quota +
    throttle, mail limits that hold, one chat stream per user, `ObjectJSONParser`.
  - **portfolio privacy:** **a handle publishes nothing until its owner says so**
    (`portfolio_public`, default off; the site owner is always public; shared links keep working),
    unguessable application slugs, `sections_off` honoured, `?q=` out of the URL.

# roadmap

> this is the **moving part** of this file. it changes as goalposts move; keep it honest.
> granular, code-bearing plans for each item live in `.claude/plans/to-do/` (see "how we work").
> `/wrap-up` refreshes this section at the end of a coding phase.

> **Current phase — finish verifying the launch stack on Postgres, then deploy.** The launch stack the
> AI implemented on 2026-11-03 landed on `main` as one commit when the repo was recreated on
> 2026-11-04, which also purged the certificates from the public history. **CI is red on `main`.** The
> fix is guide 03's Postgres amendment, implemented by the AI on `backend/prod-readiness`, unmerged and
> unpushed. Lukas logged both suites clean on that branch; its Verification 3–8 are still open, and it
> merges once they're logged. Nothing is deployed.
>
> **Hosting changed on 2026-11-03: the homelab replaces the VPS plan.** Guide 04 is the runbook now.
>
> **This is also a test of the board:** work from the exports + this repo, not the server repo or
> the homelab skill, until Lukas lifts that ([[jac-on-the-lab]]). The exports predate the Postgres
> move, so the board's database answer needs flipping and a re-export.

> **Reseed after pulling layout changes:** `python manage.py migrate && python manage.py
seed_system_defaults`. The layout spec the SPA renders from is the **media copy in the DB**, so an
> unseeded change looks like an unimplemented feature.

**Unverified on `main`** (merged without a logged click-through; start any related bug hunt from
these guides' Verification lists, in `done/`):

- **`[fullstack]-letter-register-de`.** Results were skipped, so nothing is logged, not even the
  suite. Worth one German run per tone (Verification 2–4) before launch: a recruiter trying it on
  the spot will very likely paste a German posting.
- **`[fullstack]-letter-fit`.** Verification 2–10 is unlogged. Two checks matter most: the overflow
  highlight starts on the right word (mirror alignment, §8, with the fallback documented there) and
  the rewrite popover still sits under the selection (offsetParent, §9 hazard 1).

**Launch track.** Implemented, each guide in `to-do/` awaiting its Results:

3. **prod-readiness** (`[backend]-prod-readiness`): includes the move to Postgres.
   **3b. remote model** (`[backend]-remote-model`). **3c. queues** (`[fullstack]-queues`).
4. **lab deploy** (`[infra]-lab-deploy`): the repo side is done. **The rest is Lukas's: the board's
   re-export, SSH, DNS, the launch checklist.** Prod starts with an empty database. It needs the lab's
   Caddy route + wildcard route first.
5. **GitHub Actions** (`[infra]-github-actions`): part A done, part B dropped.
6. **tower inference server** (`[infra]-tower-inference-server`, in `done/`): superseded, never built.
   Its Ollama tuning (flash attention + q8 cache, so the 9B model + the embedder fit 8 GB) moved to
   guide 04 step 2.
7. **legal pages** (`[fullstack]-legal-pages`): built; **the text is a draft for Lukas to finish**.
8. **README** (`[docs]-readme`): rewritten; its links resolve once the DNS is live.

**After launch / parked:** (run notifications and batch generation landed in `[fullstack]-queues`)

- **prewarm ping** (parked): the board's `AI_PREWARM_KEY`. Nothing to call until the wake proxy exists,
  and no use on a rented box.

- **self-hosted web-search agent** (parked) — let a self-hosted _standard_ run produce a real
  personal paragraph: wire a tool-capable local model to a **self-hostable** search backend
  (SearXNG / Tavily / Firecrawl-style) via a tool-calling loop, folding in the parked `scraper`
  app. Until this lands, `ollama` stays at `supports_web_search=False` and its runs stub the
  paragraph (Ollama's hosted `/api/web_search` is cloud + key — quick but doesn't prove the
  self-hosted thesis). See [[project-purpose-cv-showcase]].
- **pricing calculator** (backlog, small) — pre-run cost estimate on the generate panel from
  per-model pricing metadata in the model catalog (see `[fullstack]-model-knobs`).

> **Known open question, deliberately not acted on:** the fit is weight-blind. `addOrder` ranks grow
> candidates by position fraction within their pool, so the biggest pool wins and leftover page space
> buys ~9 more skills before one more job (measured: **6 skills ≈ 1 line ≈ 8.2pt**; a job entry ≈ 5
> lines). `SECTION_WEIGHT` already exists. This is what the fit-preflight Results Round 2 asks as
> "why won't skills fit" — seen against a real page and judged **not worth changing yet**: the page
> is used well as-is.

> **Portfolio generator — merged to `main`; six guides in `done/portfolio/`.** Flow rework
> (owner-fix + dynamic flat-form questionnaire + AI intro + Django landing) and multiuser (host-based
> owner resolution, wildcard subdomain hosting, host-aware SPA routing, open signup). Unit tests
> green both sides (11 frontend skips are the dormant executor-rework SPA-phase guides, unrelated).
> **Remaining is live/prod only** — the Results chapters don't log it. It now lives in roadmap 4
> (`[infra]-lab-deploy`, launch checklist). Signup needs no launch flip — it ships **open**
> ([[public-site-posture]]). See [[portfolio-multiuser]].

> **Single-executor redesign — done, backend and SPA (all six guides in `done/`).** A run touches
> exactly one executor: **HirschAI** (system-owned ollama row; local MacBook ollama until the lab
> deploy — roadmap 4) or anthropic/openai on the user's key.
> Modes `manual`/`standard`/`high` (`high` commercial-only); per-run model validated against the
> curated catalog; `GET /api/llm/executors/` is the SPA's single source; auto-run on application
> create (backend-side, never retro). The alias/grade vocabulary is gone. See
> [[llm-executor-rework]].

> **Application editor + render/export phase — done (2026-07-10 wrap-up).** All frontend guides
> (`cv-snippets`, `cv-editor`, `letter-editor`, `tailored-render`, `render-export`) are in
> `plans/done/`. Note: `render-export` moved to `done/` without a `## Results` chapter — no logged
> verification run; the current phase has been rewriting that code anyway.

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
  guide per item, named `NN_[area]-<slug>.md` (`[backend]` / `[frontend]` / …), where `NN` is the
  guide's roadmap position (`01_`, `02_`, …). cross-references cite the bare `[area]-<slug>`, so a
  renumber never breaks them. completed guides move to `done/`.
- **branch per guide**: `main` is the integration/merge branch. `/setup-guide` cuts a
  `<area>/<slug>` branch off `main`; the work happens there; `/wrap-up` merges it back (`--no-ff`)
  and deletes it when the guide moves to `done/`. Deploying a green `main` commit is a human
  decision, run from the laptop through the lab's deploy script (per the board's exports).
- **memory** (`/Users/lukas/.claude/projects/-Users-lukas-Coding-jac/memory/`) = durable
  cross-session facts the repo doesn't record. distilled, not a session log.
- **`.claude/` is gitignored** since the repo was recreated: plans and handovers live on this machine
  only, so `/wrap-up` commits nothing but this file.

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
- **`agents`** - use as many agents you like on a task if it benefits the time
