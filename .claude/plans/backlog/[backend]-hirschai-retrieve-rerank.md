# [backend] HirschAI retrieve-then-rerank (embed shortlist prefilter)

> Distilled 2026-07-17 from the deleted `[backend]-staggered-instruct-pipeline` guide (git
> history keeps the full original). Its commercial half is **dead**: the single-executor
> invariant forbids tower embedding on commercial runs — they instruct-label all entries
> directly, by design (privacy promise). What survives is a **tower-only optimisation**,
> parked because the landed ladder works and the tower box itself is parked
> (`[infra]-tower-inference-server`).

## The idea

On a HirschAI `standard` run, `CVFilter` currently sends the **whole career DB** to the
instruct rung and uses the embed floor only as fallback. Staging them —

1. **retrieve**: embed-rank everything (existing `Embed` / vector store), keep a
   recall-biased per-section shortlist (generous — e.g. 2× the section's typical keep; a
   module constant),
2. **rerank**: run `Instruct` over the shortlist only,

— bounds the prompt (a small model gets a tractable job; fewer tokens through the tower GPU)
and usually improves precision. Degrade matrix: embed fails or shortlist empty → instruct
over the **full** set (today's behaviour, never an empty rung call); instruct fails → embed
floor (existing); both → keep-all. Favourites **and pins** are appended to the shortlist
(force-include) so the rerank actually *ranks* them — the selection-layer guarantees alone
would keep them unranked.

## When to activate

When instruct-over-the-full-DB gets measurably slow, flaky, or token-heavy on the tower
model, or the career DB outgrows the prompt. Measure with the statistical prompt suite
(`jac/tests/test_prompts.py`) — the old `cv_eval` tooling is deleted. Full code + red tests
at activation; the change is confined to `jac/filter.py` (plus possibly a leaner `Instruct`
prompt for bounded lists).
