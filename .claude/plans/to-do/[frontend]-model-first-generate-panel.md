# [frontend] model-first-generate-panel

> **Guide 5** — *LLM-mode redesign*. Depends on **guide 4** (`GET /api/jac/executors/`); renders
> whatever `models`/`knobs` the endpoint advertises once `[fullstack]-llm-model-catalog-and-knobs`
> enriches it (degrades gracefully to none before that). Replaces the `grade` dropdown with a
> **model-first panel**, adds the **auto-run on application create**, and **removes the backend
> compat `grade` bridge** guide 1 added (this is the guide that lets it go).
>
> **Backlog plan.** Full code + red tests (vitest, `frontend/tests/` — see the
> `frontend-test-layout` memory) at activation.

## Context / goal

Redesign UX (Lukas, 2026-07-15): **the model is the primary pick; everything else hangs off it.**

1. **Model picker first.** The list = **Dr. Jacll** (the free self-hosted executor) + every
   commercial alias the user configured. Unavailable entries are disabled with the reason inline,
   never hidden.
2. **Options appear per pick.** Dr. Jacll → deliberately bare: strategy is fixed to `instruct`
   (the endpoint advertises only that) and no knobs — "maybe temperature? but maybe not. maybe
   that's just what it is." A commercial pick → a **model dropdown** (catalog + free-text escape),
   the **strategy toggle**, and the provider's **knobs** (effort / temperature).
3. **Generate** POSTs `mode` (strategy), `alias` (executor), and `params` (model/knobs — catalog
   guide). Or the user takes the parallel **"No AI — curate by hand"** affordance (guide 6) — a
   separate action, *not* a fake entry in the model list.

Plus the redesign's headline UX: **creating an application fills itself in when Dr. Jacll
answers** — the SPA auto-enqueues a free `instruct` run on create; when he's down, nothing runs
and the page shows why.

### Naming (all Lukas, 2026-07-15)

- The free executor's public name is **"Dr. Jacll"** (Jekyll, via jac). Rejected names, each for a
  reason worth remembering: *auto* (suggests a model is being picked automatically), *default*
  (the auto-draft role may later be reassignable to a configured commercial model — don't weld the
  name to the role), *tower* (no "runs on an office machine" impression). It's a **frontend
  branding constant** keyed off the internal alias `default` — the API stays branding-free, and
  guide 7's chat picker reuses the same constant.
- The strategies need **public labels** — nobody outside this repo knows what
  "instruct"/"conversational" stand for. Recommendation to react to: **instruct = "Quick
  tailor"**, **conversational = "Deep tailor"** (speed vs. depth is the honest user-facing
  difference). API values stay `instruct`/`conversational`; this is copy, Lukas has final word.

## Affected files

