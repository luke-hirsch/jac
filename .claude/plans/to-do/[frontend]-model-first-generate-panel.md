# [frontend] model-first-generate-panel

> **SPA phase, guide 1 — the guide that un-breaks the SPA.** Activated 2026-07-17: the
> earlier version was a backlog stub (contract bullets only); this version is the
> implementation guide — contracts verified against the landed backend, per-file target
> states, and **red tests on disk** (`frontend/tests/lib/…`, see "Tests"). Pure frontend —
> no backend rows here (the two backend leftovers the rework missed are guide 2's, see
> `[fullstack]-llm-config-tab-v2`).

## Context / goal

The generate panel becomes **executor-first**: pick the machine; mode and model hang off it.
Everything alias/grade-shaped dies in the SPA. The blast radius is bigger than the panel —
`useLLMAliases` / pins / `find_address` feed six components; all of them must compile (and
work, on the default executor) when this guide lands.

## The landed backend contract (verified against code 2026-07-17)

### `GET /api/llm/executors/` — `llm_connector/views.py:55`

One request, all rows. Exact row shape (note: `models` is a list of **objects**, not ids):

```json
{
  "provider": "anthropic",
  "label": "Anthropic",
  "self_hosted": false,
  "configured": true,
  "reachable": null,
  "default": true,
  "models": [
    { "id": "claude-sonnet-5", "label": "Claude Sonnet 5", "default": true },
    { "id": "claude-opus-4-8", "label": "Claude Opus 4.8" }
  ],
  "modes": ["standard", "high"]
}
```

- **HirschAI row** (always first): `provider: "ollama"`, `label: "HirschAI"`,
  `self_hosted: true`, `configured: true`, `reachable: true|false` (live probe,
  **30 s server-side cache** — `llm_connector/probe.py`), `models: []` (tower model is
  operator-fixed), `modes: ["standard"]`, `default: true` iff the user has no commercial
  default row with a stored key.
- **Commercial rows** (one per `CATALOG` provider — anthropic, openai): `reachable: null`
  (never probed), `configured` = row exists **and** has a key, `default` = row's flag and
  keyed, `models` from `llm_connector/catalog.py`, `modes: ["standard", "high"]`.
- Not paginated — a plain JSON array.

### `POST /api/jac/generations/` — `jac/serializers.py:486`

Writable fields: `job_application`, `mode`, `provider`, `model`, plus CV scoping
(`domains`, `started`, `ended`, `min_skill_proficiency` — the panel doesn't send these
today; leave them out). **`verify_grounding` and `personal_paragraph` are gone** — audits
are always on, the personal paragraph is capability-driven. Blanks resolve server-side
(`llm_connector/conf.py resolve_executor`): mode → `standard`, provider → the user's
default executor, model → catalog default; HirschAI ignores a client-sent model.

