# project

A personalised **public** portfolio site + a CV automation tool (**jac**), in one monorepo. The portfolio
is the product; jac feeds it.

- **portfolio:** no static page. Content is rendered per visitor, by how they arrived: a business-card QR,
  a CV link, or a questionnaire for strangers. It renders from the same career-DB entries jac maintains.
- **jac** (Job Application Creator): digests a job posting and renders a tailored CV + cover letter with
  LLMs. Lukas's own tool _and_ the portfolio's proof of concept: a recruiter must be able to **try it on
  the spot**, not be sent to GitHub to self-host.
- **security posture:** every authenticated surface is internet-facing, for real. Signups are open by
  default: `ACCOUNT_ALLOW_SIGNUPS` is a kill switch, never a locked door. Public endpoints opt in via
  explicit `AllowAny` on top of deny-by-default DRF, and abuse control is rate limits + the soft daily
  generation cap. See [[public-site-posture]].

# layout

| path                     | what                                                                  |
| ------------------------ | --------------------------------------------------------------------- |
| `backend/lukehirsch/`    | Django config: settings, urls, asgi, shared middleware + permissions  |
| `backend/jac/`           | The CV tool: career DB, CRUD API, selection, letter, generation tasks |
| `backend/llm_connector/` | Multi-provider LLM connector, per-user encrypted keys, short AI jobs  |
| `backend/spa/`           | Portfolio, per-visitor rendering, profiles, legal pages               |
| `backend/vector_store/`  | Optional RAG read path (`VECTOR_STORE`)                               |
| `frontend/`              | Vite + React + TypeScript SPA                                         |
| `deploy/`                | What the image carries (`migrate`, nginx) + the lab's compose override |
| `config/`                | The lab board's exports (untracked: they name lab addresses)          |

Don't keep a deep file tree here: it drifts. Open the dir.

# stack

- **backend:** Django 6 + DRF (headless), Channels, Celery, `django-allauth` headless (MFA, passkeys,
  mandatory email verification).
- **frontend:** React 19, Vite, TanStack Router / Query / Form / Table, Tailwind v4, shadcn/ui, react-pdf.
- **data:** Postgres in dev, suite, CI and prod, via `DATABASE_URL`; unset on a laptop means its own socket
  ([[postgres-everywhere]]). Valkey is broker, channel layer and cache.
- **llm:** one run touches exactly one executor: HirschAI (the system Ollama row, `qwen3.5:9b` +
  `qwen3-embedding:0.6b`) or Anthropic / OpenAI on the user's key ([[llm-executor-rework]]). All LLM I/O is
  line-format, never JSON ([[no-json-llm-io]]).
- **queues:** `jac-ai`, `jac-cloud`, `jac-quick`, `jac-mail` ([[queue-lanes]]).
- **python:** the pyenv virtualenv `jac`. The image, CI and the lock are 3.12.
- **hosting:** self-hosted on the lab, nothing deployed. One image, generic `compose.yaml`; only
  `deploy/compose.lab.yaml` knows the lab. It must also run on a plain rented VPS ([[normal-infra-test]]).
- **hosts:** four fixed names under `BASE_DOMAIN`, **no wildcard** (decided 2026-11-07):
  - the apex is Django's server-rendered landing (SEO), whose "Explore my work" goes to
    `portfolio.<domain>/<owner>/`; any other apex path 302s to the portfolio host, and `www.` 301s to the apex;
  - `jac.` is the app (it was `app.`);
  - `portfolio.<domain>/<handle>/` and `/<handle>/<slug>/` are every user's portfolio and links.

  The owner comes from the URL path (`/api/spa/public/<handle>/…`), never the Host header. Don't bring back per-user
  subdomains: Lukas dropped them because the wildcard's tail on the lab (DNS-01, Caddy's Cloudflare module) wasn't
  worth it ([[portfolio-multiuser]]).

# run it

```bash
./dev               # web + worker + vite, one log, Ctrl-C stops all of it
./dev stack         # the production image here, with its own database (./dev stack down)
```

`./dev` starts Postgres and Valkey if they're down and catches up on the lock, migrations and layouts. The
model is left to you: `ollama serve` or the app. The README has the rest, including which config file does what.

- **Without the worker, runs and the editor's AI tools sit pending.** A bare `runserver` isn't the app.
- **The worker doesn't reload:** restart after changing a task or the pipeline.
- **Layouts render from the media copy in the database:** an unseeded change looks like a missing feature.
  `./dev` seeds on every start.

