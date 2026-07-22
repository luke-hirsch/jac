# [fullstack] model-knobs

> **SPA phase, guide 5 — ACTIVATED 2026-07-18** (contracts verified against code; red
> tests on disk, skip-marked). Branch: `fullstack/model-knobs`.
>
> Landed with the rework and NOT rebuilt here: the catalog (`llm_connector/catalog.py`),
> per-run model validation (`resolve_executor`), the executors endpoint. This guide adds
> the **knobs**: per-run `effort` / `temperature` on commercial executors, threaded
> through the one seam a run already has — the `Executor`. HirschAI stays deliberately
> bare (tower tuning is the operator's system row).
>
> **Prerequisite:** `llm-config-rework` step 1c (the `executor=` repair in
> `llm_connector/__init__.py`) — knobs ride `Executor.complete()`, which today is never
> reached.

## Verified current state (2026-07-18)

- `catalog.py` — rows `{id, label, default?}` only; helpers `models_for` /
  `default_model` / `is_known_model`. No knob vocabulary anywhere.
- `providers/anthropic.py` — no thinking support. **Trap:** `complete()`/`stream()`
  build `dict(model=…, max_tokens=…, messages=…, **kwargs)` — a `max_tokens` in kwargs
  is a duplicate-keyword TypeError, so the thinking budget can't ride plain kwargs
  (step 3 restructures).
- `providers/openai.py:9` — **verified bug:** `_REASONING_MODEL_RE = r"^o\d"` matches
  none of the catalog's `gpt-5.6-*` ids, so even the config-level `reasoning_effort`
  is silently dropped for every model we actually offer (`_apply_model_params` gates on
  `_is_reasoning_model()`).
- `providers/ollama.py:93` — `payload.update(kwargs)`: any stray kwarg lands in the
  wire payload. Knobs must be consumed **before** adapter kwargs, never passed through.
- `executor.py` — frozen dataclass `(provider, model, user)`; `client.py` complete/
  stream/web_search forward `**kwargs` straight to the adapter.
- `jac/models.py GenerationRun` — no params column; `jac/tasks.py:151` builds
  `Executor(run.provider or "ollama", run.model or None, user)`; result `meta` =
  `{mode, provider, model}`.
- `jac/serializers.py:486 GenerationRunCreateSerializer` — no params field;
  `llm_connector/views.py:55 ExecutorListView` — rows have no `knobs` key.
- Existing test `GenerationRunReadTests.test_detail_shape_names_the_executor` pins the
  run detail shape as an **exact set** — adding `params` means updating that literal.

## Key decisions (carried from the stub, still right)

- **Knobs apply run-wide** — writer, selection, audits all run them; the run stores what
  ran (`GenerationRun.params` + result meta). Revisit only if audits visibly misbehave.
- **The knob spec is data, not code** — bounds/choices/exclusions live once in the
  catalog; the serializer validates against it; adapters map mechanically.
- **The catalog stays the gate** — knob values follow the same philosophy as model ids.
- **OpenAI is reasoning-only** — the catalog offers `gpt-5.6-*` (all reasoning per the
  model docs; their model pages list only token params, no `temperature`). So OpenAI's
  only knob is `effort`, and the adapter drops the old `^o\d` reasoning-detection
  entirely — since the catalog is the gate, `OpenAIAdapter` never sees a non-reasoning
  model. Anthropic keeps both knobs (Claude takes a custom temperature).
- **Pricing rides later** — keep the catalog row shape open (`pricing: {in, out}` slots
  in later, the endpoint serves it for free); build nothing now.
- **Chat reuses this** (`chat-assistant-rework`): same executors rows, same knobs.

## Step 1 — the spec (`llm_connector/catalog.py`)

```python
# Per-provider knob spec: choices/bounds/exclusions as DATA. The serializer
# validates against it, the adapters map it, the executors endpoint serves it.
KNOBS: dict[str, dict[str, dict]] = {
    "anthropic": {
        "effort": {"choices": ["low", "medium", "high"]},
        "temperature": {"min": 0.0, "max": 1.0, "excludes": ["effort"]},
    },
    "openai": {
        # Reasoning-only catalog (gpt-5.6-*) — these models reject a custom
        # temperature, so effort is the only knob (Anthropic keeps both).
        "effort": {"choices": ["low", "medium", "high"]},
    },
}


def knobs_for(provider: str) -> dict:
    return {name: dict(spec) for name, spec in KNOBS.get(provider, {}).items()}


def validate_params(provider: str, params) -> list[str]:
    """Knob values against the spec — a list of user-facing problems, [] = valid."""
    if not isinstance(params, dict):
        return ["Expected an object of knob values."]
    if not params:
        return []
    spec = KNOBS.get(provider)
    if not spec:
        return [f"No knobs for {provider!r} — the tower model is operator-tuned."]
    problems = []
    for name, value in params.items():
        knob = spec.get(name)
        if knob is None:
            problems.append(f"Unknown knob {name!r} for {provider!r}.")
            continue
        if "choices" in knob and value not in knob["choices"]:
            problems.append(f"{name} must be one of: {', '.join(knob['choices'])}.")
        if "min" in knob and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not knob["min"] <= value <= knob["max"]
        ):
            problems.append(
                f"{name} must be a number between {knob['min']} and {knob['max']}."
            )
        for other in knob.get("excludes", ()):
            if other in params:
                problems.append(f"{name} and {other} cannot be combined.")
    return problems
```

## Step 2 — one seam: Executor → client → adapter

`llm_connector/executor.py` — the run's knobs live ON the executor (the prompt classes
never learn about them):

