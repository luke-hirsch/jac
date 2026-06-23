# Handover — redistribute personal-paragraph tests into per-app topic files

## Goal
Reconcile the cover-letter **personal-paragraph** test suite with the post-refactor per-app `tests/`
package structure. A prior session had recreated a single `tests_personal_paragraph.py` (not
recognising the new structure); this session split its classes into the existing topic files and
removed the duplicate/stale copies.

## Where it stands — done
The six personal-paragraph test classes now live by **topic**, not in a feature file:

- `backend/llm_connector/tests/test_adapters.py` ← `WebSearchCapabilityTests`,
  `AnthropicWebSearchTests` (+ a local `_Block` SDK-stub helper). Imports added:
  `can_web_search`, `LLMAdapter`, `AnthropicAdapter`. Docstring broadened.
- `backend/jac/tests/test_llm_rungs.py` ← `PersonalParagraphWriterTests`,
  `ParagraphGroundingCheckTests` (both are `jac.llm_prompts` classes; added to the
  `from jac.llm_prompts import (...)` block).
- `backend/jac/tests/test_cover_letter.py` ← `CompanyResearcherTests`,
  `CoverLetterPersonalParagraphTests`. Imports added: `PERSONAL_STUB`, `CompanyResearcher`,
  `timezone`, `timedelta`, `_muted`.

Removed: the stale `backend/jac/tests/test_research.py` (old grade-gated design — every class
superseded) and the recreated `backend/jac/tests/test_personal_paragraph.py` (the capability-driven
version, now distributed). All 25 methods across the 6 classes accounted for; the three target files
`py_compile` clean.

Also updated the to-do plan `.claude/plans/to-do/[backend]-personal-paragraph.md` (Tests section +
header pointer) to name the three real homes instead of the dead single-file path.

## Decisions + why
- **Split by topic, not feature** — the established convention (see the
  `tests-split-by-topic-not-feature` memory + `2026-06-22-tests-per-app-packages` handover). A new
  feature is not a new test file.
- **Web-search capability → `llm_connector/tests/`, not jac.** The user mused it felt "part of
  llm_rungs", but `web_search`/`supports_web_search`/`can_web_search` are an `llm_connector` app
  capability; its tests belong beside `OllamaAdapterTests`, respecting the app boundary. Flagged to
  the user; offered to move if they disagree.
- The **capability-driven real-or-stub** design (`PERSONAL_STUB`, `*_is_stub`, `can_web_search`)
  superseded the older grade-gated design that the stale `test_research.py` still encoded — so that
  file was deleted wholesale, not merged.

## Open threads / risks
- **All moved tests are red by design** — they import symbols that don't exist yet
  (`jac.research`, `cover_letter.PERSONAL_STUB`, `llm_connector.can_web_search`,
  `jac.llm_prompts.PersonalParagraphWriter` / `ParagraphGroundingCheck`). They will **error on
  import** until the feature is implemented; the rest of each suite still runs. This is the intended
  test-first state, not breakage.
- The suite has **not been run** this session (per working style, that's Lukas). Verification = a
  real `python manage.py test jac llm_connector` once the implementation lands.
- Untouched, left in the working tree as the human's **in-flight** prerequisite work (NOT in this
  session's commit): `spa/models.py`, `spa/distill.py`, `spa/personality_questions.py`,
  `spa/tests/test_personality.py` (the personality-questionnaire guide), and the two untracked
  to-do plan files.

## Next action
Implement the personal-paragraph feature against these now-red tests, per
`.claude/plans/to-do/[backend]-personal-paragraph.md` (prerequisite:
`[backend]-personality-questionnaire.md`). Start with `llm_connector` (`web_search` capability +
`can_web_search`), then `jac/research.py`, then the `jac.llm_prompts` writer/grounding classes, then
the `CoverLetter.build()` slot — running the matching topic test file green at each step.
