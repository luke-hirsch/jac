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

- jac CV pipeline (`backend/jac/cv.py`, `filter.py`): `CV` loads/flattens career entries (each
  carries a `refs` edge list); `CVFilter` is the selection layer. The `light` rung (`Embed`) does
  propagation + per-section *floor + min-keep* drop; the `standard` rung (`Instruct`) does
  keep-by-verdict on LLM relevance labels (`_select_ranked`). The chosen **alias** threads through
  the pipeline (`filter_cv(alias=…)` → `CVFilter` → `Embed`/`Instruct`), so the LLM rungs use the
  picked model and `light` embeds with that model's `embed_model`. Per-embedder cosine floors are
  overridable via an `embed_floors` config key (merged over `_SECTION_POLICY` defaults).
- jac eval tooling: `cv_test` / `cv_eval` management commands. `cv_eval` picks the model+grade via a
  matrix (`--llm <alias>` and/or `--grade`; grade auto-detects from model strength when omitted, or
  fans out over **all** configured models at a forced grade); `--all-models` runs every configured
  model at its own grade **plus** the `default` embedding baseline; with no selection flag in a
  terminal (or `--pick`) it opens an interactive questionnaire (models / grade / analysis). Output:
  per-model tables in `findings.md` (colour-graded one-page targets) + per-model artifacts.
  `--analyze` adds an AI layer (`llm_prompts.TheJudge` grades each run's kept-vs-dropped selection;
  `TheAnalyst` writes a cross-model summary into `findings.md`), run under a fixed strong `--analyst`
  alias. `get_alias_strength` autodetects embedders → `light`.
- jac: **favourite** flag on every `CvEntry` — pins an entry for a small post-propagation ranking
  nudge (`CVFilter._FAVOURITE_BONUS`, kept below the lowest section floor so it can't resurrect a
  ~0-scored entry), capped per type (`CvEntry.FAVOURITE_LIMIT`, enforced in `model.clean` + a
  serializer mixin). Wired through the API + CRUD UI (editor toggle + sortable star column).

- jac CV ladder — **all three rungs done**. `strong` (`Conversational`, `llm_prompts.py`) reads the
  posting + every entry and returns an *ordered, chosen set* (`<id> — <why>` lines, best first), not
  scores; `CVFilter._select_holistic` applies guardrails only (pin favourites, hold `min_keep`, never
  drop languages — no floors / propagation / count clamp). `output()` routes strong → standard →
  light, each degrading to the next on empty.

- jac cover-letter pipeline (`backend/jac/cover_letter.py`): `SnippetSelector` picks 1 intro / 1
  closing / up-to-N body snippets from `ResumeSnippet` boilerplate by relevance to the filtered CV
  (relevance-dominant, with a native-language tie-break that reorders but never resurrects a
  0-score snippet); `CoverLetterWriter` (`llm_prompts.py`, writer `llama3.2:1b`) only *weaves*;
  `CoverLetter.build()` assembles bilingual furniture (`de`/`en`) around the body and computes a
  per-letter **`ai_share`** provenance metric (`_ai_share`: length-weighted native-vs-translated +
  a per-grade rewrite tax). `ResumeSnippet.language` flag + a `load_snippets` seeder (DE/EN pairs).
  `JobPosting` + `JobPostAddress` models hold the posting + `AddressExtract`-parsed employer block;
  `cover_letter` management command smoke-tests over a corpus. *(Merged to `main`.)*

# roadmap

> this is the **moving part** of this file. it changes as goalposts move; keep it honest.
> granular, code-bearing plans for each item live in `.claude/plans/to-do/` (see "how we work").
> `/wrap-up` refreshes this section at the end of a coding phase.

1. **cover-letter generation** — **in progress (on `backend/cover-letter`).** Core pipeline +
   `ResumeSnippet.language` flag + per-letter **`ai_share`** provenance metric have landed (merged to
   `main`; see current state). **Next sub-step (guide in to-do,
   `[backend]-cover-letter-grounding.md`, tests written):** (a) **drop the job posting from the
   `CoverLetterWriter` prompt** — it's the main hallucination vector (a weak model mirrors the
   posting's wish-list back as the candidate's facts); the writer weaves authored snippets only. (b)
   add a **faithfulness/grounding check** (`FaithfulnessCheck`, mirrors `TheJudge`): reads the body +
   snippets (not the posting) and flags claims the snippets don't support, surfaced like `ai_share`
   ("⚠ 2 unsupported claims"), opt-in under a strong `--verifier-llm`. `ai_share` measures
   *provenance* (how much the machine wrote); grounding measures *faithfulness* (did it lie) — these
   are orthogonal, which is why a 5% `ai_share` letter still hallucinated. See
   [[cover-letter-grounding-metric]].
2. **frontend render** of the tailored CV + cover letter.
3. **portfolio generator** — per-visitor portfolio rendering, frontend + backend.

> **CV ladder (roadmap item #1) — done.** All three rungs landed: `light` (embeddings →
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
