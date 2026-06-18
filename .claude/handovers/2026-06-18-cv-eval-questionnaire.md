# Handover — 2026-06-18 — cv_eval: findings UX + interactive questionnaire (WIP)

## Goal

Make `cv_eval`'s output actually readable and its model-selection ergonomic: surface the AI
**summary** where the human looks (findings.md), include the embedding baseline in sweeps, and add
an interactive picker so you don't have to remember flags. Continues the cv-eval AI-analysis strand
on branch `backend/cv-eval-ai-analysis`.

## Where it stands

In flight — **code typed/applied (AI, with human go-ahead) and compiles; not yet run clean
end-to-end.** All in `backend/jac/management/commands/cv_eval.py` unless noted.

- **findings.json slimmed** — `_write_findings` now strips `ranks` too (not just kept/dropped). The
  old 83 KB file was ~72 KB of `ranks`, already duplicated in `*.ranks.md`.
- **`--all-models` includes the embedding baseline** — folds `'default'` into the alias set, so the
  `default (light)` row (server embedder `qwen3-embedding:0.6b`) shows alongside the chat models.
- **findings.md restructured** — one table per run (`## <model> (<grade>)`), a **`judge`** grade
  column (filled only with `--analyze`), and a **`## AI summary`** section at the bottom carrying
  `TheAnalyst` prose. `_analyze` now runs *before* `_write_findings`, attaches `row['judge']`, and
  *returns* the summary string (passed into `_write_findings(..., summary)`).
- **`TheAnalyst` prompt** (`backend/jac/llm_prompts.py`) re-pointed at a comparative verdict —
  "X and Y behave alike; Z is off track; all models struggle on …".
- **Interactive questionnaire** — new `--pick` flag, and auto-runs when no selection flag
  (`--llm/--grade/--all-models`) is given **in a terminal**. `_interactive_setup` asks: which models
  (multi-select, `all` default, includes `default`), grade (auto/light/standard/strong), and whether
  to run the AI analysis. `_parse_pick` parses `1,3`/`1 3`/`all`. TTY-gated on **both** stdin and
  stdout; non-TTY falls back to the plain `default` run (scripts/tests never block on `input()`).

Two bugs found-and-fixed live this session: a paste-misplacement of the kept/dropped capture (it had
landed in `handle()` instead of `_evaluate`), and an `UnboundLocalError` on `aliases` after the
`aliases`→`menu`/`configured` rename (the analyst picker still referenced the old name).

## Decisions + why

- **TTY-gate on both ends.** Only prompt when stdin AND stdout are real terminals, so `StringIO`/CI
  runs (incl. `test_cv_eval_writes_findings`, which passes no flags) skip the questionnaire and use
  the default run instead of hanging.
- **`default`-union lives in `handle`, not `_resolve_runs`.** Keeps `_resolve_runs` a pure matrix
  function so its existing unit tests stay valid.
- **Summary surfaced in findings.md, judge still opt-in.** The comparative summary is what the human
  reads, so it goes in findings.md; but it (and the judge column) only populate with `--analyze`,
  since the judge costs N postings × M models LLM calls.

## Open threads / risks

- **Unverified.** No clean run confirmed after the last fix. Run the questionnaire path
  (`--analyze`) and confirm it reaches the judge/summary without error and findings.md renders the
  per-model tables + summary.
- **Cheaper summary not built.** If the per-entry judge is overkill and only the comparative
  paragraph is wanted, a `--summarize` (one Analyst call over the numbers, no judge) was offered but
  not implemented — pick this up if cost bites.
- **Guide is now stale.** `.claude/plans/to-do/[backend]-cv-eval-ai-analysis.md` describes only the
  original judge+summary; the questionnaire / per-model tables / embedding baseline grew on top. It
  stays in `to-do/` until the feature is verified, then moves to `done/`.

## Next action

From a terminal: `python backend/manage.py cv_eval --user 1 --jobs-dir data/postings` → answer the
questionnaire (pick a couple models, say yes to analysis) and confirm `findings.md` shows per-model
tables, a `judge` column, and a `## AI summary`. Then run `python backend/manage.py test jac` for
the suite. Once green, move the plan to `done/` and merge the branch.
