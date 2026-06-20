# Handover — 2026-06-20 — snippet language flag + AI-share metric

## Goal

Make JAC cover letters work for German postings without turning into AI slop. The gap: the
cover-letter pipeline (in flight on `backend/cover-letter`) selects English `ResumeSnippet`
boilerplate and tells the writer "write in de" — silently AI-translating Lukas's authored voice.

## Where it stands

- **Plan written & approved** — `.claude/plans/to-do/[backend]-snippet-language-ai-share.md`.
  Code-bearing; Lukas types the source per working style.
- **Tests written** — `backend/jac/tests.py` (compiles; will be RED until the model field exists —
  expected, AI-writes-tests-first flow). New: `ResumeSnippetLanguageTests`,
  `SnippetSelectorLanguageTests`, `CoverLetterAiShareTests`, + 2 methods on `CoverLetterBuildTests`.
- **Source NOT yet written** — the 6 implementation steps below are untouched.
- **Pre-existing in-flight cover-letter pipeline** (also uncommitted, prior sessions): `cover_letter.py`
  (`SnippetSelector`/`CoverLetter`), `management/commands/cover_letter.py`, `JobPosting`/`JobPostAddress`
  in `models.py` + migration `0003`, `CoverLetterWriter`/`AddressExtract` in `llm_prompts.py`, spa
  UserProfile address fields (`spa/*` + migration `0002`). All committed this session as a checkpoint.

## Decisions + why

- **`language` is a flag on `ResumeSnippet`, not paired DE/EN renderings.** Lukas won't always author a
  counterpart; a flag + cross-language selection is more flexible. (His choice.)
- **Don't avoid translation — measure it.** `CoverLetter._ai_share` returns 0–1 ("≈37% written by AI")
  from provenance + per-grade rewrite tax (`_REWRITE_TAX` = 0.05/0.20/0.45). Frontend renders it.
- **Native-language preference is a SORT TIEBREAKER ONLY.** Caught a bug in the first plan: a numeric
  bonus inside `_score` would clear the `_score > 0` body keep-gate and resurrect zero-relevance
  snippets. Fixed to `key=(self._score(s), self._native(s))`, gate stays on pure relevance. Mirrors the
  favourite-bonus design in `CVFilter`.

## Open threads / risks

- `_REWRITE_TAX` constants (0.05/0.20/0.45) are a guess — retune after seeing real letters.
- Raw-fallback path (LLM call failed) leaves snippets *untranslated* in their original language — a
  pre-existing degradation, out of scope; `_ai_share` assumes the normal weave path.
- Whole `backend/cover-letter` branch is a big uncommitted pile now checkpointed but unverified/unmerged;
  tests are red until the model field lands.

## Next action

Implement step 1 of the guide: add `ResumeSnippet.language = CharField(max_length=8, default="en")`,
run `makemigrations jac` (→ `0004`) + `migrate`, then `python manage.py test jac` and walk down the
remaining steps (serializer, admin, `SnippetSelector` tiebreak, `CoverLetter._ai_share`, command output).
