# Handover — 2026-07-27 — done-guide test reconciliation (backend green; frontend deferred)

## Goal
Started as "some backend tests fail after a minor `llm_prompts.py` edit — check them." The prompt
edit turned out inert; the real finding was that **two guides sitting in `plans/done/` never had
their acceptance tests reconciled** (filed to `done/` on the strength of source alone, `## Results`
chapters left as the empty placeholder). Fixed the backend fallout; mapped the frontend fallout and
**deferred it deliberately** — Lukas will implement the `cert-attachments` + `polished-render`
guides first (those turn two of the frontend red files green on their own), then we dig into the
rest.

## The rule we're operating by (Lukas's, stated this session)
**A test's correct colour is decided by where its guide lives:** guide in `plans/done/` ⇒ its tests
MUST be green; guide in `plans/to-do/` ⇒ its tests are *expected* red (acceptance tests waiting for
their guide). Corollary tripwire: a `done/` guide with an **empty `## Results` chapter** hasn't been
verified — grep the code, it's probably missing its closing steps (unskip tests / a repair / a
Results log). Both guides below had empty Results.

## The prompt edit is innocent (the original question)
`jac/llm_prompts.py` only moved `_TONE`/`_FOCUS` from class-level to instance attributes in
`CoverLetterWriter.__init__`; the prompt text (`_COMMON`) is byte-identical. The **live** prompt
suite (`jac/tests/test_prompts.py`, 8 real-LLM tests against local ollama, ~5½ min) ran fully
**green**. Those tests skip when ollama is down; they were up this session.

## Where it stands — BACKEND: fully reconciled, green
Branch **`fix/llm-config-rework-leftovers`** (cut off `main`, **uncommitted**). Backend suite =
**292 passing** (excluding the live `test_prompts` and the correctly-red `test_attachments`).

Two `done/` guides were the culprits:

### `[fullstack]-model-knobs` (done/) — source had landed, tests were still skip-marked
Every knob symbol was in the code (`catalog.KNOBS`/`knobs_for`/`validate_params`, `Executor.params`,
`map_params` on base+anthropic+openai, the dead `_REASONING_MODEL_RE` gone, the `client.py` params
seam ×3, `GenerationRun.params` migration `0002`, `ExecutorListView` knobs). But the guide's
"**step 0 = unskip**" was never run. Fixed (all test-file, AI's remit):
- Removed `@unittest.skip("[fullstack]-model-knobs …")` off 5 classes + the now-dead `import
  unittest` in each: `KnobSpecTests` (test_config), `AdapterKnobMappingTests` (test_adapters),
  `ClientParamsSeamTests` (test_client), `ExecutorKnobAdvertisingTests` (llm_connector test_api),
  `GenerationRunParamsTests` (jac test_api).
- Applied the guide's own "Unskip note" to `GenerationRunReadTests.test_detail_shape_names_the_executor`:
  added `params` **and** `letter_tone`/`letter_focus` (the last two are landed
  `manual-no-run-mode` source on the same read serializer) to the exact-set.
- **Real test bug** (invisible while skipped): `ExecutorKnobAdvertisingTests` asserted OpenAI
  advertises `temperature`; the guide's design is OpenAI = reasoning-only (`effort` only),
  Anthropic keeps both. Fixed the assertion to match. Source was correct.
- Result: all 26 model-knobs acceptance tests green.

### `[fullstack]-llm-config-rework` (done/) — Step 1 was ~90% typed; one repair + 3 test bugs left
Audited Step 1's seven sub-repairs against code: **1a ✓, 1c ✓, 1d ✓, 1f ✓, 1g ✓**; **1e moot**
(`_ai_share`/`_REWRITE_TAX` superseded by the snippet-free letter rewrite — those bookkeeping tests
were already removed from `test_pipeline.py`). Only **1b was missing**:
- **Source (the only non-test edit this session, 1 line):** dropped dead `"alias"` from
  `LLMRequestLogSerializer.fields` (`llm_connector/serializers.py`). It was raising
  `ImproperlyConfigured` and cascading into `/api/schema/` generation and every request-log view.
- **3 test bugs** (never run, so never caught): `test_default_is_exclusive_through_the_api`,
  `test_list_shows_only_own_rows_never_the_system_row`, `test_list_returns_only_own_logs` all
  iterate `.data` as a list, but these endpoints paginate (`PAGE_SIZE=100`; the guide's own
  `useLLMConfigs` reads `.results`). Fixed to read `.data["results"]`.

### Branch contents / housekeeping
`git diff --stat` on the branch also shows **pre-existing uncommitted work that was in the tree at
session start, NOT mine**: `backend/jac/tests/test_pipeline.py` (−98, the personal-paragraph
bookkeeping removal) and the two deleted `plans/to-do/letter_pipeline/*.md` files. My source
footprint is exactly one line (`serializers.py`) — verify with
`git diff --stat -- '*.py' ':!*/tests/*'`. Untracked: `plans/done/letter_pipeline/` and this
handover. **Recommend committing/merging this branch before switching to the new guides** so the
green backend fix doesn't tangle with cert-attachments/polished-render work (uncommitted changes
follow a checkout).

