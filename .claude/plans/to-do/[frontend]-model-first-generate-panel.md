# [frontend] model-first-generate-panel

> **SPA phase, guide 1 — the guide that un-breaks the SPA.** Rewritten 2026-07-17 against the
> landed single-executor backend (rework guides 1–3 in `done/`). The backend has spoken
> executors + `standard`/`high` since `456a72f`…`18ed4d9`; the SPA still speaks grade/alias
> everywhere and is knowingly broken. Pure frontend — no backend rows here.
>
> **Backlog plan.** Full code + red tests (vitest, `frontend/tests/` — see the
> `frontend-test-layout` memory) at activation.

## Context / goal

The generate panel becomes **executor-first**: pick the machine; mode and model hang off it.

The landed backend contract this guide builds against (verify against code at activation,
not against this file):

- **`GET /api/llm/executors/`** — one request, all rows:
  `{provider, label, self_hosted, configured, reachable, default, models, modes}`.
  - HirschAI row: `provider: "ollama"`, `label: "HirschAI"`, `configured: true`, live
    `reachable` (30 s-cached probe), `models: []` (the tower model is operator-fixed),
    `modes: ["standard"]`, `default: true` iff no commercial default row exists.
  - Commercial rows (anthropic / openai): `reachable: null`, `configured`/`default` from the
    user's `LLMConfig` rows, `models` = catalog `[{id, label, default?}]`,
    `modes: ["standard", "high"]`.
- **`POST /api/jac/generations/`** takes `job_application`, `mode`, `provider`, `model`
  (+ CV scoping). Blanks resolve server-side (mode → `standard`, provider → the user's
  default executor, model → catalog default; HirschAI ignores a client-sent `model`). 400s,
  shaped `{"provider": [msg]}` / `{"mode": [msg]}`: `manual` mode; `high` on HirschAI;
  unconfigured provider; unknown model (the catalog **is** the gate — a rework decision, no
  free-text escape); nothing available (HirschAI offline + no commercial row).
- **Auto-run is backend-side** (`JobApplicationViewSet.perform_create`): creating an
  application spawns a `standard` run on the user's default executor when one exists, never
  retroactively. The SPA **must not** POST a run at create anymore.
- Run read shapes / result meta carry `mode` + `provider` + `model`; `grade`/`alias` are
  gone. Result CV rows additionally carry `pinned` + `warning` (rendering belongs to
  `[frontend]-entry-pins-ui`; type them here so the shapes compile).

## Affected files

| Path | Change |
| --- | --- |
| `lib/queries/llm.ts` | Delete the alias/strength/pin vocabulary (`AliasStrength`, `aliasesForGrade`, `pinnedAliasFor`, `useLLMAliases`, pin queries); add `useExecutors()` on `/api/llm/executors/` (refetch on window focus + interval so the panel notices HirschAI coming up/going down). Config-tab types move with `[frontend]-llm-config-tab-v2`. |
| `lib/queries/generations.ts` | `Grade` → `Mode` (`"manual" \| "standard" \| "high"`); payload `{mode, provider, model}`; `meta.grade`/`meta.alias` reads → `meta.mode`/`provider`/`model`; result-row types gain `pinned`/`warning`. |
| `lib/queries/{jac,applications}.ts` | Run-summary types follow (`grade` → `mode` + `provider`/`model`). |
| `components/applications/generate-panel.tsx` | Rebuild: executor picker (disable-don't-hide, reasons inline), per-pick options (HirschAI = deliberately bare; commercial = model dropdown + mode toggle), offline as a first-class state, submit → the new payload. |
| `routes/_authenticated/applications/index.tsx` | Delete the create-time `grade: "light"` run POST — creation alone triggers the backend auto-run; navigate to the detail page, which picks the run up. |
| `components/applications/result-view.tsx` + `use-run-lifecycle.ts` | Badge reads `meta.mode/provider/model`; confirm the lifecycle hook seeds from a run the SPA didn't POST itself (it rehydrates from the runs list, so likely free — verify, don't assume). |

## Approach / key decisions

- **The endpoint is the single source.** The picker renders exactly what `useExecutors()`
  returns, labels included — `"HirschAI"` comes from the API's `label` field, so the SPA
  needs **no branding constant**. ("Dr. Jacll" is dead; the executor is named HirschAI,
  decided with the rework.)
- **Disable, don't hide; never yank.** Unavailable rows stay visible with the reason inline
  ("offline", "no API key" — the disabled reasons double as status display). A background
  refetch never flips the user's explicit selection; it disables Generate with the reason.
- **Mode is a per-pick toggle speaking server vocabulary.** Driven by the row's `modes` —
  never hardcoded. HirschAI: no toggle (`standard` only). Commercial: `standard`/`high`,
  default `high`. Copy may explain ("High = holistic selection, compose-licence letter,
  always audited"); API values stay `standard`/`high`.
- **Offline is a state, not an error.** No executor at all → the panel says so and steers to
  "No AI — curate by hand" (`[frontend]-manual-no-run-mode`) — a parallel affordance, never a
  fake picker entry. The create 400 "nothing available" maps to this same state plus an
  executors refetch, not an error toast.
- **Auto-run UX.** Create with HirschAI up → the detail page opens onto a pending/running
  `standard` run (existing WS flow streams it). Tower down at create → the application stays
  empty, and stays empty when the tower returns (backend rule, never retro) — the panel
  simply offers Generate.
- **No knobs here.** `[fullstack]-model-knobs` adds effort/temperature + `params`; this
  panel leaves the per-pick options area open but ships without.

## Tests (at activation)

- `frontend/tests/lib/generations.test.ts` — payload builder: `{mode, provider, model}`
  passthrough, blanks omitted; meta/row types compile with `pinned`/`warning`.
- `frontend/tests/lib/executors.test.ts` — endpoint rows → picker rows: disabled+reason
  matrix (HirschAI offline / unkeyed commercial); default-row preselect; never-yank (selected
  row going unavailable keeps the selection, flags submit-disabled); per-pick options
  (HirschAI bare; commercial shows exactly the row's `models` and `modes`).
- `frontend/tests/lib/llm.test.ts` — no alias/strength vocabulary anywhere in the types.

## Verification

`tsc -b` clean; vitest green. Click-through: ollama up → create an application → it fills
itself in without touching Generate (auto-run streams; badge `standard / HirschAI`); stop
ollama → create another → empty application, HirschAI greyed "offline", panel steers to No
AI; restart ollama → the empty application does **not** retro-generate, Generate is offered;
with a configured Anthropic row → model dropdown + mode toggle appear, a `high` run's badge
shows provider + model.

## Results

<!-- Human fills this in. -->
