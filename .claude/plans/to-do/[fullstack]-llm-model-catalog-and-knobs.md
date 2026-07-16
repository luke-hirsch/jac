# [fullstack] llm-model-catalog-and-knobs

> Spun out of the *LLM-mode redesign* follow-up (Lukas, 2026-07-15). Rides after **guide 4** (it
> enriches `GET /api/jac/executors/` with `models`/`knobs`); guide 5's panel renders whatever this
> advertises. Independent of guides 2/3/6. This is the **llm_connector rework** Lukas named —
> the biggest genuinely-new piece of the redesign.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

Two user-facing gaps the model-first panel exposes, plus a maintenance decision:

1. **Run-time model pick.** An `LLMConfig` binds exactly one model today (`config["model"]`).
   Lukas wants "pick an Anthropic model" *at generate time* — one configured provider row, a
   dropdown of that provider's current models on the panel.
2. **Real knobs instead of grades.** "I would rather use stuff most commercial models provide,
   like effort for claude models or temperature for more variety" — per-run `effort`/`temperature`
   mapped to each provider's native parameter, replacing what grades pretended to abstract.
3. **Catalog scope = Anthropic + OpenAI only.** Keeping model lists current is real maintenance,
   and two providers is the budget ("will be hard enough to keep anthropic and openai up to
   date"). **Google is benched**; xAI / Meta adapters are not added.

## Affected files

| Path | Change |
| --- | --- |
| `backend/llm_connector/catalog.py` (new) | The curated catalog as plain data — per-provider model lists + knob specs (shape below). No I/O, no SDK calls; a dict someone edits. |
| `backend/llm_connector/providers/anthropic.py` | Per-run overrides: `model = kwargs.pop("model", None) or self._model` — today a `model` kwarg **crashes** (`dict(model=self._model, **kwargs)` raises on the duplicate key). Map `effort` → extended-thinking budgets (`thinking={"type": "enabled", "budget_tokens": …}`) — nothing exists today. Temperature note: Anthropic requires temperature unset/1 while thinking is enabled — the knob spec encodes the exclusion. |
| `backend/llm_connector/providers/openai.py` | Same `model` pop-override. **Fix reasoning detection**: `_REASONING_MODEL_RE = ^o\d` misses the gpt-5.x line entirely, so `reasoning_effort` is silently dropped for the current lineup outside `web_search()`. `temperature` already flows via kwargs — only send when set (reasoning models reject it). |
| `backend/llm_connector/providers/ollama.py` | Same `model` pop-override; `temperature` merged into `options` (the existing knob path). |
| `backend/llm_connector/client.py` | Thread a per-call `params` dict (`model`/`effort`/`temperature`) through `complete()`/`stream()`/`web_search()` into adapter kwargs — one seam, adapters pop what they understand. |
| `backend/jac/models.py` + migration | `GenerationRun.params` (nullable JSON): the run stores what it actually ran with — reproducibility + the result badge. |
| `backend/jac/serializers.py` | Create accepts `params`; validated against the knob specs (temperature bounds, effort enum, model = catalog id **or** any sane free-text string). Echo model/knobs into result `meta`. |
| `backend/jac/tasks.py` | Pass `run.params` into the pipeline's main-rung LLM calls (support rungs keep their own routing — guide 4's `pick_alias`). |
| `backend/jac/views.py` (executors endpoint) | Populate `models`/`knobs` per provider from the catalog, with the config's own bound model first (deduped) so the dropdown always contains what the config would run anyway. |
| `frontend/src/routes/_authenticated/account/llm.tsx` + `lib/queries/llm.ts` | Config mask: the model field becomes a **catalog dropdown with a free-text escape** ("custom model id"); **google removed from the new-config provider choices** (existing rows stay editable and working). |
| `frontend/src/components/applications/generate-panel.tsx` | (Guide 5's file — listed only for the wiring.) Renders the dropdown + knobs the endpoint advertises; layout decisions are guide 5's. |

## The catalog

```python
CATALOG = {
    "anthropic": {
        "models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
        "knobs": ["effort"],
    },
    "openai": {
        "models": ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"],
        "knobs": ["effort", "temperature"],
    },
    # ollama/custom: no catalog — server-operated (Dr. Jacll) or free-text by nature.
    # google: deliberately absent — benched (see decisions).
}
```

Ids as of 2026-07-15 — the OpenAI trio per Lukas (the gpt-5.6-{} lineup from last week);
**verify both lists against provider docs at activation**. Keeping it current = editing this one
dict; that's the entire maintenance story.

## Approach / key decisions

- **The catalog is a convenience, never a gate.** The serializer accepts any non-empty sane model
  string; the dropdown is sugar with a free-text escape. A brand-new model the catalog hasn't
  caught up with must never be blocked — that's the direct answer to "hard to keep up to date". A
  stale catalog means stale *suggestions*, not a broken app; a bogus model id fails at the
  provider with a clean error, same as a bogus id in the config does today.
- **Knobs are provider-native, translated once.** The API speaks one vocabulary
  (`effort: minimal|low|medium|high`, `temperature: 0–2`); each adapter maps it to its native
  param — OpenAI `reasoning_effort`, Anthropic `thinking.budget_tokens` tiers (e.g. low ≈ 2k /
  medium ≈ 8k / high ≈ 16k, always below `max_tokens`), ollama `options.temperature`. Constraint
  handling lives in the knob spec, not scattered through adapters: Anthropic effort excludes
  temperature; OpenAI reasoning models reject temperature.
- **Google benched, not broken** (Lukas, 2026-07-15). The adapter stays; existing configs keep
  working — including the personal paragraph's Gemini grounding — but the new-config mask stops
  offering it, and it gets no catalog/knob maintenance. No xAI / Meta adapters either. Revisit
  when the maintenance budget allows.
- **Params live on the run.** Same pattern as `mode`/`alias`: what a run executed with sits on the
  row (badge + reproducibility), not reconstructed later from a config that may have changed.
- **Chat reuses this** (guide 7): the assistant's model picker offers the same catalog dropdown —
  one shared source, no second list to maintain.
- **Backlog: pricing calculator** (Lukas, 2026-07-15). The catalog is where per-model pricing
  metadata would live ($/1M input & output tokens) → a pre-run cost estimate on the panel. Not in
  this guide; recorded on the CLAUDE.md roadmap.

## Tests (written at activation)

- `llm_connector/tests/test_adapters.py` — per adapter: a `model` override lands in the SDK call
  (and no longer crashes); `effort` maps to `reasoning_effort` for **gpt-5.6-\*** (regex fixed)
  and to a `thinking` block for Anthropic; `temperature` reaches `options` for ollama; the
  exclusion rules (thinking ⇒ no temperature) hold.
- `llm_connector/tests/test_config.py` — catalog shape sanity: every entry has `models` + `knobs`;
  **no google entry**.
- `jac/tests/test_generation_api.py` — create with `params`: valid values persist on the run and
  echo in result `meta`; out-of-range temperature / unknown effort → 400; an unknown model string
  **passes** (the catalog is not a gate).
- `jac/tests/test_executors_api.py` — commercial executor rows carry the catalog models with the
  config's bound model first; the ollama row carries none.
- `frontend/tests/lib/llm.test.ts` — mask helpers: catalog-dropdown state ↔ payload including the
  free-text escape; provider choices exclude google for new configs while an existing google row
  still round-trips.

## Verification

Configure one Anthropic row → on the panel pick it, choose a different model from the dropdown +
effort=high → the run's badge shows model + effort and the provider log confirms the override;
type a made-up model id → the request is accepted and fails only at the provider (clean error, not
a validation wall). The config mask no longer offers Google for a new config; the existing Google
row still edits and runs.

## Results

<!-- Human fills this in. -->
