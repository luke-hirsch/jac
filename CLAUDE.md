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
- **hosting:** self-hosted on the lab, live on the mini since 2026-11-08. One image, generic `compose.yaml`; only
  `deploy/compose.lab.yaml` knows the lab. It must also run on a plain rented VPS ([[normal-infra-test]]).
- **hosts:** four fixed names under `BASE_DOMAIN`, **no wildcard** (decided 2026-11-07):
  - the apex is Django's server-rendered landing (SEO), whose "Explore my work" goes to
    `portfolio.<domain>/<owner>/`; any other apex path 302s to the portfolio host, and `www.` 301s to the apex;
  - `jac.` is the app (it was `app.`);
  - `portfolio.<domain>/<handle>/` and `/<handle>/<slug>/` are every user's portfolio and links.

  The owner comes from the URL path (`/api/spa/public/<handle>/…`), never the Host header. Don't bring back per-user
  subdomains: Lukas dropped them because the wildcard's tail on the lab (DNS-01, Caddy's Cloudflare module) wasn't
  worth it ([[portfolio-multiuser]]).
- **deploys keep connections:** jac is live, so no migration deletes or invalidates what a user holds (tokens,
  API keys, sessions). A new requirement is checked when they're used, or backfilled honestly
  ([[deploys-keep-connections]]). Guide 10's second round broke this once (`jac` 0004 deleted every token).

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

**Live on the lab since 2026-11-08.** Lukas's first glance: everything checks out. What's left sits in nine guides in
`.claude/plans/to-do/`. All of it is his, and none of it is urgent:
- `02_[infra]-health-every-host`: `/health/` on `jac.` and `portfolio.` reaches Django now (merged, live). His curls
  against the live names and the Results are left;
- `03_[fullstack]-live-checklist`: the 24 live checks (the launch guide's open ones folded in, the rollback drill rides
  on guide 02's deploy) and the watch list;
- `04_[fullstack]-after-launch`: the backlog, in a recommended order, and what waits on the lab or the board. Each item
  gets an activation pass before Lukas types it ([[backlog-guides-need-activation]]);
- `05` to `10`: coded by the AI at his request (2026-11-20, 09 and 10 on 2026-11-21), summary guides, all merged into
  `main` and live, his to test on live and log:
  - 05 `live-model-catalog`: each key's live model list + the fix for effort on current Claude models;
  - 06 `claude-skill`: write applications with Claude on his own subscription, and have Claude review and improve
    the career data, every batch of edits undoable ([[claude-skill]]);
  - 07 `career-export`: the career and the applications as files, a download and an import each; an import fills
    only an empty career or list ([[career-files]]);
  - 08 `skill-file-mode`: the Claude app can't reach jac on his plan, so the skill works from files there (a static
    zip, a career file per chat, Claude's results imported in jac). It supersedes 06's Claude-app half; the token
    stays, for Claude Code only.
  - 09 `career-change-field-names` (deployed by the AI at his request): the changes import 500d on a name `career`
    showed but the import doesn't take (`type` for `job_type`); names are checked first now, as a 400. The skill
    zip in the Claude app predates it: re-upload, then his live test.
  - 10 `claude-connector` (deployed by the AI at his request): the Claude app, phone included, reaches jac through a
    custom MCP connector at `jac.<domain>/api/jac/mcp/` (Anthropic's servers call it, not the sandbox), on the skill's
    token; `get_career` hands over SKILL.md as the rules. File mode stays as the fallback. Its second round put Claude
    under Account → LLM → Claude behind the same consent card as an API key; the third round keeps that consent per
    user like a cookie choice (`TransferConsent`: asked once, withdrawing deletes the keys or tokens; privacy §6, §8
    and §11 and terms §6 cover it). His steps: a token, the connector in Claude's
    settings, the couch test ([[claude-skill]]).

The launch guide is archived as `done/[fullstack]-launch-live-2026-11-08.md` (its step 3 records how the mini was set
up), next to the wildcard version and the earlier ones. **The pre-launch mode
("the AI codes at his request, we're at a volatile stage") ran until launch.** Default-strict (below) applies again
until Lukas says otherwise.

- **Branches:** none open. `main` holds the Postgres move, prewarm, the race fix, the legal pages and what they
  promise, 2b (random handles, the consent gate and card, the audit fix, the legal sign-off), the four-host layout,
  `./dev` serving the landing on plain `localhost` as the apex does live, mail over Strato's 465 with SSL, and
  guides 02 and 05 to 10, all deployed (2026-11-21).
- **The legal pages** say du and name no infrastructure ([[legal-no-infrastructure]]). Since 2026-11-07 they say `jac.`
  and `portfolio.<domain>/<name>`. Of what their text commits jac to ([[legal-consent-and-restore]]), consent and the
  deleted-account record are built. The lab confirmed the shortened log address and the addressless stream log
  (2026-11-14); what the VPS's own logs keep is still open (guide 04). The visitor hash waits on the board. The DNS
  stays at Strato, so section 11 no longer names Cloudflare. Guide 07's download makes §13's "a data export on
  request by email" out of date: the sentence is his call (guide 07).
- **Live isn't verified yet:** Lukas has given it a first glance, but the checklist hasn't run. Guide 03 is the logged
  run ([[implemented-is-not-verified]]).
- **The lab:**
  - labd, the tower's wake proxy, runs (server guide 18), **but live jac doesn't go through it** (found 2026-11-16):
    the mini's `HIRSCHAI_URL` points at the tower's Ollama directly, so nothing wakes the tower and a run waits its
    15 minutes for someone to switch it on. The fix is that `.env` line (guide 03, check 4).
  - Prewarming is built. It's live only if `HIRSCHAI_PREWARM_KEY` is in the mini's `.env`, and it isn't
    (2026-11-16; guide 03, check 15).
  - There's no nightly backup yet, only the deploy dumps on the mini's disk. Planned: server guide 20, 90 days.
  - The public route for the four names (server guide 19: DNS at Strato, a certificate per name) is closed, green on
    the real machines since 2026-11-14. The deploy and the board's down-check reach `/health/` over the default
    server (`jac-web:8000`). **lab-watch** on the VPS (server guide 22) asks the apex's `/health/` every minute and
    pages Lukas after 3 minutes without 200 + `X-Release`, so neither may move.
  - The mini restarts itself for macOS updates, and jac comes back only after a login as mini (`stacks-up` honours the
    override since 2026-11-14).
  - The board's exports belong in `config/` (latest: `-8`): the repo root isn't ignored, and they name lab addresses.
  - A one-shot `ssh -t mini '…'` finds Docker, so guides can give mini commands as laptop one-liners.
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
