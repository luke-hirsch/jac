# Handover — 2026-07-27 — letter-eval abandoned, tests re-anchored

## Goal
Lukas rolled back all his implementation of the `[backend]-letter-eval-judge` guide (an automated
LLM-as-judge that graded a generated cover letter against a hand-made gold-standard letter). He
decided **cover-letter quality evaluation stays human** — "what is good now might not be good in my
eyes in a couple weeks." Task: remove the letter-evaluation tests, then wrap up.

## Where it stands
- **`backend/jac/tests/test_prompts.py` — re-anchored and committed** (in `67d2422 "minor warnings
  fixed"`, which Lukas committed mid-session alongside his own `llm_prompts.py` warning fixes).
  - Removed `LetterCriticPromptTests` + the dead `LetterCritic` import (the class was deleted from
    source back in `0bf0406`).
  - Removed the `ResumeSnippet` import + `SNIPPETS` fixture (snippets are gone from the pipeline);
    added a `CV_FACTS` string + `GROUNDED_BODY` constant.
  - Re-anchored `CoverLetterWriterPromptTests` → `cv_facts=CV_FACTS`, and `FaithfulnessPromptTests`
    → string `sources=CV_FACTS`. The still-valid `Embed`/`Instruct`/`AddressExtract` live tests
    are untouched. File imports/compiles clean.
- **`[backend]-letter-eval-judge.md` deleted** from `.claude/plans/to-do/letter_pipeline/`. Its
  `CoverLetterJudge` / `letter_eval` command / gold-fixtures were never on disk (the rollback was
  clean) — nothing else to remove.
- **Memory added:** `letter-quality-eval-is-human.md` (+ MEMORY.md index line).
- Untouched: the two sibling guides `[backend]-letter-matrix-pipeline.md` and
  `[frontend]-letter-matrix-ui.md` stay in `to-do/` — their code landed (`0bf0406`, `059e916`) but
  their `## Results` chapters are empty/incomplete (matrix-ui notes an open bug: the "rebuild now"
  button for the writing-sample dossier is missing), so they're still in-flight, not `done/`.

## Decisions + why
- **Letter *quality* eval is human; only *grounding* stays automated.** Don't re-propose a
  gold-standard/LLM-judge quality gate. `FaithfulnessCheck` + `ai_share` (provenance) remain.
- **Re-anchor, don't delete, the writer + faithfulness live tests** (Lukas's explicit pick over
  either deleting all three broken letter tests or leaving the file un-importable). Keeps a writer-
  shape + grounding smoke against the new snippet-free `cv_facts` API.

## Open threads / risks
- **`backend/jac/tests/test_pipeline.py` is broken and I did NOT touch it** (out of scope — it's
  `ai_share` bookkeeping, not letter eval). Two problems from the snippet rework:
  1. line 17 `from jac.models import ... ResumeSnippet` — `ResumeSnippet` is gone → the whole
     module fails to import.
  2. `CoverLetterBookkeepingTests` calls `CoverLetter._ai_share(...)`, but **`_ai_share` no longer
     exists** in `cover_letter.py` → 3 of its 4 tests are dead (only
     `test_editable_body_prepends_the_personal_paragraph` still stands).
  This lines up with the known post-rework breakage — CLAUDE.md lists an `_ai_share` repair as
  **step 1 of `[fullstack]-llm-config-rework`**. Left for that guide-owned work rather than
  silently deleting a metric's acceptance tests. But note: an ImportError breaks *collection* of
  the whole module, so nothing in `test_pipeline.py` runs until at least the `ResumeSnippet` import
  is fixed.
- **Stale durable docs not refreshed this session** (prior-session pipeline churn, deliberately not
  rewritten from a partial read): CLAUDE.md's cover-letter "current state" bullets + the
  `cover-letter-grounding-metric` and `cover-letter-language-strategy` memories still reference the
  removed `ResumeSnippet`/snippet-selection and `_ai_share`. CLAUDE.md's own caveat block already
  flags these as owned by the guide-stack refresh — reconcile them when the llm-config-rework stack
  lands, not piecemeal.

## Next action
Resume the SPA-phase guide stack at **`[fullstack]-llm-config-rework` step 1** — its `_ai_share`
repair is the fix that also un-breaks `test_pipeline.py` (drop/replace the `ResumeSnippet` import
and re-anchor `CoverLetterBookkeepingTests` on whatever `ai_share` becomes). See MEMORY.md
`[LLM executor rework]`.
