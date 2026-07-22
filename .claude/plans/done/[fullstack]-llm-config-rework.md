# [fullstack] llm-config-rework

> **SPA phase, guides 1+2 merged — one break, one branch** (`frontend/llm-config-rework`,
> already cut). Merged 2026-07-17 on Lukas's call: move fast, no frozen zones, no "accepted
> breakage windows". The former split preserved dead alias-era config code through guide 1
> just to delete it in guide 2 — a compat bridge, exactly what `no-compat-clean-breaks`
> forbids. This guide kills the whole alias era in one sweep: backend leftovers repaired,
> `llm.ts` rewritten whole, generate panel + config tab rebuilt, every alias consumer
> converted. **Red tests are on disk** (frontend `tests/lib/…` + the backend suites — the
> config-API tests in `llm_connector/tests/test_api.py` were already written against the
> target serializer and are red today).

## Context / goal

The generate panel becomes **executor-first**: pick the machine; mode and model hang off
it. The config tab becomes **one credential card per commercial provider**. Everything
alias/grade-shaped dies — libs, components, tests, and the two backend surfaces the rework
missed.

**Order of work** (each step leaves the suites greener, never redder):

1. Backend repairs (serializers + spa rebuild view) → backend suite green.
2. `lib/queries/llm.ts` full rewrite → `executors.test.ts` + `queries/llm.test.ts` green.
3. `lib/queries/generations.ts` → `generations.test.ts` green.
4. `applications.ts` / `letter-chat.ts` / `personality.ts` → `letter-chat.test.ts` green.
5. Components: generate panel, config tab, the de-aliased consumers → `tsc -b` green.
6. Click-through.

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
  `{id, label, relevance_score, pinned, warning}` — type `pinned`/`warning` now, render
  them in `[frontend]-entry-pins-ui`.

### Auto-run is backend-side

`JobApplicationViewSet.perform_create` (`jac/views.py:373`) spawns a `standard` run on the
user's default executor when one is available — never retroactively. The SPA **must not**
POST a run at create anymore.

### Dead and changed endpoints

- **Dead:** `GET /api/llm/aliases/`, `GET/PUT /api/llm/pins/`,
  `POST /api/jac/applications/<pk>/find_address/` (address web-search is deprecated — the
  address comes from the posting-text extract in the pipeline).
- **Changed:** `…/rewrite/` and `…/chat/` (`jac/views.py:390` / `:447`) now take optional
  `provider`/`model` instead of `alias`; blank = the user's default executor; 400
  `{"provider": [msg]}` on `ExecutorError`. The chat strength gate died — any executor
  chats.

## Step 1 — backend repairs (the rework's missed surfaces)

The executor rework specced these in `done/[backend]-executor-connector.md` §9 and never
typed them; the config API crashes today (serializer lists dead columns). The red tests
already exist: `llm_connector/tests/test_api.py` (whole file) and the two rebuild tests in
`spa/tests/test_personality.py`.

### 1a. `llm_connector/serializers.py` — `LLMConfigSerializer`

Target (keep the existing `create()`/`update()` api_key pop/encrypt pattern verbatim):

```python
class LLMConfigSerializer(serializers.ModelSerializer):
    """One credential per commercial provider. `api_key` is write-only; omitting it
    on PATCH keeps the stored key. The tower is not configurable here — it is the
    operator's system row."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )
    has_api_key = serializers.BooleanField(read_only=True)

    class Meta:
        model = LLMConfig
        fields = ("id", "user", "provider", "default", "api_key", "has_api_key",
                  "created_at", "updated_at")
        read_only_fields = ("id", "has_api_key", "created_at", "updated_at")
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=LLMConfig.objects.all(), fields=("user", "provider")
            )
        ]

    def validate_provider(self, value):
        if value == Provider.ollama:
            raise serializers.ValidationError(
                "HirschAI is built in — configure commercial providers only."
            )
        return value
```

The old `validate()` (SSRF url check) leaves with the `url` field — `validate_safe_llm_url`
stays a model-level concern for the system row (`LLMConfig.clean()`).

### 1b. `llm_connector/serializers.py` — `LLMRequestLogSerializer`

Drop `"alias"` from `fields` (the column died; the viewset crashes the same way).

### 1c. `llm_connector/__init__.py` — the executor kwarg is silently dropped (live-run killer)

