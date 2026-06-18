# Handover — cv_eval AI analysis (judge + summary)

**Date:** 2026-06-18
**Branch:** `backend/cv-eval-ai-analysis` (cut from `main`; NOT merged — in-flight)
**Guide:** `.claude/plans/to-do/[backend]-cv-eval-ai-analysis.md`

## Goal

Add an opt-in **AI analysis layer** to the `cv_eval` management command: after running the CV
pipeline over a postings corpus, have a strong LLM (1) **judge** each run's selection quality
(kept vs dropped vs the posting) and (2) write a cross-run **summary** (`analysis.md`). Plus a new
`--all-models` matrix mode (every configured model at its own auto-detected grade).

## Where it stands

**Code is typed (by the human) and compiles; tests NOT yet run/verified.**

- `backend/jac/llm_prompts.py` — two new classes added: **`TheJudge`** (per-run selection grader,
  `critique()` → `{grade, notes}`, line-format `GRADE A-F` + `<id> — note`) and **`TheAnalyst`**
  (cross-run summariser, `analyse()` → free-form prose). Note: named `TheJudge`/`TheAnalyst`, not
  the `Judge`/`Analyst` the guide used.
- `backend/jac/management/commands/cv_eval.py` — new flags `--all-models`, `--analyze`,
  `--analyst`; `_resolve_runs` gained `all_models`; `_evaluate` now captures `candidates`
  (pre-prune) → `kept`/`dropped`; new `_analyze`/`_run_block`/`_write_judge`; `findings.json`
  slimmed (kept/dropped stripped so it stays diff-comparable).
- `backend/jac/tests.py` — added `JudgeCritiqueTests` + `AnalystSummaryTests`; `ResolveRunsTests`
  extended for `--all-models`.

This session specifically: AI wrote the guide, human typed the code, AI fixed a paste-placement
bug (the kept/dropped capture block + a duplicate `row` had landed in `handle()` referencing
undefined vars; moved into `_evaluate`; also `_write_findings` was dumping `rows` not `slim`).
Both source files now `py_compile` clean.

## Decisions + why

- **Judge + summary, stacked** (not just a numbers summary): the judge sees kept-vs-dropped so it
  can flag *wrong* picks, not just count mismatches; the summary is then grounded in quality.
- **Fixed strong grader** via `--analyst` (default: strongest configured alias), never each run's
  own model — otherwise weak models grade their own homework.
- **Determinism preserved**: `findings.json/md` unchanged in shape (kept/dropped stripped before
  write), so `--compare` still diffs cleanly; analysis is purely additive artifacts.
- **Line-format I/O** for `TheJudge` (parsed) per the `no-json-llm-io` memory; `TheAnalyst` output
  is human-read prose, so free-form.

## Open threads / risks

- **Tests not run yet.** The guide's test bodies referenced `Judge`/`Analyst`; the human's typed
  version uses `TheJudge`/`TheAnalyst` — confirm imports/usages in `tests.py` all match.
- **`test_strong_currently_routes_through_standard_scorer` was removed** from `tests.py` this
  session. We did NOT touch the strong rung — flag for the human: confirm this was intentional, or
  restore it.
- Judge cost is N postings × M models LLM calls — only fires under `--analyze`, but the
  `--all-models --analyze` sweep is the heavy path.
- The cover-letter plan (`[backend]-cover-letter.md`) sits untracked in `to-do/` — unrelated to
  this branch, left for its own `backend/cover-letter` branch.

## Next action

Run the no-cost checks, then the smoke:
```bash
cd backend
python manage.py test jac.tests.JudgeCritiqueTests jac.tests.AnalystSummaryTests jac.tests.ResolveRunsTests -v 2
python manage.py cv_eval --user 1 --job-file data/test_job.md --all-models          # no LLM cost
python manage.py cv_eval --user 1 --jobs-dir data/postings --all-models --analyze    # spends LLM calls
```
Confirm `findings.json` has no `kept`/`dropped` keys, and that `*.judge.md` + `analysis.md` appear.
When green, `/wrap-up` moves the guide to `done/` and merges this branch into `main`.