Tests: `cd backend && python manage.py test --skip llm --parallel auto`, `cd frontend && npm test`.

# where things stand

**Going live. There is one guide: `.claude/plans/to-do/01_[fullstack]-launch.md`, holding only what's left for
Lukas, in the order of the work:**
- step 1: his dev check of both branches, the new layout and the old browser checks;
- step 2: merging both;
- step 3: setting up the mini;
- step 4: the live checklist.

It was rewritten for the four named hosts on 2026-11-07. The wildcard version and the earlier ones are in
`.claude/plans/done/`. **Until launch the AI codes at his request ("we're at a volatile stage")**, on its own branches;
the browser check and the merge stay his.

- **Branches:**
  - `main` holds the Postgres move, prewarm, the race fix, the rewritten legal pages and what they promise (consent
    for a key, the deleted-account mail). CI is green since 2026-11-06, and it's pushed.
  - `fullstack/launch-decisions` holds 2b: random handles, the consent gate and card, the audit fix, the legal sign-off.
  - `fullstack/portfolio-paths`, cut from it, holds the four-host layout.

  Both are by the AI and wait for Lukas's dev check, then merge together.
- **The legal pages** say du and name no infrastructure ([[legal-no-infrastructure]]). Since 2026-11-07 they say `jac.`
  and `portfolio.<domain>/<name>`. Of what their text commits jac to ([[legal-consent-and-restore]]), consent and the
  deleted-account record are built. The shortened log address waits on the lab, and the visitor hash on the board. The
  DNS stays at Strato, so section 11 no longer names Cloudflare.
- **Nothing is deployed, and merged isn't verified.** What needs a browser is guide step 1, and it belongs before going
  live.
- **The lab:**
  - labd, the tower's wake proxy, runs (server guide 18, tested 2026-11-06). jac needs only its `.env` values (step 3h).
  - Prewarming is built and dormant until `HIRSCHAI_PREWARM_KEY` is set.
  - There's no nightly backup yet, only the deploy dumps on the mini's disk.
  - The public route for the four names is server guide 19, rebuilt for them on 2026-11-07 (server commit `724b177`;
    DNS stays at Strato, a certificate per name). The board's `-7` export is in `config/`.
- **The board test still stands:** work from the exports in `config/` + this repo, not the server repo or
  the homelab skill, until Lukas lifts it ([[jac-on-the-lab]]). Lab addresses stay out of tracked files,
  guides and handovers: the repo is public ([[public-repo]]).

What's already built lives in the code and the linked memories, not in a list here. **"Implemented" means
the code exists. "Works" needs a logged run:** [[implemented-is-not-verified]].

# how we work

Hands-on coding for a human who stays on top of their own codebase:

1. Lukas says what he wants; the AI plans, pushes back, sharpens.
2. The AI writes a **code-bearing guide** (`/setup-guide`) and the **tests to disk, red**: they are the
   acceptance criteria.
3. **Lukas types the code,** the AI corrects and improves.
4. **Lukas tests,** logging the outcome in the guide's closing `## Results`, which is the bug list follow-up
   work starts from.

**Who does what, default-strict:**

- **AI:** diagnoses with probes, writes guides and tests, maintains the Claude-meta files (this file,
  `.claude/`, memory). It doesn't edit non-test source and doesn't run the suite to prove a change.
- **Lukas:** types application + repo source, runs it, does all testing and verification, merges.
- **Override, per request only:** "just code it", "spike this" ([[ai-implements-guides]]). The AI works on its
  own branch; testing and merging stay with Lukas.

**Files:**

- **`CLAUDE.md`:** stable facts and how we work. Keep it lean; no claim may contradict the code.
- **`.claude/plans/to-do/` → `done/`:** one guide per item, `NN_[area]-<slug>.md`, one branch per guide,
  `<area>/<slug>`. `main` is the merge branch. Deploying a green `main` commit is Lukas's decision.
- **memory:** durable facts the repo doesn't record, distilled, not a session log.
- **`.claude/` is gitignored:** plans and handovers live on this machine only, with no history.

**Skills (user-level):** `/setup-guide`, `/wrap-up` (refreshes docs, writes a handover, merges finished
branches, commits only this file), `/pickup`. Use as many agents as you like on a task if it saves time.