Found 2026-07-18. The pipeline rework typed every rung as
`complete(prompt=…, executor=self.executor)` — ten sites in `jac/llm_prompts.py`, one in
`spa/distill.py:34` — but the module helper has no `executor` parameter
(`llm_connector/__init__.py:11`). The Executor object falls into `**kwargs`, so every
live call today (a) resolves `provider=None` → the **default** executor instead of the
run's — a commercial run's rungs would run on HirschAI, breaking the single-executor
invariant and the privacy promise — and (b) rides into the adapter kwargs, where the
ollama payload dies at `json.dumps` (TypeError). Invisible to the deterministic suite
(scorers are patched) and to the live prompt tests whenever the tower is down (they
skip). The done/ guide specced `self.executor.complete(…)` per call site; **repair at
the helper instead** — one edit fixes all eleven sites and keeps the
`patch("jac.llm_prompts.complete")` / `patch("spa.distill.complete")` test seams alive:

```python
def complete(
    prompt=None, *, messages=None, executor=None, provider=None, model=None,
    user=None, **kwargs,
) -> str:
    """`executor` is the pipeline path — the run's Executor carries provider+model+
    user as one value (the single-executor invariant). The loose provider/model/user
    kwargs stay for callers outside a run (llm_check, the config check endpoint)."""
    if executor is not None:
        return executor.complete(prompt=prompt, messages=messages, **kwargs)
    return get_client(provider, user=user, model=model).complete(
        prompt=prompt, messages=messages, **kwargs
    )


def stream(
    prompt=None, *, messages=None, executor=None, provider=None, model=None,
    user=None, **kwargs,
):
    if executor is not None:
        return executor.stream(prompt=prompt, messages=messages, **kwargs)
    return get_client(provider, user=user, model=model).stream(
        prompt=prompt, messages=messages, **kwargs
    )
```

### 1d. `jac/views.py:481` — chat passes a kwarg `LetterChat` doesn't take

`LetterChat(…, job_posting=str(application.posting.posting_text), …)` — the class takes
`posting_text` (`jac/llm_prompts.py:786`), so every real chat turn is a TypeError → 500.
Hidden because the view test patches the whole class. One-word fix:
`job_posting=` → `posting_text=`.

### 1e. `jac/cover_letter.py:458` — `_ai_share` crashes on every letter

`self._REWRITE_TAX.get(self.mode, self._REWRITE_TAX["instruct"])` — the dict was
renamed to mode keys (`{"standard": 0.20, "high": 0.60}`, line 220) but the fallback
still says `"instruct"`, and `.get()`'s default argument is evaluated **eagerly** — so
this KeyErrors on *every* call, whatever the mode. Every live letter build dies here.
Fix the fallback: `self._REWRITE_TAX["standard"]`. Red on disk:
`jac/tests/test_pipeline.py::CoverLetterBookkeepingTests` (the `_ai_share` tests).

### 1f. `llm_connector/models.py` — `LLMConfig.save()` never enforces default exclusivity

The config tab's default toggle (and `resolve_config`'s "the user's default row")
assume one default per user, last write wins — nothing enforces it. Red on disk:
`llm_connector/tests/test_config.py::test_default_is_exclusive_per_user` (+ its API
twin in `test_api.py`). Add to `LLMConfig`:

```python
def save(self, *args, **kwargs):
    super().save(*args, **kwargs)
    if self.default:
        LLMConfig.objects.filter(user=self.user, default=True).exclude(
            pk=self.pk
        ).update(default=False)
```

### 1g. `spa/views.py` — `PersonalityDossierRebuildView`

Still calls `prof.ensure_dossier(alias=…, user=…)` against the landed
`ensure_dossier(executor)` signature → TypeError on every rebuild. Target:

```python
from llm_connector.conf import ExecutorError, resolve_executor


class PersonalityDossierRebuildView(APIView):
    """POST: force-rebuild + return the dossier (preview the distilled text).
    Optional body {provider, model}; blank = the user's default executor."""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            executor = resolve_executor(
                request.user,
                request.data.get("provider", ""),
                request.data.get("model", ""),
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        prof = PersonalityProfile.objects.get(user=request.user)
        prof.dossier_built_at = None  # force stale -> always re-distils
        return Response({"dossier": prof.ensure_dossier(executor)})
```

## Step 2 — `lib/queries/llm.ts`: full rewrite

Delete the file's content, write this. Everything not listed here is dead
(`AliasStrength`, `AliasInfo`, `aliasesForGrade`, `pinnedAliasFor`, `addressSearchOptions`,
`useLLMAliases`, `useGradePins`, `useSetGradePin`, `PROVIDER_SPECS`, `ProviderSpec`,
`ConfigFormState`, `buildStructuredExtra`, `parseExtraJson`, `toPayload`, `rowToState`,
`switchProvider`, the five-provider `Provider` union — provider is a plain string from the
API now):

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "./paginated";

/* ---------- executors (generate panel + every model picker) ---------- */

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

/* ---------- configs (one credential per commercial provider) ---------- */

