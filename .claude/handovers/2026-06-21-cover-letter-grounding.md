# Handover — 2026-06-21 — cover-letter: snippet-language wrap-up + grounding plan

## Goal

Finish the cover-letter snippet-language / `ai_share` sub-step, then plan the next sub-step after a
real letter hallucinated despite a 5% `ai_share`: strip the posting from the writer + add a
faithfulness/grounding check.

## Where it stands

Branch: **`backend/snippet-language-ai-share`** (staying on it — grounding rides the same branch).
Everything below is committed this session as one checkpoint.

- **snippet-language + `ai_share` — implemented, NOT yet human-verified.** Files:
  `backend/jac/cover_letter.py` (`SnippetSelector` native tie-break in `select()`, `CoverLetter`
  `_ai_share` + `snippet_provenance` in `build()`), `models.py` (`ResumeSnippet.language`),
  `serializers.py`, `admin.py`, migration `0004_resumesnippet_language.py`, and a new
  `management/commands/load_snippets.py` (DE/EN snippet seeder). Tests already written in
  `tests.py`: `CoverLetterBuildTests`, `SnippetSelectorLanguageTests`, `CoverLetterAiShareTests`,
  `ResumeSnippetLanguageTests`.
- **Two source edits made by AI this session** (human explicitly asked "finish this guide"):
  (1) wired the native tie-break into `SnippetSelector.select()` — `_native` was defined but unused;
  (2) step 7 of the snippet-language guide — `> AI share: NN%` header on the `.cover.md` artifact +
  `:.0%` formatting in `management/commands/cover_letter.py`.
- **Grounding sub-step — planned only.** Guide written:
  `.claude/plans/to-do/[backend]-cover-letter-grounding.md` (full paste-ready code + embedded
  tests). No grounding code typed yet.

## Decisions + why

- **`ai_share` ≠ faithfulness.** `ai_share` is provenance accounting (snippet languages + grade
  tax), never inspects output — so 5% gave false confidence while the writer fabricated. Fix is a
  separate `FaithfulnessCheck` (mirrors `TheJudge`), not a tweak to `ai_share`. See memory
  `cover-letter-grounding-metric`.
- **Strip the posting from `CoverLetterWriter`** — it's the fabrication vector (1B model mirrors the
  posting's wish-list as the candidate's facts). Writer keeps only snippets + role title. Selection
  already used the posting upstream.
- **Honesty rule:** `FaithfulnessCheck` returns `count=None` ("not checked") on any failure, never
  `0` — a failed audit must not read as clean. Verifier runs under a separate strong `--verifier-llm`
  (a 1B writer can't fact-check itself); opt-in (one extra LLM call).
- Guide written in the *fuller* style (full blocks, affected-files table, embedded tests) after the
  human flagged the previous snippet-language guide as too terse (fragments, undefined locals like
  `self.lang` / `body_is_ai_fallback`, prose-only tests).

## Open threads / risks

- **snippet-language work is unverified** — the human hasn't run the test suite or a live letter yet.
  Run before building on it.
- The AI edited application source twice this session (normally the human's seat) at explicit
  request — worth the human eyeballing those two diffs.
- Grounding guide assumes the snippet-language `build()` structure (it stacks on this branch's
  uncommitted-until-now work, not on `main`).

## Next action

On `backend/snippet-language-ai-share`, run:
`cd backend && python manage.py check && python manage.py test jac.tests.SnippetSelectorLanguageTests jac.tests.CoverLetterAiShareTests jac.tests.CoverLetterBuildTests -v 2`
— confirm green, then implement `[backend]-cover-letter-grounding.md` (start with stripping the
posting from `CoverLetterWriter` in `llm_prompts.py`).
