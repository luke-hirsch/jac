# Handover — CV ladder `strong` (Conversational) rung

## Goal

Land the final rung of the CV selection ladder (roadmap item #1): a **conversational LLM that
selects the CV holistically** — reads the posting + every entry, returns an *ordered, chosen set*
with a one-line rationale each, rather than per-entry scores. The deterministic layer then applies
**guardrails only**. Plan: `.claude/plans/to-do/[backend]-cv-strong-conversational-rung.md`.

## Where it stands

**Done — implemented + tested this session (still uncommitted in working tree at handover time):**

- `backend/jac/llm_prompts.py` — `Conversational` class (was a `pass` stub). `.selection()` calls
  `complete()`, parses a **line format** (`<id> — <why>`, best first), returns ordered `[{id, why}]`;
  `[]` on any failure. Tolerant `_PICK_RE` regex anchors on a leading `type:pk` id, takes the rest as
  the reason, skips unreadable lines (truncation-robust), validates ids against the entry set,
  de-dupes preserving order. Provider-agnostic; `_MAX_POST_CHARS = 12000`.
- `backend/jac/filter.py` — `_strong_scores` (stub returning `{}`) replaced by `_strong_selection`
  (delegates to `Conversational(...).selection()`). New `_select_holistic(selected)`: trusts the
  model's ordered picks, drops the rest, applies guardrails — **pin favourites** the model omitted,
  **never drop languages** (`min_keep is None`), **top up to `min_keep`** from natural order. No
  floors, no propagation, no count clamp. Survivors carry `score=None`, `reason=<why>`. `output()`
  now routes `strong → standard → light`, each degrading on empty.
- `backend/jac/tests.py` — three new test classes, fully offline (mocked scores / patched
  `complete`): `CVSelectHolisticTests`, `ConversationalSelectorTests`, `CVFilterStrongRoutingTests`.

**Untouched / next:** roadmap is now re-numbered — #1 is **cover-letter generation** (uses
`ResumeSnippet` boilerplate stitched by the writer model `llama3.2:1b`). A to-do plan already exists:
`.claude/plans/to-do/[backend]-setup-resume-creation-pipeline.md`.

## Decisions + why

- **Strong emits a selection, not scores.** A conversational model's strength *is* holistic judgment
  (relationships, ordering, "these two tell one story"). Reducing that to a scalar and re-deriving
  selection throws it away. So strong bypasses the shared `_select*` scoring machinery entirely and
  uses guardrails-only `_select_holistic`.
- **Favourites are a hard pin in the LLM rungs** (standard + strong), unlike `light`'s soft nudge —
  the LLM rungs have no continuous score to tilt, so "pin" is the natural analogue. Accepted as
  interim; true per-entry force-include/exclude lands in the render phase. (Already in the
  `jac-project-context` memory.)
- **Line format, not JSON** — consistent with the `no-json-llm-io` memory: token-cheap, one bad line
  doesn't sink the whole reply.

## Open threads / risks

- **Not yet run against a live model.** All tests are offline. The real conversational model's reply
  shape (does it actually emit `<id> — <why>` cleanly, or wrap in prose/markdown?) is unverified
  end-to-end — the parser is built to tolerate prose/fences/bullets, but worth a real `cv_eval`
  smoke run on a `strong`-graded alias.
- `_select_holistic` reads `e["type"]` for sectioning and `e.get("favourite")`; assumes the flattened
  entry dicts always carry `type`. Consistent with the other rungs, but confirm on real data.

## Next action

Human: type the code is already typed (volatile/spike was not used — verify the diff reads right),
**run the test suite** (`CVSelectHolisticTests`, `ConversationalSelectorTests`,
`CVFilterStrongRoutingTests`) and a live `cv_eval` smoke run on a `strong` alias to confirm the
parser handles the real model's output. Then start roadmap #1 (cover-letter) via `/setup-guide`
against `[backend]-setup-resume-creation-pipeline.md`.
