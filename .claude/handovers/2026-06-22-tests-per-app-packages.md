# Handover — split test files into per-app `tests/` packages

## Goal
Replace the single (and growing) `tests.py` / ad-hoc `tests_*.py` files in each backend app with a
proper `tests/` package per app, split into manageable `test_*.py` files by concern, with shared
fixtures factored into a non-collected `_helpers.py`.

## Where it stands — done
All three backend apps converted and verified; originals deleted.

- **`backend/jac/tests/`** — `_helpers.py` (`_muted`, `_entry`, `_cv_with`, `_job_posting`,
  `_CoverLetterCVMixin`, `_StubSnippet`, `_keep_all`) + `test_models.py`, `test_cv_query.py`,
  `test_cv_selection.py`, `test_llm_rungs.py`, `test_cover_letter.py`, `test_commands.py`,
  `test_api.py` (the big one, ~660 lines), `test_export_import.py`, `test_research.py` (was the
  untracked `tests_scraper.py`).
- **`backend/llm_connector/tests/`** — `_helpers.py` (`FakeAdapter` + `FAKE_LLM`, registers the
  `fake` provider on import) + `test_client.py`, `test_config.py`, `test_api.py`, `test_adapters.py`.
- **`backend/spa/tests/`** — `test_auth.py` (was `tests.py`) + `test_personality.py` (was the
  untracked `tests_personality.py`). Moved as-is; each was already a single concern, no `_helpers`.

Split points follow the existing `# ===` / `# ---` banner sections in each old file. Each module's
imports were tailored to the symbols it actually uses (black-wrapped over 88 cols), not a blanket
copy of the old top-of-file import block. `test_research.py` now imports fixtures from `._helpers`
instead of `from jac.tests import ...`.

## Decisions + why
- **`_helpers.py` (leading underscore)** for shared fixtures: doesn't match the runner's `test*.py`
  collection pattern, so it's never collected as a test module, yet is importable as `from
  ._helpers import …`. Kills the old duplication (`_muted` was redefined in three files).
- **One file per banner section** (fine granularity, chosen over coarse grouping) — the sections
  already existed as the author's own seams, so the split is low-risk and the files stay readable.
- **llm_connector `test_adapters.py`** holds `OllamaAdapterTests` (was trailing under the viewset
  banner); viewset-scoping tests went to `test_api.py` to match the jac naming.
- Committed straight on `main` (maintenance reorg, no feature branch / guide).

## Open threads / risks
- **The suite has not been *run* green** — per the working style, that's Lukas's. I only did
  mechanical checks: class-set diffs prove zero classes lost; Django `build_suite` discovery imports
  every module without error (jac 184 · llm_connector 96 · spa 22 = 302 tests). A genuine run is the
  real acceptance.
- Import tailoring is usage-based; a symbol referenced only inside a string (not a `patch("…")`
  target that the regex caught) could in theory be missed — discovery didn't surface any, but a real
  run is the confirmation.
- Pre-existing uncommitted work left untouched and OUT of this commit: two untracked to-do plans
  (`[backend]-personal-paragraph.md`, `[backend]-personality-questionnaire.md`) — a different
  feature, not part of this reorg.

## Next action
Run `python manage.py test jac llm_connector spa` from `backend/` and confirm a clean wall of dots.
If green, the reorg is fully done; if anything red, it'll be a missed/extra import in the offending
`test_*.py` header (the bodies are byte-for-byte the originals).