## Where it stands — FRONTEND: mapped, NOT touched
`cd frontend && npx vitest run` → **27 failed / 213 passed / 11 skipped** across 9 failing files.
Split by the rule:

**Correctly red — guide in `to-do/`, DO NOT touch (these go green when Lukas lands the guide):**
- `tests/lib/attachments.test.ts` — imports `@/lib/render/attachments`, which doesn't exist yet →
  `[frontend]-cert-attachments` (`to-do/beautify/`). Backend twin `jac/tests/test_attachments.py`
  (`ApplicationAttachment` import) is the same story — also correctly red, left alone.
- `tests/lib/render-moderncv.test.ts` — two-column moderncv render acceptance →
  `[frontend]-polished-render` (`to-do/beautify/`).

**Wrongly red — stale tests vs landed `done/` source (the letter-pipeline-v2 / letter-quality
rewrite changed the contract; tests still assert the old shape). This is the deferred work:**
- `tests/lib/letter-doc.test.ts` — **confirmed test-stale**: expects `editableBody` to open with a
  personal paragraph; landed source is `return letter.body;` (body-only, matching the backend
  change already in `test_pipeline.py`). Also stub-handling / `appendParagraph` asserts.
- `tests/lib/generations.test.ts` — `aiShareBadge` / `qualityBadge` (ai_share + grounding badge
  contract changed in the rewrite).
- `tests/lib/queries/personality.test.ts` — `personalityHint` expects `/stub/i`; source returns a
  different "no personality answers yet…" message. **⚠ unclear if test-stale or a source gap.**
- Also failing, not yet individually triaged: `tests/lib/applications.test.ts`
  (`runToApplicationPatch`), `tests/lib/cv-doc.test.ts` (`addEntry`), `tests/lib/export.test.ts`
  (`exportBlocker`, `skillNames/entryParts`), `tests/lib/snippet-form.test.ts`.
- **11 skipped** includes 3 `describe.skip` blocks gated on guides already in `done/` (so also
  wrongly silenced, per the rule): `cv-doc.test.ts` "entry-pins sync" + `applications.test.ts`
  "pin sync on apply" (`[frontend]-entry-pins-ui`), `letter-doc.test.ts` "manual-mode letter
  furniture" (`[frontend]-manual-no-run-mode`). Unskip + verify as part of the same pass.

## Decisions + why
- **Backend test fixes are the AI's remit; the one source line (1b) done on Lukas's explicit "add
  it for me" authorization, on its own branch** (working-style override for AI-typed source).
- **Do NOT skip-mark the to-do acceptance tests** (`test_attachments`, `attachments.test.ts`,
  `render-moderncv.test.ts`). They're correctly red — silencing them would hide a valid signal.
  This retracts an earlier (wrong) offer to skip-mark `test_attachments`.
- **Frontend deferred, not force-fixed.** Unlike the clean backend case, some "wrongly red"
  frontend tests may be *source* gaps (Lukas's to type), not test-only edits — needs a case-by-case
  pass, not a blind sweep. `personalityHint` is the first unknown.

## Open threads / risks
- The frontend reconciliation must NOT be a blind "make it green" — for each wrongly-red file decide
  *test-stale* (AI fixes the test to the landed contract) vs *source-gap* (Lukas types the missing
  source). Confirmed test-stale so far: `letter-doc` `editableBody`. Confirmed unknown:
  `personalityHint`.
- Durable-doc drift persists (flagged in the prior `2026-07-27-letter-eval-abandoned` handover):
  CLAUDE.md's cover-letter "current state" bullets + `cover-letter-grounding-metric` /
  `cover-letter-language-strategy` memories still describe the removed snippet/`_ai_share` era. A
  proper `/wrap-up` after this stack should reconcile them.
- `model-knobs` + `llm-config-rework` `## Results` chapters are still empty though their suites now
  pass — worth a one-line "verified green 2026-07-27, backend" note so the tripwire stops firing.

## Next action
1. **Lukas:** implement `[frontend]-cert-attachments` then `[frontend]-polished-render` (their own
   branches). `attachments.test.ts` + `render-moderncv.test.ts` (and backend `test_attachments.py`)
   go green as those land — expected.
2. **Then (this handover's real follow-up):** frontend stale-test reconciliation. Re-run
   `npx vitest run`, and for each remaining red file triage test-stale vs source-gap against the
   letter-pipeline-v2 / letter-quality landed source; AI fixes the test-stale ones + unskips the 3
   done-guide `describe.skip` blocks; hand Lukas a precise list of any source gaps.
3. Housekeeping: commit/merge `fix/llm-config-rework-leftovers` (green, self-contained) before or
   alongside the above so it doesn't get lost.
