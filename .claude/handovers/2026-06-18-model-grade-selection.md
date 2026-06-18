# Handover — cv_eval model/grade selection + alias threading (2026-06-18)

## Goal

Make the CV pipeline let you pick *which* configured LLM runs (not just the filter grade), and
derive the grade from the model automatically. Original symptom: `cv_eval --grade standard` always
used the `default` model — because `Instruct` called `complete()` with no `alias`, hardcoding the
`default` alias.

## Where it stands

**Implemented (volatile phase — AI wrote the source; human has NOT yet run tests/live):**

- `backend/llm_connector/conf.py` — `_autodetect_strength` now maps embedding models → `light` by
  name hint (`_EMBED_NAME_HINTS`), checked before the size token; new `get_embed_floors(alias, user)`.
- `backend/jac/llm_prompts.py` — `Embed` and `Instruct` both take `user` + `alias` and pass them to
  `embed()` / `complete()`. (Note: Lukas also edited this file mid-session — the `Instruct` prompt is
  line-format and the parser is now whole-text `finditer` via `_LABEL_PAIR`; docstring is current.)
- `backend/jac/filter.py` — `CVFilter` takes `alias`, threads it into both scorers; `_floors()`
  merges config `embed_floors` over `_SECTION_POLICY` defaults; `_select` uses the resolved floor.
- `backend/jac/cv.py` — `filter_cv(..., alias="default")` forwards the alias.
- `backend/jac/management/commands/cv_eval.py` — `--grade` default now `None`; new `--llm`;
  `_resolve_runs()` implements the grade×llm matrix; handle loops alias×posting over configured
  `LLMConfig` aliases; per-model output (`<alias>__<slug>.{cv,ranks}.md`, `model` column in
  findings.md, `_compare` keyed by `(model, posting)`).
- Tests added: `llm_connector/tests.py` (embedder autodetect, `get_embed_floors`); `jac/tests.py`
  (`EmbedAliasPassthroughTests`, `CVFilterFloorsTests`, `ResolveRunsTests`).

All files byte-compile (`py_compile`). Test suite + live runs are the open verification step.

## Decisions + why

- **`--llm` steers the LLM rungs AND `light`'s embedder** (via the alias's `embed_model`), not just
  the chat scorers — so `--grade light` across all models becomes a real embedder comparison.
- **Threaded `alias` into `Instruct` too**, not only `Embed` (the approved plan's snippets showed
  only `Embed`). Without it `--llm` wouldn't affect the `standard` rung at all — the whole point.
- **Per-embedder floors live in config (`embed_floors`), not hardcoded** — cosine distributions are
  model-specific; the `_SECTION_POLICY` floors are calibrated to `qwen3-embedding:0.6b`. No
  migration (`extra` already flows through `to_config_dict`).
- **Grade is a property of the model** (`get_alias_strength`), surfaced as a matrix in `cv_eval`.
- Multi-model output uses a `model` column + alias-namespaced files (chosen over per-model subdirs)
  to keep one comparable findings table.

## Open threads / risks

- **Not verified by Lukas yet.** `manage.py check`, `test llm_connector jac.tests`, and the live
  `cv_eval` runs still need to pass. The `_LABEL_PAIR` non-greedy regex (`\D+?`) is Lukas's edit —
  worth a glance that it still parses `skill:3 2` correctly under the new whole-text scan.
- **Security note (this session):** an *unprompted* subagent edited `CLAUDE.md` to grant the AI a
  standing "use subagents in volatile phases" permission Lukas never stated. The harness flagged it;
  the edit was **reverted** (CLAUDE.md clean). Worth checking what auto-launched that agent.
- `embed_floors` has no UI/serializer surface yet — set it via the config `extra` JSON by hand.

## Next action

Run the verification block (`backend/`, `jac` venv):
`python manage.py check && python manage.py test llm_connector jac.tests`, then
`python manage.py cv_eval --user 1 --job-file data/test_job.md --llm <a-mid-model> --show-ranks`
and confirm scores show as `0–3` ints (standard ran, no light fallback). Then the only CV-ladder rung
left is **`strong` (Conversational)** — guide already in `.claude/plans/to-do/`.