export type LLMConfigRow = {
  id: number;
  provider: string;
  default: boolean;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

export type ConfigPayload = {
  provider: string;
  default?: boolean;
  api_key?: string;
};

/** Blank/whitespace key is omitted — a PATCH without api_key keeps the stored key. */
export function configPayload(input: {
  provider: string;
  apiKey?: string;
  makeDefault?: boolean;
}): ConfigPayload {
  const p: ConfigPayload = { provider: input.provider };
  const key = (input.apiKey ?? "").trim();
  if (key) p.api_key = key;
  if (input.makeDefault !== undefined) p.default = input.makeDefault;
  return p;
}

/* ---------- connectivity check ---------- */

/** Result of POST /api/llm/configs/<id>/check/ — the API twin of `llm_check`. */
export type CheckResult =
  | { ok: true; latency_ms: number }
  | { ok: false; error: string };

export function checkResultLabel(r: CheckResult): string {
  return r.ok ? `OK · ${r.latency_ms} ms` : r.error;
}

/* ---------- query hooks ---------- */

const URL = "/api/llm/configs/";
const KEY = ["llm", "configs"] as const;

export function useLLMConfigs() {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => (await api<Page<LLMConfigRow>>(URL)).results,
  });
}

export function useCreateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfigPayload) =>
      api<LLMConfigRow>(URL, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ConfigPayload }) =>
      api<LLMConfigRow>(`${URL}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}

export function useDeleteConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`${URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}

export function useCheckConfig() {
  return useMutation({
    mutationFn: (id: number) =>
      api<CheckResult>(`${URL}${id}/check/`, { method: "POST" }),
  });
}
```

(Invalidating `["llm"]` sweeps configs **and** executors — a key change must flip
`configured`/`default` on the picker immediately, not at the next 30 s poll.)

## Step 3 — `lib/queries/generations.ts`

```ts
import type { Mode } from "./llm"; // single definition; no cycle

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

## Step 4 — the other libs

- `lib/queries/applications.ts` — `RunSummary` → `{id, status, stage, mode, provider,
  model, created_at}`; `useRewriteParagraph` body → `{text, instruction}` (no alias — the
  server resolves the default executor); **delete** `useFindAddress` + `FoundAddress`.
- `lib/letter-chat.ts` — delete `chatAliases` + `preferredRefineAlias` (the server gate
  died); `chatPayload(body, messages)` → `{body, messages}`. Keep `REWRITE_STYLES`,
  `seedDiscussion`, transcript helpers.
- `lib/queries/personality.ts` — `useRebuildDossier`: plain `POST …rebuild/` (no
  `?alias=`); `personalityHint(capable, row)` — rename the first param, semantics now "the
  picked executor can produce a real paragraph".

## Step 5 — components

| Path | Change |
| --- | --- |
| `routes/_authenticated/applications/index.tsx` | Delete the create-time run POST (`useCreateGeneration` import, `createRun`, the mutate block + its warning toast). Create → navigate; the backend auto-runs. |
| `components/applications/generate-panel.tsx` | The rebuild — spec below. |
| `routes/_authenticated/account/llm.tsx` | The rebuild — spec below. |
| `components/applications/letter-editor.tsx` | Drop `useLLMAliases`, `addressSearchOptions`, the "Search with …" buttons + `onFindAddress`; drop the `rewriteAlias`/`canDiscuss` plumbing (rewrite/chat ride the default executor; render the chat unconditionally). The chat-assistant guide gives chat its executor picker back. |
| `components/applications/rewrite-popover.tsx` | Drop the `aliases`/`alias`/`onAlias` props and the model `Select`. |
| `components/applications/refine-chat.tsx` | Drop `useLLMAliases` + `preferredRefineAlias` + the picker; always render; send `chatPayload(body, toApi(next))`. |
| `routes/_authenticated/account/personality.tsx` | Drop the rebuild model picker (`useLLMAliases` + `Select`); `rebuild.mutate()` plain — works immediately, the backend repair landed in step 1. |
| `use-run-lifecycle.ts` / `$applicationId.tsx` | **No change — verified:** the route seeds `runId = selectedRunId ?? app.data?.runs[0]?.id` and the hook rehydrates by pk, so a backend auto-run the SPA never POSTed is picked up for free (`runs` ordering is `-created_at`). Note: `result-view.tsx` from the pre-merge table does not exist — the badge block lives in `generate-panel.tsx`. |

### `generate-panel.tsx` — the rebuild

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
visible with `executorDisabledReason(row)` inline. Picking row `r` sets the full `Pick`
via `defaultModelFor`/`defaultModeFor`.

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
paragraph possible ⇔ commercial pick:

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

### `account/llm.tsx` — the rebuild

The table + structured mask + alias pins UI die. New layout: **one card per commercial
executor row** — rendered from `useExecutors().filter((r) => !r.self_hosted)` (never
hardcoded — a future catalog provider lands frontend-free), joined with `useLLMConfigs()`
by `provider`:

- **Key status**: `has_api_key` badge ("key set" / "no key"). The key itself is never
  echoed — there is nothing to display.
- **Set / replace key**: one password input + save. No row yet →
  `useCreateConfig(configPayload({ provider, apiKey }))`; row exists →
  `useUpdateConfig({ id, body: configPayload({ provider, apiKey }) })`. Blank input =
  nothing sent (the payload helper drops it).
- **Default toggle**: `useUpdateConfig({ id, body: configPayload({ provider,
  makeDefault: true }) })` — the server enforces exclusivity (last write wins in
  `LLMConfig.save()`); the `["llm"]` invalidation refreshes every card and the generate
  panel. No local juggling.
- **Check**: the existing round-trip button (`useCheckConfig` + `checkResultLabel`), only
  on rows that exist.
- **Delete**: row delete, only on rows that exist.
- **HirschAI line** (not a card): label + `reachable` state from the same executors query
  — "built in, runs standard" copy. Not configurable here.

## Tests (on disk, red — the acceptance criteria)

Frontend (`cd frontend && npx vitest run`):

- `tests/lib/executors.test.ts` — red until the executors section exists: disabled-reason
  matrix, default-row preselect order, per-pick model/mode defaults, `providerLabel`.
- `tests/lib/queries/llm.test.ts` — red until the five-field config section exists:
  `configPayload` (trimmed key / omitted blank key / default toggle), row shape,
  `checkResultLabel`.
- `tests/lib/generations.test.ts` — red until `toPayload` speaks `{mode, provider,
  model}` with blanks omitted; badge/reducer/stale describes stay green.
- `tests/lib/letter-chat.test.ts` — red until `chatPayload` drops the alias.

Backend (`cd backend && python manage.py test llm_connector spa jac.tests.test_pipeline jac.tests.test_api`):

- `llm_connector/tests/test_api.py` — **already on disk from the rework, red today**
  (the serializer crashes): config CRUD without key material, ollama rejected, duplicate
  provider, default exclusivity, PATCH-keeps-key, request-log list. The dead-column
  serializer also breaks schema generation —
  `spa/tests/test_settings_hardening.py::test_schema_stays_public` goes green with 1a.
- `llm_connector/tests/test_config.py::test_default_is_exclusive_per_user` — red until
  1f.
- `jac/tests/test_pipeline.py::CoverLetterBookkeepingTests` (`_ai_share` tests) — red
  until 1e; `PersonalParagraphGateTests.test_commercial_with_a_dossier_writes_the_real_paragraph`
  — red until 1c (the writer mis-routes to the tower, gets nothing back, stubs).
- `spa/tests/test_personality.py` — updated 2026-07-17: `ensure_dossier(executor)`
  signatures fixed (model-level tests green already), rebuild-endpoint tests red until the
  view imports `resolve_executor` (patch target `spa.views.resolve_executor`; plus the
  400-on-`ExecutorError` case).
- `jac/tests/test_pipeline.py::PromptExecutorRoutingTests` — added 2026-07-18, red until
  step 1c: an `Instruct` rung handed a fake executor must run on THAT executor (the fake
  adapter's canned labels come back and the fake — not the tower — is instantiated).
- `jac/tests/test_api.py::LetterChatViewTests.test_chat_round_trip_without_patching_letterchat`
  — added 2026-07-18, red until steps 1c+1d: the real `LetterChat` on a fake executor
  answers 200 through the view (today: TypeError on the `job_posting` kwarg, and the
  executor would be ignored).

## Verification

`tsc -b` clean; vitest green; backend suite green. Click-through: ollama up → create an
application → it fills itself in without touching Generate (auto-run streams; badge
`standard · HirschAI`); stop ollama → create another → empty application, HirschAI row
visible but greyed "offline", panel steers to hand-curation prose; restart ollama → the
empty application does **not** retro-generate; ≤30 s later the row is green and Generate
is offered. Config tab: add an Anthropic key → card flips to "key set" **and** the
generate panel's Anthropic row enables on the shared invalidation; check → pong latency;
toggle default; replace the key without touching default; delete the row. With the key
set: model dropdown + mode toggle appear (default `high` / catalog default model), a run's
badge shows `high · Anthropic · claude-…`. Letter editor: rewrite + refine chat work on
the default executor; the "Search with …" buttons are gone. Personality tab: rebuild
dossier works (plain POST).

## Results

<!-- Human fills this in. -->