```python
@dataclass(frozen=True)
class Executor:
    provider: str
    model: str | None = None
    user: object = None
    params: dict | None = None

    def complete(self, prompt=None, *, messages=None, **kwargs) -> str:
        if self.params:
            kwargs.setdefault("params", self.params)
        return self._client().complete(prompt=prompt, messages=messages, **kwargs)
    # stream() and web_search(): the same two-line injection.
```

`llm_connector/base.py` — the mapping hook, ignore-by-default:

```python
def map_params(self, params: dict) -> dict:
    """Translate generic per-run knobs into this provider's native kwargs.
    Base: no knobs — unknown-provider safety (ollama must NEVER see knob
    kwargs; its payload builder forwards every kwarg onto the wire)."""
    return {}
```

`llm_connector/client.py` — pop + map at the top of `complete()`, `stream()`, and
`web_search()`, before any adapter call:

```python
params = kwargs.pop("params", None)
if params:
    kwargs.update(self._adapter.map_params(params))
```

## Step 3 — `providers/anthropic.py`

```python
_THINKING_BUDGET = {"low": 2048, "medium": 8192, "high": 16384}

def map_params(self, params: dict) -> dict:
    out = {}
    effort = params.get("effort")
    if effort:
        budget = self._THINKING_BUDGET.get(effort, 8192)
        out["thinking"] = {"type": "enabled", "budget_tokens": budget}
        # The API requires budget_tokens < max_tokens; reserve the full budget
        # ON TOP of the configured output room.
        out["max_tokens"] = budget + self._max_tokens
    elif "temperature" in params:
        out["temperature"] = params["temperature"]
    return out
```

And restructure `complete()`/`stream()`/`web_search()` param assembly so mapped kwargs
**override** the defaults instead of raising duplicate-keyword TypeErrors:

```python
params = dict(model=self._model, max_tokens=self._max_tokens, messages=api_msgs)
params.update(kwargs)          # was: dict(..., **kwargs)
```

## Step 4 — `providers/openai.py`

The catalog is reasoning-only (`gpt-5.6-*` — all "Highest" reasoning per the model
docs, and their pages list no `temperature`). Since **the catalog is the gate**,
`OpenAIAdapter` is only ever built for a reasoning model, so the old `^o\d` detection
is dead weight — **delete it** rather than widen it, and bake in reasoning semantics:
always `max_completion_tokens`, always forward effort.

```python
# module top: DELETE `_REASONING_MODEL_RE`, drop `import re` (no other use).
from ..catalog import default_model   # NEW import (catalog imports nothing back — safe)

# __init__: the "gpt-4o" fallback is a non-reasoning model → swap for the catalog default.
self._model = config.get("model", default_model("openai"))   # was "gpt-4o"

# DELETE _is_reasoning_model(); collapse _apply_model_params to reasoning semantics:
def _apply_model_params(self, params: dict) -> None:
    if self._max_tokens:
        params.setdefault("max_completion_tokens", self._max_tokens)
    if self._reasoning_effort:
        params.setdefault("reasoning_effort", self._reasoning_effort)

def map_params(self, params: dict) -> dict:
    out = {}
    if params.get("effort"):
        out["reasoning_effort"] = params["effort"]
    return out           # no temperature branch — not a knob for reasoning models
```

`web_search()` is untouched — it already forwards effort independently of the old gate.
Deleting the gate also revives config-level `reasoning_effort`/`max_completion_tokens`
for the whole current catalog (the `^o\d` regex silently dropped both for `gpt-5.6-*`),
deliberate.

**Effort vocabulary stays `["low","medium","high"]`.** OpenAI's guide now lists a wider
set (`none/minimal/low/medium/high/xhigh/max`) but Sol's per-model subset isn't
published, and Anthropic's thinking maps cleanly to low/med/high — so the shared spec
stays uniform across both executors and the single Select in the panel. Widen later only
against a published subset.

## Step 5 — persist + validate + thread (`jac/`)

- `jac/models.py GenerationRun`: `params = models.JSONField(default=dict, blank=True)`
  → `python manage.py makemigrations jac`.
- `jac/serializers.py GenerationRunCreateSerializer`: `"params"` joins `fields`;
  `params = serializers.JSONField(required=False, default=dict)`; in `validate()`,
  right after the executor resolves:

