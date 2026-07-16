# [backend] staggered-instruct-pipeline (shared embed prefilter)

> **⚠️ PARKED / PARTLY SUPERSEDED (2026-07-16 executor rework).** The single-executor
> invariant forbids this guide's commercial half — a commercial run must never touch the
> tower embedder, so "send the embed-ranked shortlist to the paid model" is dead. Only a
> HirschAI-internal retrieve-then-rerank could survive, as a tower-run optimisation.
> Rewrite against `[backend]-pipeline-single-executor` before any activation.

> **Guide 3** — *LLM-mode redesign*. Depends on **guide 2 (selection-ladder-remap)**. Adds a
> **shared, reachability-gated embed prefilter** — a two-stage **retrieve-then-rerank** — that is
> the default shape of **`instruct` mode** and that `conversational` uses **whenever the free
> ollama embedder is online**. The "middle ground between pure embedding and an instruct model"
> Lukas asked for, generalised (his follow-up: *"the ladder would also work for the [commercial]
> path if the embedder is online — send the ranked shortlist with the prompt; if offline they get
> the whole lot"*).
>
> Formerly named `auto-staggered-pipeline` — renamed with the 3-mode collapse (2026-07-15): what
> used to be a separate `auto` mode **is** `instruct` on the free default alias; the ladder below
> is simply how `instruct` selects.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

Pure embedding (old `light`) has good recall but weak precision; a model run over the *whole*
career DB is slow, token-heavy, and flaky on small models. Stage them, and make the retrieve stage
a **shared front** available to both AI modes:

1. **Retrieve (embed):** rank every entry by cosine (existing `_light_scores` / `Embed`), take a
   **recall-biased shortlist** — larger than the final keep (e.g. top-K per section, K generous, or
   everything above a low recall floor). Cheap; runs on the free embedder.
2. **Rerank (LLM):** run the mode's LLM rung (`Instruct` for `instruct`; the holistic
   `Conversational` for `conversational`) **only on the shortlist**, not the full set. Bounds token
   cost, gives a weak model a tractable job, and — when the executor is a paid alias — shrinks the
   payload the user pays for (the token-generosity theme: the free embedder subsidises the paid
   pass).
3. **Degrade:** LLM rung empty/unreachable → fall back to the pure-embed `_select` over the full
   scores. **Embedder offline → skip the prefilter entirely and hand the LLM rung the whole career
   DB** (its pre-prefilter behaviour); embedder *and* LLM offline → `_group_all` (keep everything
   unscored).

**Reachability gate.** The prefilter is attempted whenever the embedder answers; the gate is simply
"try to embed, and on `LLMTransportError` fall through to the full set". No dependence on guide 4's
UI probe — the pipeline degrades on the embed call itself. (Guide 4's probe is for the UI; this is
the runtime fallback.)

**Per-mode application:**
- `instruct` — prefiltered **iff** the embedder is reachable; else the whole DB goes to the
  instruct rung. On the tower this is the automatic free pipeline (embed + instruct both
  self-hosted); on a commercial alias it's the "try around" case, still subsidised by the free
  embedder when it's up.
- `conversational` — holistic wants a global view, so prefilter with a **generous** shortlist
  (token relief without starving the holistic pass), or leave full — make it a per-mode flag and
  tune with `cv_eval`. Default: generous shortlist when the embedder is up.
- `manual` — never here (keep-all in the filter, guide 2).

## Affected files

| Path | Change |
| --- | --- |
| `backend/jac/filter.py` | A shared `_prefiltered_entries()` (embed → recall-biased `_shortlist`, or the full set on embed failure) that `output()`'s `instruct`/`conversational` branches run **before** their LLM rung. The LLM rung is then constructed with `entries=shortlist`. Guardrails (favourites, `min_keep`) still apply over the full set in the selection layer. |
| `backend/jac/llm_prompts.py` | `Instruct`/`Conversational` already take `entries` — confirm they score exactly what they're given, so passing the shortlist just works. **Prompt simplification (Lukas's follow-up):** with a bounded shortlist and explicit modes, trim the instructions — a shorter list means the prompt no longer has to hedge for a huge DB, and the mode-rekeyed clause dicts (from guide 2) can collapse toward one lean clause per mode. Keep the line-format id-anchored contract (`no-json-llm-io` memory). |
| `backend/jac/tests/test_cv_selection.py` | Prefilter + degrade tests across modes. |