| Path | Change |
| --- | --- |
| `frontend/src/lib/queries/generations.ts` | Payload `grade` → `mode` + `alias` + optional `params`; `Grade` type → `Mode` (`"manual" \| "instruct" \| "conversational"`); `meta.grade` reads → `meta.mode` (+ executor/model for the badge). |
| `frontend/src/lib/queries/llm.ts` | A `useExecutors()` query hitting `/api/jac/executors/`; `aliasesForGrade`/`pinnedAliasFor`/`AliasStrength` → executor/mode-keyed; the per-alias `strength` display **dies here** (guide 4 rekeyed the pins API; final backend autodetect deletion is guide 7's). |
| `frontend/src/components/applications/generate-panel.tsx` | The model-first layout above: picker with disabled-with-reason entries; per-pick options; offline banner; the **token-generosity hint** on paid executors ("side tasks run on the free local model to save your tokens — change?"); the "No AI" affordance routing to guide 6. Result badge shows executor + strategy (+ guide 3's `meta.prefilter` when `"full"` on a paid alias — "sent the whole career DB"). Maps the create 409 `llm_unreachable` to the offline state. |
| `frontend/src/routes/_authenticated/applications/index.tsx` | The hardcoded `grade: "light"` seed becomes the **auto-run**: after a successful application create, if `useExecutors()` returns a non-null `default` → POST an `instruct` run on it; else no POST — land on the detail page in its offline/manual state. |
| `frontend/src/lib/queries/{jac,applications}.ts` | Run summary types `grade` → `mode`. |
| **Backend** `jac/serializers.py` | **Remove** the compat `grade` `SerializerMethodField` from the read serializers, the legacy-`grade` acceptance in the create `validate()`, and guide 2's file-local mode→grade literal dict (`# compat: dies with guide 5`) — the SPA now speaks `mode`. |
| **Backend** `jac/models.py` | **Delete** `GRADE_TO_MODE`, `KNOWN_MODE_INPUTS`, and the legacy-grade branch in `normalize_mode` (keep its blank/unknown→`instruct` coercion — that's input tolerance, not the bridge). After this guide the word "grade" survives only in the connector's strength machinery, which guide 7 deletes (see guide 1's compat ledger). |

## Approach / key decisions

- **`useExecutors()` is the gate.** One query, cached, refetched on an interval / window focus so
  the UI notices the tower coming up. The panel renders the picker from it; never hardcodes the
  list.
- **Auto-run on create, never retro.** The auto-run fires exactly **once**, at application create,
  and only when the executors query returns a non-null `default` (by guide 4's rules that is
  always the free executor — paid never runs unasked). If the tower is down at create, the
  application stays empty and *stays* empty when the tower later returns — no background
  generation the user didn't watch start ("no one likes surprises"); the panel simply offers
  Generate again. If the create-time POST races the tower dying, the 409 lands in the same offline
  state — the auto-run failing to start is never an error toast, just the offline panel.
- **Show every executor always; disable, don't hide.** A vanishing option reads as a bug and makes
  the panel unpredictable across visits. Dr. Jacll offline is greyed with "offline — pick your own
  model or curate by hand"; an unkeyed config is greyed with "no API key". The disabled reasons
  double as the status display.
- **Never yank an explicit selection.** The dynamic default applies only until the user touches
  the picker. If a background refetch makes the *selected* executor unavailable, keep the
  selection, disable Generate, and show the reason inline — don't silently flip the dropdown under
  the user's cursor mid-click.
- **Offline is a first-class state, not an error.** When Dr. Jacll is unavailable, the panel says
  so and steers to "No AI" (always available) or a configured commercial executor — it does not
  present a dead Generate button.
- **Handle the enqueue 409.** Guide 4's `llm_unreachable` fail-fast fires exactly when the poll is
  stale (tower died between refetches) — map it to the same offline state and trigger an executors
  refetch, not a generic error toast: the user should see *why*, not "request failed".
- **Strategy defaults are per-executor**, read from the endpoint's `strategies` order: Dr. Jacll
  has no toggle at all (instruct only); a commercial pick defaults to `conversational` with
  `instruct` selectable for experimentation.
- **Bridge removal is part of this guide.** Only here, once the SPA sends/reads `mode`, is it safe
  to delete the backend `grade` compat. Do it in the same branch so `main` never has an SPA on
  `mode` talking to a server still requiring `grade`. One deploy-order caveat: a browser still
  holding the *old* cached bundle sends `grade`, which the bridge-less server now ignores — the run
  silently becomes `instruct` on the default alias (free, so no cost surprise). Single-operator
  today, so "hard-refresh after deploy" is the whole mitigation; with real users the asset hash
  makes it moot on next load.

## Tests (written at activation)

- `frontend/tests/lib/generations.test.ts` — payload builder emits `mode` + `alias` (+ `params`
  passthrough), omits blanks; the 409 `llm_unreachable` response maps to the offline state, not a
  generic error.
- `frontend/tests/lib/llm.test.ts` — executor/mode-keyed helpers; no strength anywhere in the
  types.
- `frontend/tests/lib/executors.test.ts` — endpoint result → picker rows (disabled + reason for
  offline-free / unkeyed-paid); the **auto-run decision** pure helper (`shouldAutoRun` true iff
  `default` is non-null — never true for a paid-only setup); never-yank: the selected executor
  going unavailable keeps the selection and flags submit-disabled; per-pick options visibility
  (Dr. Jacll bare; commercial shows the strategy toggle + exactly the advertised knobs).
- Backend: read serializer no longer emits `grade`; create ignores a stray `grade` key (unknown →
  `instruct`, the guide-1 coercion now catches it); absence assertions for
  `GRADE_TO_MODE`/`KNOWN_MODE_INPUTS` on `jac.models` (scaffolding — delete after merge, like
  guide 2's).

## Verification

`tsc -b` clean; vitest green; click-through: **create an application with ollama up → it fills
itself in without touching Generate**; stop ollama, create another → nothing runs, Dr. Jacll is
greyed "offline", the panel steers to No AI; start ollama again → the empty application does *not*
retro-generate, but Generate is offered; pick a commercial executor → model dropdown, strategy
labels, and knobs appear, plus the token-generosity hint; a generated run's badge shows executor +
strategy.

## Results

<!-- Human fills this in. -->
