# Handover — 2026-06-21 — cover-letter grounding implemented (saga closed)

## Goal

Implement the cover-letter faithfulness/grounding sub-step from
`[backend]-cover-letter-grounding.md`: strip the job posting from the writer and add an opt-in
`FaithfulnessCheck`. This is the last sub-step of the cover-letter saga.

## Where it stands

Branch `backend/cover-letter` merged into `main` (`--no-ff`) and deleted this session. All
cover-letter work now lives on `main`.

- **Grounding — implemented (human-typed) and live-tested.** Files:
  - `backend/jac/llm_prompts.py` — new `FaithfulnessCheck` (line-format auditor, `UNSUPPORTED <n>`
    + bullet claims; `count=None` on any failure, never `0`). `CoverLetterWriter` had the job
    posting **removed** (`job_post_text` arg + `_MAX_POST_CHARS` gone; `_COMMON` tightened to
    "use ONLY facts in the snippets").
  - `backend/jac/cover_letter.py` — `CoverLetter` gained `verify_grounding` / `verifier_alias`
    ctor args and `_grounding(body, snippets, weave_failed)`; `build()` now tracks `weave_failed`
    and adds `"grounding": {count, claims}` to the result.
  - `backend/jac/management/commands/cover_letter.py` — `--verify` / `--verifier-llm` flags;
    `_grounding_line` helper; `.cover.md` header + console line now show grounding beside ai_share.
- **Tests written** (`backend/jac/tests.py`, ~187 lines added). **Some fail** — deferred on
  purpose; the human will address them in a separate fix-guide. The live pipeline smoke-tested
  green.

## Decisions + why

- `ai_share` (provenance) and faithfulness (truth) are **orthogonal** — a 5% `ai_share` letter
  still hallucinated. Kept as two separate axes; did not fold grounding into `ai_share`.
- **Honesty rule:** failed/unreadable audit → `count=None` ("not checked"), never `0`. Raw-fallback
  body (= verbatim snippets) → `count=0` with no LLM call; no snippets → `None`.
- Posting stripped from the writer because it was the fabrication vector (1B model echoes the
  wish-list back as the candidate's facts); selection already consumed the posting upstream.
- `FaithfulnessCheck` runs under a separate strong `--verifier-llm` (a 1B writer can't audit
  itself); opt-in, one extra LLM call.

## Open threads / risks

- **Failing cover-letter tests in `jac/tests.py`** — known, deferred to a follow-up setup-guide.
  Do not assume the suite is green.
- Source was human-typed this session per working-style; the AI only did docs/merge here.

## Next action

Write a fix-guide for the failing `jac/tests.py` cover-letter tests (identify which assertions
break and why), then move on to roadmap #1 (frontend render of CV + cover letter, surfacing the
`grounding` dict next to `ai_share`).