```python
problems = validate_params(executor.provider, attrs.get("params") or {})
if problems:
    raise serializers.ValidationError({"params": problems})
```

- `GenerationRunSerializer.fields` gains `"params"` (detail/echo; update the exact-set
  assertion in `GenerationRunReadTests.test_detail_shape_names_the_executor`).
- `jac/tasks.py`: `executor = Executor(run.provider or "ollama", run.model or None,
  user, run.params or None)`; result meta gains `"params": run.params or {}`.
- Auto-run (`jac/views.py perform_create`) sends none — server defaults, correct as-is.

## Step 6 — advertise (`llm_connector/views.py`)

Each row gains `"knobs"`: `knobs_for(provider)` on commercial rows, `{}` on the
HirschAI row. One import (`knobs_for`), two lines.

## Step 7 — frontend

- `lib/queries/llm.ts`: `ExecutorRow` gains `knobs: KnobSpec` with
  `export type KnobSpec = Record<string, { choices?: string[]; min?: number;
  max?: number; excludes?: string[] }>;` — and the fixtures in
  `tests/lib/executors.test.ts` gain `knobs: {}` / a spec so `tsc` stays green.
- `lib/queries/generations.ts`:

```ts
export type GenerationParams = Record<string, string | number>;

/** Blank/invalid inputs are omitted — the server owns defaults and validation. */
export function knobParams(input: {
  effort?: string;
  temperature?: string;
}): GenerationParams {
  const p: GenerationParams = {};
  if (input.effort) p.effort = input.effort;
  const t = (input.temperature ?? "").trim();
  if (t !== "" && !Number.isNaN(Number(t))) p.temperature = Number(t);
  return p;
}
```

  `GenerationForm` gains `params: GenerationParams`; `toPayload` appends
  `if (Object.keys(f.params).length) p.params = f.params;`.
- `generate-panel.tsx`: knob controls render **from `pickedRow.knobs`** (never
  hardcoded — HirschAI's `{}` renders nothing, a future provider lands frontend-free):
  `effort` = a small Select over `choices` plus a "model default" blank; `temperature`
  = a numeric input with the spec's bounds, disabled (with a hint) while any knob in
  its `excludes` list is set. State resets on executor pick; submit passes
  `params: knobParams(knobs)`. On a 400 with `data.params`, surface the first message
  (same pattern as the mode/provider 400s).

## Tests (on disk, skip-marked — unskipping is step 0)

Backend (`llm_connector/tests/test_config.py::KnobSpecTests`,
`…/test_adapters.py::AdapterKnobMappingTests`, `…/test_client.py::ClientParamsSeamTests`,
`…/test_api.py::ExecutorKnobAdvertisingTests`, `jac/tests/test_api.py::GenerationRunParamsTests`):

- spec sanity: every CATALOG provider has knobs (`effort` on both; `temperature` on
  anthropic only); `validate_params` catches unknown knob / out-of-range /
  bool-as-number / exclusion; OpenAI rejects `temperature` (reasoning-only catalog);
  no-knobs providers reject any params; empty params always valid.
- adapter mapping: base `map_params` → `{}`; anthropic effort → thinking block with
  `budget_tokens < max_tokens`; anthropic temperature alone passes; openai assumes
  reasoning semantics (`_apply_model_params` → `max_completion_tokens`, no regex) and
  effort → `reasoning_effort` (temperature dropped — not an OpenAI knob); ollama has no
  override (base `{}` — knob kwargs never reach the wire payload).
- client seam: `params` is popped (never forwarded raw) and the mapped kwargs reach
  the adapter (`KnobbyFake` in the test file maps `temperature`).
- API: valid params persist + echo (create response and run row); out-of-range /
  unknown / combined / HirschAI-params → 400 under `"params"`; executors rows
  advertise knobs on commercial rows only. **Unskip note:** add `"params"` to the
  exact-shape set in `test_detail_shape_names_the_executor`.

Frontend (`tests/lib/generations.test.ts` —
`describe.skip("model knobs ([fullstack]-model-knobs)")`): `knobParams` matrix (blank →
`{}`, effort only, numeric temperature parsed, garbage temperature dropped) and
`toPayload` carrying non-empty params / omitting empty.

## Verification

Anthropic run with `effort: high` → the request log shows the `thinking` block and the
bumped `max_tokens`; the run row + result badge carry the params; an anthropic
temperature `9` 400s with the bounds message; the HirschAI pick renders no knob
controls; an OpenAI `gpt-5.6-*` run with effort shows `reasoning_effort` in the request
log (the deleted `^o\d` gate proven gone — effort now reaches the wire); an OpenAI run
with `temperature` 400s as an unknown knob and the OpenAI pick renders no temperature
control. `tsc -b` + vitest + backend suite green.

## Results

<!-- Human fills this in. -->