## Approach / key decisions

- **Shortlist size** is a recall knob, not a final-length knob (length variance stays intentional —
  see the `selection-size-is-intentional` memory). Start with: per section, keep
  `max(min_keep_ceiling, 2× the section's typical keep)` embed-top entries as candidates; tune
  against `cv_eval`. Make it a module constant so it's one line to change.
- **The rerank runs on the shortlist only.** Build the rung with `entries=shortlist`, take its
  verdict, then select. Entries not in the shortlist are dropped (they lost at the recall stage) —
  except guardrails (favourites, `min_keep`) which still apply over the full set, matching the
  existing selection layer.
- **Force-includes ride the shortlist.** If the recall stage dropped a **favourite**, the
  selection-layer guardrail would re-add it *unscored* — the LLM never saw it, so it lands unranked
  and `fitCv` treats it blind. Appending favourites to the shortlist (they're few and capped) means
  the rerank actually ranks them; the small favourite bonus then applies as usual. The
  `application-pinned-entries` plan adds a second force-include source here (per-application pins)
  — build `_shortlist` to take an explicit `force_include: set[str]` of entry ids so pins drop in
  without touching the mechanism.
- **An empty shortlist counts as embed failure.** Embedder answers but everything scores below the
  recall floor (or the section map comes out empty) → same branch as `LLMTransportError`: hand the
  rung the **full** set. The LLM rung must never be invoked with zero entries — that's a guaranteed
  degrade for nothing.
- **Report the path taken** in the result meta: `result["meta"]["prefilter"]` ∈ `"shortlist"`
  (rerank ran on the embed shortlist) / `"full"` (embedder down or empty shortlist → rung saw the
  whole DB) / `"skipped"` (no LLM rung ran at all, pure embed or keep-all). On a paid alias,
  `"full"` is a *cost* event — the entire career DB went to the paid model — and this flag is what
  lets the UI say so (guide 5 can badge it) instead of the user finding out on the invoice. Same
  spirit as the letter's grounding `count=None` convention: degrade loudly, never silently.
- **Everything self-hostable.** No provider-native calls; on the default alias embed + instruct
  both run on ollama. The MacBook's local ollama is a faithful test bed for the tower.

## Tests (written at activation)

- Embed returns a wide ranking → assert the LLM rung is called with **only** the shortlist ids, and
  the final keep is the rung verdict intersected with guardrails. (Cover `instruct` and
  `conversational`.)
- A favourite ranked below the recall cut → still present in the ids the rung receives
  (force-include), and still kept after selection.
- Embed succeeds but the shortlist is empty → the rung receives the **full** entry set (never an
  empty list).
- **`instruct` with the embedder up vs down:** up → the rung sees the shortlist; embed raises
  `LLMTransportError` → the rung sees the **full** entry set (whole lot), no crash. This is the
  core of Lukas's follow-up.
- LLM rung returns empty → fallback to the pure-embed `_select` over the full scores.
- Embedder *and* LLM rung both fail → `_group_all` (keep-all), not a crash — closes the gap where a
  transport error currently fails the whole run instead of degrading (see `filter.py`
  `_light_scores` swallowing note).
- `result["meta"]["prefilter"]` reports `shortlist`/`full`/`skipped` correctly across the degrade
  matrix.

## Verification

`cv_eval` on a corpus shows the staggered `instruct` ranking ≥ pure-embed on precision without a
runaway token count; a live run on local ollama completes within the soft time limit.

## Results

<!-- Human fills this in. -->
