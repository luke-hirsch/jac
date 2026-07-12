# [backend] letter pipeline v2 — embed-ranked snippets, grade roles, strong grounding

> **Mode note:** volatile phase — Lukas delegated implementation ("handle it on your own",
> 2026-07-11), so Claude writes source here. Tests still land first; Lukas owns the live
> verification run (see `## Verification`).

## why

Lukas's verdict on generated letters: "doesn't feel like it uses the snippets." Diagnosis:

- `SnippetSelector` scores structurally (linked kept job/project +10, domain/skill overlap) —
  fine, but blind to what the posting _says_; the embedding model that already ranks the CV
  never sees the snippets.
- The writer prompt says "use them all" over up to 5 snippets — a small model smears them
  into mush; a strong model is shackled to stitching.
- Grounding is opt-in, so the one grade allowed to write freely has no mandatory audit.

## decisions (cleared with Lukas, 2026-07-11)

1. **Embedding selection, every grade**: rank all active snippets against the posting text
   (cosine, same `Instruct:/Query:` format as the CV filter), pick best intro + best closing +
   **top-3 body** (`max_body_snippets` default 5 → 3, migration). Native-language stays a
   tiebreak only. No relevance keep-gate on the embed path (the ranking IS the gate);
   the structural path keeps its `> 0` gate.
2. **Embed alias resolution**: try `embed_alias` (new optional param) → run `alias` →
   `"default"` (deduped); first alias whose `embed()` yields usable vectors wins; all fail →
   today's structural scorer as fallback (logged at INFO, never fatal). No new model field —
   the server `default` alias always carries `embed_model`.
3. **Grade roles**: light = glue only (unchanged); standard = _polish_ the three paragraphs
   into good prose (may restructure/cut, claims preserved); **strong composes its own letter
   and now sees the job posting** — posture change: the posting was the fabrication vector,
   the compensating control is (4).
4. **Grounding always-on for strong** (regardless of `verify_grounding`), plus **one repair
   pass**: unsupported claims are fed back to the writer (`unsupported_claims` param),
   re-checked once; survivors stay flagged (`grounding.repaired: true` marks the pass ran).
   Standard/light keep opt-in verify, flag-only.
5. **`ai_share` strong rewrite-tax 0.45 → 0.60** — free composition is more machine prose
   than "polished stitching"; the heuristic should say so.
6. Result dict gains `snippet_ranking: "embedding" | "structural"` (provenance for the UI).

## design

### `jac/llm_prompts.py`

- `class SnippetEmbed(Embed)` — only overrides `_EMBED_INTSTRUCT` ("Given a job posting,
  retrieve the resume snippets most relevant to it."). Reuses cap/cosine as-is.
- `CoverLetterWriter` gains `posting_text: str = ""` and `unsupported_claims: list[str]`
  kwargs.
  - `_GRADE_CLAUSE["standard"]` sharpened: polish into strong prose — reorder, tighten,
    cut redundancy — every claim from the snippets, invent nothing.
  - `_GRADE_CLAUSE["strong"]`: compose an original tailored letter; the posting steers
    emphasis/tone/ordering **but is never a source of facts about the candidate**.
  - `_prompt()`: `JOB POSTING:` block appears **only** when `grade == "strong"` and
    `posting_text` is non-empty (light/standard stay posting-blind); when
    `unsupported_claims` is passed, append a `A previous draft contained unsupported
claims — remove or replace them:` block listing one claim per `- ` line.

### `jac/cover_letter.py`

- `SnippetSelector(cv, user_pk, max_body=3, posting_language, *, user=None, alias="default",
embed_alias=None)`:
  - `select()` first tries `_embed_scores()`: one `SnippetEmbed` call over ALL active
    snippets (`id = f"{s.kind}:{s.pk}"`), per decision 2's alias chain; returns
    `{id: score}` or None.
  - Embed path: intro/closing = max by `(score, native)`; body = top `max_body` by
    `(score, native)`, no gate. Structural path: unchanged legacy behaviour.
  - Return dict gains `"ranking"`.
- `CoverLetter.__init__` gains `embed_alias=None`; `build()`:
  - `verify = self.verify_grounding or self.grade == "strong"`.
  - strong + `count > 0` → one repair: rewrite with `unsupported_claims`, re-audit,
    keep the repaired body+grounding (`repaired: True`); empty repair write → keep first
    draft's body+grounding. Non-strong grounding dict shape unchanged (no `repaired` key).
  - passes `posting_text` to the writer (writer itself ignores it below strong).
  - `_REWRITE_TAX["strong"] = 0.60`.

### `jac/models.py` + migration

- `GenerationRun.max_body_snippets` default 5 → 3 (behavioural default only; existing rows
  keep their value).

### untouched

`tasks.py` (CoverLetter encapsulates the new behaviour), serializers, WS contract. The
`snippet_ranking`/`repaired` keys ride inside the existing `result.cover_letter` JSON.

## test map (land first, red)

| file                         | class                                     | covers                                                                                                                                                                                                                                     |
| ---------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/test_cover_letter.py` | `SnippetSelectorEmbeddingTests`           | top-3 by cosine; intro/closing best-of-kind; native tiebreak on equal vectors; alias chain (`alias` → `default`) then structural fallback on total failure; first success stops the chain; `ranking` key; inactive snippets never embedded |
| `tests/test_cover_letter.py` | `CoverLetterStrongGroundingTests`         | strong verifies with `verify_grounding=False`; clean check → no repair call; dirty check → repair prompt carries the claims, re-audit, `repaired: True`; empty repair write keeps draft 1; standard stays opt-in                           |
| `tests/test_cover_letter.py` | existing classes                          | `_CoverLetterCVMixin.setUp` patches `jac.llm_prompts.embed` → `NotImplementedError`, pinning them to the structural path (old assertions stay valid); ai-share strong expectation 0.45 → 0.60                                              |
| `tests/test_llm_rungs.py`    | `CoverLetterWriterPromptTests` (extended) | strong prompt includes `JOB POSTING` + text; standard prompt omits it even when passed; `unsupported_claims` block renders; existing no-posting/no-invention tests stay                                                                    |

Run: `cd backend && python manage.py test jac.tests.test_cover_letter jac.tests.test_llm_rungs`

## Verification (Lukas)

1. Full backend suite green (`python manage.py test`).
2. Live: generate against a real posting at each grade (ollama light/standard config + one
   commercial strong alias). Judge: does the letter now _sound like the snippets_ at
   light/standard? Does strong read tailored, and does the grounding badge show
   checked/repaired counts?
3. `cover_letter` management command smoke run still passes over the corpus.

## Results

_(filled by Lukas after testing — raw test output, observed issues, what works)_

- the text by the strong model is okayish, but when reading it, it feels very redundant (same expierience mentioned in the same way multiple times). i believe an overall analysis with a model would have shown that. |
- standard model only produces one short paragraph. feels like it takes the three snippets and summerizes them instead of connecting them.
- the personality paragraph is missing in all instances, because the frontend has not implemented this
