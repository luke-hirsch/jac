# [fullstack] model-knobs

> **SPA phase, guide 5.** Renamed + rescoped 2026-07-17 (was
> `[fullstack]-llm-model-catalog-and-knobs`): the catalog (`llm_connector/catalog.py`), the
> per-run model pick, and the executors endpoint **landed** with the executor rework. What
> remains is the **knobs** half — per-run `effort`/`temperature` — plus keeping the catalog's
> row shape open for pricing metadata (feeds the roadmap's pricing calculator; not built
> here).
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

"Real knobs instead of grades": per-run **effort** (reasoning/thinking budget) and
**temperature**, mapped to each provider's native parameter. **Commercial only** — HirschAI
stays deliberately bare (the panel's "maybe that's just what it is" decision; the tower
model/options live on the operator's system row).

## Affected files

| Path | Change |
| --- | --- |
| `llm_connector/catalog.py` | Per-provider `knobs` spec next to the model lists — enums, bounds, and exclusions as **data** (e.g. anthropic: effort excludes temperature). Keep the model-row shape open for `pricing: {in, out}`. |
| `llm_connector/providers/anthropic.py` | `effort` → extended-thinking `thinking={"type": "enabled", "budget_tokens": tier}` (low/medium/high tiers, always below `max_tokens`); enforce the no-temperature-with-thinking exclusion. Nothing exists today. |
| `llm_connector/providers/openai.py` | Per-call `reasoning_effort`/`temperature` in `complete()`/`stream()` (today only config-level + inside `web_search`); **verify `_is_reasoning_model` matches the gpt-5.6 lineup** — an `^o\d`-style regex silently drops effort for the current catalog. Temperature only sent when set (reasoning models reject it). |
| `llm_connector/client.py` + `executor.py` | Thread a per-call `params` dict (`effort`/`temperature`) through `complete()`/`stream()`/`web_search()`; adapters pop what they understand — one seam. |
| `jac/models.py` (+ migration) | `GenerationRun.params` (JSON, default `{}`): what the run actually ran with — reproducibility + the result badge. |
| `jac/serializers.py` | Create accepts `params`, validated against the catalog knob spec (out-of-range / unknown knob / knobs-on-HirschAI → 400, loud); echoed into result `meta`. |
| `jac/tasks.py` | Pass `run.params` into the executor calls. |
| `llm_connector/views.py` | The executors endpoint advertises `knobs` per commercial row (HirschAI: none). |
| frontend `generate-panel.tsx` + `lib/queries/generations.ts` | Render exactly the advertised knobs per pick; `params` passthrough in the payload. |

## Approach / key decisions

- **Knobs apply run-wide** (writer, selection, audits — the single executor runs them all).
  Simplest and honest; revisit only if audits visibly misbehave under high effort. The run
  stores what ran either way.
- **The knob spec is data, not code.** Exclusions (anthropic thinking ⇒ temperature unset;
  openai reasoning ⇒ no temperature) are encoded once in the catalog, validated in the
  serializer, honoured in the adapters — never scattered as per-adapter ifs.
- **The catalog stays the gate.** The rework decided model ids validate strictly against the
  catalog (`resolve_executor`) — a new model is a one-line catalog edit, not a free-text
  escape. Knob values follow the same philosophy: the spec is the contract.
- **Pricing rides later.** When the pricing-calculator roadmap item activates, `pricing`
  metadata joins the catalog rows and the executors endpoint serves it for free — nothing to
  pre-build here beyond not closing the row shape.
- **Chat reuses this** (`[fullstack]-chat-assistant-rework`): same executors rows, same
  knobs — one source, no second list.

## Tests (at activation)

- `llm_connector/tests/test_adapters.py` — per adapter: `effort` maps to a `thinking` block
  (anthropic) / `reasoning_effort` for the gpt-5.6 ids (regex fixed); temperature exclusions
  hold; ollama adapter untouched by knob kwargs.
- `llm_connector/tests/test_config.py` — knob-spec shape sanity per catalog provider.
- `jac/tests/test_api.py` — create with `params`: valid values persist + echo in meta;
  out-of-range temperature / unknown effort / knobs on HirschAI → 400.
- `llm_connector/tests/test_api.py` — executors rows advertise knobs on commercial rows only.
- `frontend/tests/lib/` — knob state ↔ payload helpers.

## Verification

Anthropic run with `effort: high` → the provider log shows the thinking block; the run row
and result badge carry the params; an out-of-range temperature 400s cleanly; the HirschAI
pick shows no knobs.

## Results

<!-- Human fills this in. -->
