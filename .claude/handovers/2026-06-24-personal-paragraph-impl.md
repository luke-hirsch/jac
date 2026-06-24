# Handover — cover-letter personal paragraph (implemented)

## Goal

Add one researched, company-specific paragraph to the cover letter: company web research × the
candidate's personality dossier, inserted after the snippet body. The second half of the
`backend/personal-paragraph` work (the questionnaire prerequisite shipped earlier this branch).

## Where it stands

**Implemented, all tests green (351), clean output. Live LLM verification NOT yet done.**

Real files landed/changed:
- `llm_connector/base.py` — `web_search()` stub + `supports_web_search=False` flag (was already in).
- `llm_connector/providers/{anthropic,openai,google}.py` — `web_search()` + `supports_web_search=True`.
  Anthropic = server `web_search` tool; OpenAI = **Responses API** (`responses.create`); Google =
  Gemini **Google Search grounding** (legacy `google-generativeai` SDK).
- `llm_connector/client.py`, `__init__.py` — `web_search()` + `can_web_search()` (were already in).
- `jac/research.py` — `CompanyResearcher` (was already in).
- `jac/llm_prompts.py` — `PersonalParagraphWriter`, `ParagraphGroundingCheck`, and a shared
  module-level `_parse_unsupported` (both faithfulness audits now call it).
- `jac/cover_letter.py` — `PERSONAL_STUB`, `_personal_paragraph`/`_personality_dossier`/`_stub`,
  `_ai_share(personal_words=…)`, render insertion, result keys, ctor params.
- `jac/management/commands/cover_letter.py` — `--personal` / `--research-llm` flags + header output.
- `spa/serializers.py` — added the missing `validate_answers` (cap + drop-blanks) for the questionnaire.
- Tests: `llm_connector/tests/test_adapters.py` (3 provider web-search test classes + flags),
  `jac/tests/test_llm_rungs.py` (parse tests repointed at `_parse_unsupported`).

Both guides are in `.claude/plans/done/`.

## Decisions + why

- **Three web-search providers, capability-gated by a class flag** (`supports_web_search`), not
  hardcoded to Anthropic: Anthropic, OpenAI (Responses API + `reasoning_effort="high"` for gpt-5.x),
  Google (Gemini grounding). `max_uses` is Anthropic-only and is popped/ignored by the other two.
- **Ollama / self-hosted web search deliberately parked → roadmap #3.** A "web-search-capable" local
  model still only *calls* a search tool; Ollama's own backend is a cloud API + key, so it doesn't
  prove the self-hosted thesis. The real win is a tool loop wiring a self-hostable model to a
  self-hostable search backend (SearXNG/Tavily), folding in the parked `scraper` app. `ollama`/
  `custom` stay `supports_web_search=False` and stub. See [[project-purpose-cv-showcase]].
- **Paragraph is capability-driven, not grade-gated**; loud `PERSONAL_STUB` on any miss (light always,
  weak non-searching standard too). Its own `ParagraphGroundingCheck` (research+personality sources,
  never snippets — would otherwise read as fully hallucinated). Real-paragraph words fold into
  `ai_share`; a stub counts 0.
- **Shared `_parse_unsupported`** so the "None-never-0" honesty rule lives in one place (the gap that
  surfaced this session: `ParagraphGroundingCheck` called a non-existent `self._parse`).

## Open threads / risks

- **Live LLM verification pending** — no real Anthropic/OpenAI/Gemini search call has run. Eyeball the
  **Gemini grounding-metadata path first** (`candidates[].grounding_metadata.grounding_chunks[].web.uri`):
  it targets the legacy `google-generativeai` SDK (newer `google.genai` changes tool config + traversal),
  and grounding URLs are often Vertex redirect links, not bare domains.
- `google-generativeai` emits a deprecation `FutureWarning`; silenced narrowly in the Google test
  helper only.
- **Send-time stub safeguard** still a TODO — nothing yet blocks a `PERSONAL_STUB` from shipping.
- Two unrelated stale to-do plans show as deleted in the working tree
  (`[backend]-setup-resume-creation-pipeline.md`, `[frontend]-setup-crud-api-calls-resumesnippet-model.md`);
  left unstaged — not part of this commit.

## Next action

Run the real end-to-end path (guide's Verification section): configure a web-search-capable alias
(anthropic / openai gpt-5.x+`reasoning_effort=high` / google gemini-2.5-pro), then
`python manage.py cover_letter --user 1 --job-file <f> --grade strong --llm <a> --personal
--research-llm <a> --verify` and confirm the company-specific paragraph + `Personal paragraph: ✓ (N
sources)` header. Then the stub paths (`--grade light`; a non-web-capable alias at standard). If
anything's off, branch fresh off `main`.
