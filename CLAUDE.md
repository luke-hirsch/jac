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
- **tokens survive a redeploy:** nothing a deploy changes may stop a token or API key from working. A stated,
  one-off revocation in a migration is fine ([[deploys-keep-connections]]).

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

**Live on the lab since 2026-11-08.** Lukas's first glance: everything checks out. Five guides are left in
`.claude/plans/to-do/`, all of them his:
- `03_[fullstack]-live-checklist`: the 24 live checks (the launch guide's open ones and guide 02's live curls folded in;
  the rollback drill on `57ee607`, never across `jac.0006`) and the watch list;
- `04_[fullstack]-after-launch`: the backlog, in a recommended order. It starts with what the archived guides left open
  (guide 30's about page and comments first, then guide 18's and 19's live tests, four legal sentences, token scope,
  his own data fixes, three small bugs), then what waits on the lab or the board. Each item gets an activation pass
  before Lukas types it ([[backlog-guides-need-activation]]);
- `31` to `33`, the portfolio's next chain, activated with red tests on their branches, typed in that order
  ([[portfolio-guides-30-33]]): one stylesheet (`frontend/src/theme.css`, shadcn everywhere, the Studio pieces gone)
  with the page owner's CV colour as primary; a `contact` block, a form that mails the owner instead of a public
  address; and the decision model `d3:4b` keeping or dropping each entry over a WebSocket, so the page fills while the
  visitor watches. 33 starts with checks on the tower.

Guides 02, 05 to 07 and 09 to 29 (AI-coded at his request, 2026-11-20 to 11-25) are in `done/` without a Results
chapter (2026-11-23): Lukas tests live, in the Claude app, and what breaks comes back as new work. All are
live: `/health/` on every host, each key's live model list, the career and applications as files ([[career-files]]),
the style rules, and the Claude connector at `jac.<domain>/api/jac/mcp/` as the only way Claude reaches jac, its
consent kept per user (`TransferConsent`) ([[claude-skill]]); 13 to 17: skill years from the linked entries, one
skill↔certification link (migration `jac.0006`), less review noise, Keep in the change log, the domain manager; 18:
the PDF preview in the Claude app, an MCP App the connector serves ([[claude-skill]]); 19: every export named
`cv_` / `letter_` / `application_<title>_<full name>` (`exportStem`); 20: the applications list's status filter
(`?status=`, kept into an application); 21 to 23, the portfolio: a "What's this?" page (`portfolio.<domain>/about/`,
`about` a reserved handle), a waiting card for the AI half of a questionnaire visit (ranking + welcome are one queued
job, `portfolio_personalise`, 6/h; the visitor waits or browses the standard page), and the skills as a network around
the jobs and projects that used them (`item.skills` from `_link_skills`, both ends on the page; d3-force); 24: with
certifications off, the CV describes two jobs and two projects and the page fit drops tail entries before it shortens
them (the layout's `detailed_when_off`, resolved by `effectiveDetail`); 25: renaming a file on the attachments page saves
(the endpoint reads JSON too); 26 to 29, the portfolio again: block kinds quote, figures, cta, linklist and media (a
self-hosted upload, sniffed, 50 MB a file and 250 MB a user; `spa.blocks`), the standard page stored as a `standard`
link row (`<home>?focus=balanced&tone=neutral` shows it at once: the AI job starts only for a topic, a question or a
non-default style), the connector's `get_portfolio` / `save_portfolio_block` / `save_portfolio_page` (writes need the
token's `may_edit_portfolio`, off by default; each answers with `before` and the `url`; `rules/portfolio.md`), and the
Studio look on the public portfolio only (`.studio`, Space Grotesk + Inter, the owner's accent made readable on the
dark ground). Parked in guide 04: review dismissals, draft entries from Claude, ChatGPT, click-to-load video embeds.

Guide 30 (typed by Lukas, in `done/` with his Results, 2026-11-25) put a proof-of-work check in the questionnaire
(ALTCHA, `spa/captcha.py`, no cookie, no outside service): nothing anonymous wakes the tower without a solved,
single-use check. The personalise POST and the visitor prewarm carry it; "I feel lucky" and the standard page need
none, since they never reach the model.

The launch guide is archived as `done/[fullstack]-launch-live-2026-11-08.md` (its step 3 records how the mini was set
up), next to the wildcard version and the earlier ones. **The pre-launch mode
("the AI codes at his request, we're at a volatile stage") ran until launch.** Default-strict (below) applies again
until Lukas says otherwise.

- **Branches:** three, each one red-test commit, stacked on `main`: `fullstack/one-stylesheet` →
  `fullstack/portfolio-contact` → `fullstack/portfolio-live-fill` (guides 31 to 33). `main` holds the Postgres move,
  prewarm, the race fix, the legal pages and what they promise, 2b (random handles, the consent gate and card, the
  audit fix, the legal sign-off), the four-host layout, `./dev` serving the landing on plain `localhost` as the apex
  does live, mail over Strato's 465 with SSL, and the privacy page without menu paths, guides 02 and 05 to 30, and the
  favicon (the antlers, [[brand-mark]]). Live was `1eb6d72` (2026-11-25) at the last check; guide 30 is on `main` and
  pushed, and its deploy isn't confirmed. The about page should get guide 30's text first (guide 04). `jac.0006` (every skill's certification link moved into the certification) was already
  applied live when guide 18 deployed.
- **The legal pages** say du and name no infrastructure ([[legal-no-infrastructure]]). They say what happens with the
  data, not where to click: no menu paths, and a test keeps `Account →` off the privacy page. Since 2026-11-07 they say `jac.`
  and `portfolio.<domain>/<name>`. Of what their text commits jac to ([[legal-consent-and-restore]]), consent and the
  deleted-account record are built. The lab confirmed the shortened log address and the addressless stream log
  (2026-11-14); what the VPS's own logs keep is still open (guide 04). The visitor hash waits on the board. The DNS
  stays at Strato, so section 11 no longer names Cloudflare. Guide 07's download makes §13's "a data export on
  request by email" incomplete: the sentence is his call (guide 04).
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
