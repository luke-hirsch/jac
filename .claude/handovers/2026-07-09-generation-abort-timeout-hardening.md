# 2026-07-09 — generation abort/timeout hardening (+ the "stuck loop" diagnosis)

## Goal

Two complaints from live use: (1) a default-model generation "timed out" and the app got stuck in an
endless *Generating…* loop; (2) timeouts must be abortable and visible to the user ("retrying in Ns",
"check connection"). Lukas said "fix it all" — explicit volatile phase, AI wrote the source directly.

## Where it stands

**Root cause of the original incident: nothing timed out — no Celery worker was running.** Run #1
sat `pending` forever (task still queued in Valkey), and the UI faithfully rendered that dead state:
Generate button disabled, no staleness detection, no abort. The default light pipeline itself is
fast (3 LLM round-trips; probed ~2–3 s each cold) — no pipeline changes were needed for issue 1.

Everything below is **implemented, committed on `main`, all suites green**
(backend 441, frontend 101, tsc, eslint clean):

- `backend/jac/views.py` — `POST /api/jac/generations/<pk>/cancel/` (revoke + mark failed
  `"cancelled by user"` + publish `failed`; idempotent; survives a dead broker). Create now uses
  `apply_async(expires=900)` and stores `task_id` (new field, migration `0008`).
- `backend/jac/tasks.py` — task claims only `pending` runs and writes terminal states only from
  `running` (cancel always wins, stale deliveries are no-ops); `soft_time_limit=1500` → clean
  "generation timed out" error; `LLMTransportError` → "could not reach the language model … check
  that the model server is running".
- `backend/llm_connector/` — `LLMTransportError` (base + ollama/custom URLError paths);
  `LLMClient` retries transport failures once; `retry_reporter(cb)` contextvar lets the task
  publish "LLM <op> failed — retrying in Ns" progress events.
- `frontend` — `ws.ts` auto-reconnects with backoff (1→2→4→8→15 s; never on 1000/4401/4404) and
  reports socket status; `$applicationId.tsx` gets an Abort button, elapsed-seconds ticker,
  stale-queue hint (pending >30 s ⇒ "worker may be down"), connection-lost notice;
  `generations.ts` gains `useCancelGeneration` + `isStalePending`/`pendingAgeSeconds`.
- `backend/lukehirsch/settings.py` — **channel layer switched core → pubsub.** The core layer +
  redis-py 8 drops every WS idle >5 s; this fix was documented in memory long ago but had never
  been typed into settings. Guard comment added.
- `README.md` — "Run (dev)" now lists all four processes; the worker
  (`celery -A lukehirsch worker -l info`) is the one whose absence caused the incident.

**Not done / left as-is:** live end-to-end verification (Lukas's side, below). No separate
`cancelled` run status (kept the 4-status WS contract; a cancel reads as
`failed`/`"cancelled by user"`).

## Decisions + why

- **Cancel reuses `failed`** instead of adding a `cancelled` status — keeps the WS/REST contract
  and frontend types stable; the error string carries the semantics.
- **Claim/terminal writes are conditional queryset updates** (`pending→running`, `running→done|failed`)
  rather than locks — race-safe enough for one user, zero infra.
- **Retry only `LLMTransportError`** (URLError paths), never HTTP errors — avoids double-billing
  commercial calls on 4xx/5xx; one retry, 2 s, reporter hook instead of threading callbacks
  through the prompt classes.
- **`expires=15 min` on enqueue** — a task queued while the worker is down must not fire hours
  later; the stale-pending UI + cancel covers the leftover run row.
- **Stale-pending threshold 30 s** (frontend only) — light runs finish in seconds, so 30 s of
  `pending` almost certainly means "no worker".

## Open threads / risks

- **Live verification pending** (the volatile-phase deal: testing stays with Lukas):
  1. start the worker — the *old* queued task for run #1 will fire immediately and should complete
     it (or hit Abort first; the claim guard then skips it);
  2. a fresh default run end-to-end (progress → done, result renders);
  3. Abort mid-run; 4. kill the worker, start a run → stale hint at ~30 s → Abort;
  5. restart daphne mid-run → "connection lost — retrying" then reconnect + snapshot.
- **`CoverLetterWriter` refusal gap (found while probing):** llama3.2:1b spuriously *refused* a
  benign weave prompt; `write()` only falls back on *empty* responses, so refusal prose can become
  the letter body. Noted in CLAUDE.md roadmap notes — worth a small guide (refusal heuristic →
  treat as weave failure → raw-stitch fallback).
- `revoke(terminate=True)` needs the prefork pool (dev default) — on solo/threads pools a
  *running* task won't die, but the terminal-write guard still discards its result.
- The last commit before this session ("resume snippets frontend ui") is **unpushed**, as is all of
  today's work — push when ready.
- `[frontend]-cv-snippets.md` still sits in `to-do/` while parts of it appear implemented —
  reconcile guide vs code before continuing it (now roadmap #1).

## Next action

Run the live checklist above — concretely: `brew services start valkey` (already up), start Ollama,
`cd backend && python manage.py runserver` + `celery -A lukehirsch worker -l info`, open the
existing application detail page, and watch run #1 resolve; then exercise Abort + the stale hint.