400s come DRF-shaped — `{"mode": [msg]}` or `{"provider": [msg]}` — with these exact
server messages (pin copy to them, don't paraphrase):

| trigger | field | message |
| --- | --- | --- |
| `mode: "manual"` | `mode` | `manual never runs a generation — curate the application directly.` |
| `high` + HirschAI | `mode` | `high mode needs a commercial executor — HirschAI runs standard.` |
| blank provider, nothing usable | `provider` | `No executor available — HirschAI is offline and no provider is configured.` |
| unknown provider | `provider` | `Unknown provider '…'.` |
| provider without key | `provider` | `No 'anthropic' API key configured.` |
| model not in catalog | `provider` | `Unknown model '…' for '…'.` (the catalog **is** the gate) |

### Run read shapes

- `GenerationRunSerializer` (`jac/serializers.py:539`): `id, job_application, status,
  stage, error, result, mode, provider, model, posting_title, created_at, updated_at`.
  **Gone:** `grade`, `alias`, `verify_grounding`, `personal_paragraph`, `evaluation`,
  `score`.
- `GenerationRunSummarySerializer` (`:563`, nested as `application.runs`): `id, status,
  stage, mode, provider, model, created_at`. Ordering `-created_at` → `runs[0]` is newest.
- `result.meta` (`jac/tasks.py:191`) = `{mode, provider, model}` — **`provider` is the raw
  key** (`"ollama"`), the SPA maps it to a label via the executors rows. HirschAI runs have
  `model: ""`.
- Result CV rows (`jac/generation_result.py:61`):
  `{id, label, relevance_score, pinned, warning}` — type `pinned`/`warning` here so the
  shapes compile; *rendering* them belongs to `[frontend]-entry-pins-ui`.

### Auto-run is backend-side

`JobApplicationViewSet.perform_create` (`jac/views.py:373`) spawns a `standard` run on the
user's default executor when one is available — never retroactively. The SPA **must not**
POST a run at create anymore.

### Dead and changed endpoints (the wider blast radius)

- **Dead:** `GET /api/llm/aliases/`, `GET/PUT /api/llm/pins/`,
  `POST /api/jac/applications/<pk>/find_address/` (address web-search is deprecated — the
  address comes from the posting-text extract in the pipeline).
- **Changed:** `…/rewrite/` and `…/chat/` (`jac/views.py:390` / `:447`) now take optional
  `provider`/`model` instead of `alias`; blank = the user's default executor; 400
  `{"provider": [msg]}` on `ExecutorError`. The chat strength gate died — any executor
  chats.
- **Broken until guide 2 (accepted window, do not fix here):** `/api/llm/configs/` (the
  serializer still lists dead columns and crashes) and `/api/spa/personality/rebuild/`
  (calls `ensure_dossier(alias=…)` against the new `(executor)` signature). Both repairs
  are specced in `[fullstack]-llm-config-tab-v2`.

## Where you are (WIP triage, 2026-07-17)

The pre-activation working-tree edits (`llm.ts`, `backend/llm_connector/serializers.py`)
have been reset to HEAD — start clean from the zones below. For the record: the `llm.ts`
edit had merged an executor row type **into** `PROVIDER_SPECS` (two different things: the
executors *endpoint row* vs the config tab's *form mask spec*); the serializer edit was
guide 2's backend repair, and the full target lives there now.

## `lib/queries/llm.ts` — three zones

The file splits into three zones. Work them exactly; don't blend.

**Zone A — new: executors.** Add this section (types + pure helpers + hook). This is what
`tests/lib/executors.test.ts` pins:

```ts
/* ---------- executors (generate panel + every future model picker) ---------- */

export type Mode = "manual" | "standard" | "high";

export type CatalogModel = { id: string; label: string; default?: boolean };

/** One row of GET /api/llm/executors/ — a machine as the backend resolves it. */
export type ExecutorRow = {
  provider: string;
  label: string;
  self_hosted: boolean;
  configured: boolean;
  /** HirschAI: live probe (30 s server cache). Commercial rows: null (never probed). */
  reachable: boolean | null;
  default: boolean;
  models: CatalogModel[];
  modes: Mode[];
};

/** Why a row can't run right now, or null when it can. The terse reason doubles
 *  as the inline status copy — disabled rows stay visible, never hidden. */
export function executorDisabledReason(row: ExecutorRow): string | null {
  if (row.self_hosted) return row.reachable === false ? "offline" : null;
  return row.configured ? null : "no API key";
}

/** The row the picker preselects: the backend's default when usable, else the
 *  first usable row, else null — the panel's offline state. */
export function defaultExecutorRow(rows: ExecutorRow[]): ExecutorRow | null {
  const usable = rows.filter((r) => executorDisabledReason(r) === null);
  return usable.find((r) => r.default) ?? usable[0] ?? null;
}

/** The model a fresh pick starts on: catalog default, else first, else "" —
 *  HirschAI has no models; the tower model is fixed server-side and never sent. */
export function defaultModelFor(row: ExecutorRow): string {
  return row.models.find((m) => m.default)?.id ?? row.models[0]?.id ?? "";
}

/** The mode a fresh pick starts on: high when the row offers it (commercial),
 *  else standard. Never hardcode the mode list — render row.modes. */
export function defaultModeFor(row: ExecutorRow): Mode {
  return row.modes.includes("high") ? "high" : "standard";
}

/** Label for a raw provider key — result meta carries "ollama", the user knows
 *  it as HirschAI. Falls back to the key itself for rows that vanished. */
export function providerLabel(rows: ExecutorRow[], provider: string): string {
  return rows.find((r) => r.provider === provider)?.label ?? provider;
}

export function useExecutors() {
  return useQuery({
    queryKey: ["llm", "executors"],
    queryFn: () => api<ExecutorRow[]>("/api/llm/executors/"),
    // The panel must notice HirschAI coming up / going down. The server probe is
    // cached 30 s — poll at that cadence and on refocus; faster buys nothing.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}
```

**Zone B — frozen: the config-tab block.** `Provider`, `LLMConfigRow`, `ProviderSpec`,
`PROVIDER_SPECS`, `ConfigFormState`/`ConfigPayload`, `buildStructuredExtra`,
`parseExtraJson`, `toPayload`, `rowToState`, `switchProvider`, `CheckResult`,
`checkResultLabel`, `useLLMConfigs`, `useCreateConfig`/`useUpdateConfig`/
`useDeleteConfig`/`useCheckConfig` — **leave byte-identical to HEAD.** The account tab
(`routes/_authenticated/account/llm.tsx`) still imports all of it; it's knowingly broken
at runtime (the backend config API crashes until guide 2's repair) but it must keep
compiling. Guide 2 rewrites this whole zone against the five-field config API. The itch to
collapse it now = guide 2 pulled forward; if you do that, do it as guide 2, on its own
commit, not blended into this one.

**Zone C — delete: alias/pins/address-search.** `AliasStrength`, `AliasInfo`,
`FREE_PROVIDERS`, `isFreeProvider`, `aliasesForGrade`, `GradePins`, `pinnedAliasFor`,
`SEARCH_BRANDS`, `addressSearchOptions`, `useLLMAliases`, `useGradePins`,
`useSetGradePin`. Their endpoints are dead; every consumer is converted below.

## Affected files (honest blast radius)

The old table under-counted; alias vocabulary feeds six components. `result-view.tsx`
from the previous revision **does not exist** — the badge block lives in
`generate-panel.tsx` (the `result && ai && grounding` JSX).

| Path | Change |
| --- | --- |
| `lib/queries/llm.ts` | Three zones above. |
| `lib/queries/generations.ts` | New types + payload builder (code below); badges/reducer/stale helpers untouched. |
| `lib/queries/applications.ts` | `RunSummary` → `{id, status, stage, mode, provider, model, created_at}`; `useRewriteParagraph` body → `{text, instruction}` (no alias — server resolves the default executor); **delete** `useFindAddress` + `FoundAddress`. |
| `lib/letter-chat.ts` | Mechanical de-alias: delete `chatAliases` + `preferredRefineAlias` (the server gate died); `chatPayload(body, messages)` → `{body, messages}`. Keep `REWRITE_STYLES`, `seedDiscussion`, transcript helpers. |
| `lib/queries/personality.ts` | `useRebuildDossier`: plain `POST …rebuild/` (no `?alias=`); `personalityHint(capable, row)` — rename the first param, semantics now "the picked executor can produce a real paragraph". |
| `routes/_authenticated/applications/index.tsx` | Delete the create-time run POST (`useCreateGeneration` import, `createRun`, the mutate block + its warning toast). Create → navigate; the backend auto-runs. |
| `components/applications/generate-panel.tsx` | The rebuild — spec below. |
| `components/applications/letter-editor.tsx` | Drop `useLLMAliases`, `addressSearchOptions`, the "Search with …" buttons + `onFindAddress`; drop the `rewriteAlias`/`canDiscuss` plumbing (rewrite/chat ride the default executor; render the chat unconditionally). Guide 6 gives chat its executor picker back. |
| `components/applications/rewrite-popover.tsx` | Drop the `aliases`/`alias`/`onAlias` props and the model `Select`. |
| `components/applications/refine-chat.tsx` | Drop `useLLMAliases` + `preferredRefineAlias` + the picker; always render; send `chatPayload(body, toApi(next))`. |
| `routes/_authenticated/account/personality.tsx` | Drop the rebuild model picker (`useLLMAliases` + `Select`); `rebuild.mutate()` plain. Note: the endpoint 500s until guide 2's backend repair — accepted window. |
| `routes/_authenticated/account/llm.tsx` | **Untouched** (zone B keeps it compiling). |
| `use-run-lifecycle.ts` / `$applicationId.tsx` | **No change — verified:** the route seeds `runId = selectedRunId ?? app.data?.runs[0]?.id` and the hook rehydrates by pk, so a backend auto-run the SPA never POSTed is picked up for free (`runs` ordering is `-created_at`). |

## `lib/queries/generations.ts` — target

```ts
import type { Mode } from "./llm"; // single definition; no cycle (llm.ts doesn't import from here)

export type RunStatus = "pending" | "running" | "done" | "failed";

export type RunMeta = { mode: string; provider: string; model: string };

export type CvEntry = {
  id: string;
  label: string;
  relevance_score: number | null;
  deselected?: boolean;
  /** Entry pin: force-kept by every rung; survives applying a new run. */
  pinned?: boolean;
  /** Selection warning from the run — rendered by [frontend]-entry-pins-ui. */
  warning?: string;
};

export type TailoredResult = {
  meta: RunMeta;
  cv: Record<string, CvEntry[]>;
  cover_letter: CoverLetterResult; // unchanged
};

export type GenerationRun = {
  id: number;
  job_application: number;
  status: RunStatus;
  stage: string;
  error: string;
  result: TailoredResult | null;
  mode: string;
  provider: string;
  model: string;
  posting_title: string;
  created_at: string;
  updated_at: string;
};

export type GenerationForm = {
  job_application: number;
  mode: Exclude<Mode, "manual"> | ""; // "" = server default (standard); manual 400s
  provider: string; // "" = the user's default executor
  model: string;    // "" = catalog default; always "" for HirschAI
};

export type GenerationPayload = {
  job_application: number;
  mode?: string;
  provider?: string;
  model?: string;
};

/** Blanks are omitted, not sent — the server owns every default. */
export function toPayload(f: GenerationForm): GenerationPayload {
  const p: GenerationPayload = { job_application: f.job_application };
  if (f.mode) p.mode = f.mode;
  if (f.provider) p.provider = f.provider;
  if (f.model) p.model = f.model;
  return p;
}
```

`Grade` dies. Badges (`aiShareBadge`/`groundingBadge`/`qualityBadge`), `WsEvent`,
`RunState`, `runReducer`, `pendingAgeSeconds`, `isStalePending`, and the three hooks are
untouched (the create hook's body type follows `GenerationForm`).

## `generate-panel.tsx` — the rebuild

What survives verbatim: the running/stale/socket banners, Abort, Apply + the applied
wiring, the runs list *frame*, the badge *frame*. What's replaced: the grade/alias
selects, both checkboxes, the grade-gap and web-search hints, and every `meta.grade` /
`r.alias` read.

**Picker state** (adjust-state-during-render, same pattern the old grade-snap used):

```tsx
const executors = useExecutors();
const rows = executors.data ?? [];
type Pick = { provider: string; model: string; mode: Mode };
const [picked, setPicked] = useState<Pick | null>(null);

// Preselect the backend default once rows arrive; never overwrite an explicit pick.
if (picked === null && rows.length > 0) {
  const def = defaultExecutorRow(rows);
  if (def)
    setPicked({
      provider: def.provider,
      model: defaultModelFor(def),
      mode: defaultModeFor(def),
    });
}

const pickedRow = rows.find((r) => r.provider === picked?.provider) ?? null;
// Never yank: a refetch that disables the picked row keeps the selection and
// surfaces the reason on the Generate button instead.
const pickedReason = pickedRow ? executorDisabledReason(pickedRow) : null;
const noExecutors =
  executors.data != null && rows.every((r) => executorDisabledReason(r) !== null);
```

**Executor picker.** One row per `ExecutorRow` (radio-card or Select — your call
visually): `label` from the API (no branding constant in the SPA), disabled rows stay
visible with `executorDisabledReason(row)` inline. Picking row `r` sets the full
`Pick` via `defaultModelFor`/`defaultModeFor`.

**Per-pick options.**
- HirschAI: deliberately bare — one muted line, e.g. *"Runs standard on the tower's fixed
  model. The personal paragraph will be a stub."* No model, no mode.
- Commercial: model `Select` over `pickedRow.models` (`label` shown, `id` submitted) + a
  mode toggle rendered **from `pickedRow.modes`** (never hardcoded), default `high`. Copy
  may explain ("High = holistic selection, sees the posting under an always-on audit");
  submitted values stay `standard`/`high`.
- No knobs here — `[fullstack]-model-knobs` adds effort/temperature; leave the options
  area structurally open but ship without.

**Personality hint.** The checkbox died; the paragraph is capability-driven. Real
paragraph possible ⇔ commercial pick, so:

```tsx
const capable = pickedRow != null && !pickedRow.self_hosted;
const personality = usePersonality(capable);
const hint = personalityHint(capable, personality.data);
```

**Submit.**

```tsx
const run = await create.mutateAsync({
  job_application: app.id,
  mode: picked.mode,
  provider: picked.provider,
  model: picked.model, // "" for HirschAI → omitted by toPayload
});
onRunSelected(run.id);
```

Generate is disabled while `running || create.isPending || !picked || pickedReason !=
null` — when `pickedReason` is set, show it next to the button (that's the never-yank
half: selection stays, submit explains).

**400 handling.** Replace the blanket toast: on `ApiError` with `status === 400`, surface
the first message from `(e.data as { mode?: string[]; provider?: string[] })`; if it's the
"No executor available" message, also `executors.refetch()` — the panel falls into the
offline state by itself. Keep the generic toast for everything else.

**Offline state.** `noExecutors` → instead of the picker: *"No AI is available right now —
HirschAI is offline and no commercial key is configured."* plus a text pointer toward
hand-curation (the actual "No AI — curate by hand" affordance is
`[frontend]-manual-no-run-mode`; render prose, not a dead button). This is a state, not an
error — no toast.

**Badges + runs list.** Result badge: `{result.meta.mode} ·
{providerLabel(rows, result.meta.provider)}` and append ` · {result.meta.model}` when
non-empty. Runs-list rows: `{r.mode} · {providerLabel(rows, r.provider)}`. The
`snippet_ranking` / stub / ai-share / grounding badges are untouched.

**Auto-run UX (nothing to build, verify it).** Create with HirschAI up → detail page opens
onto the pending auto-run (route seeds from `runs[0]`, WS streams). Tower down at create →
application stays empty forever (backend rule, never retro); the panel simply offers
Generate.

## Tests (on disk, red — the acceptance criteria)

Landed with this activation; run with `cd frontend && npx vitest run`:

- `tests/lib/executors.test.ts` — **new, red** until zone A exists: disabled-reason
  matrix, default-row preselect order, per-pick model/mode defaults, `providerLabel`
  fallback.
- `tests/lib/generations.test.ts` — **rewritten, red** until `toPayload` speaks
  `{mode, provider, model}` with blanks omitted; badge/reducer/stale describes unchanged
  and must stay green.
- `tests/lib/letter-chat.test.ts` — **rewritten, red** until `chatPayload` drops the
  alias; the `chatAliases`/`preferredRefineAlias` describes are gone with the helpers.
- `tests/lib/queries/llm.test.ts` — **pruned** (alias/pins/address-search describes
  deleted); the surviving zone-B describes must stay green untouched. Guide 2 rewrites
  this file.

## Verification

`tsc -b` clean; vitest green. Click-through: ollama up → create an application → it fills
itself in without touching Generate (auto-run streams; badge `standard · HirschAI`); stop
ollama → create another → empty application, HirschAI row visible but greyed "offline",
panel steers to hand-curation prose; restart ollama → the empty application does **not**
retro-generate; ≤30 s later the row is green and Generate is offered; with a configured
Anthropic row → model dropdown + mode toggle appear (default `high` / catalog default
model), a run's badge shows `high · Anthropic · claude-…`. Letter editor: rewrite +
refine chat still work (default executor); the "Search with …" buttons are gone.

## Results

<!-- Human fills this in. -->
