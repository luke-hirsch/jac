# Phase 4b — Guide B: the 3-tier filter strategy (embeddings replace tag-words)

> Code-bearing setup guide — **Lukas types this**. Second of two; builds on
> [Guide A](phase-4b-native-ollama-setup-guide.md) (native Ollama provider + `embed()`).
> Both land as **one commit**. Diagnostics behind every choice were run by Claude (see §1);
> the testing in §Verify is yours.

## 1. Goal

Collapse four scattered relevance metrics into **one clean capability ladder** and delete
the self-coded tag-word filter (it "worked very very shitty"). The ladder, by the chat
model's `strength`:

1. **Embeddings** (`ai_embed_filter`) — cosine similarity to the posting via the server's
   qwen3-embedding:0.6b. Deterministic, complete, cross-lingual, fast (~2s for 86 entries). The
   **universal floor** and the whole story for a small/default model.
2. **(no small-model grading tier)** — diagnostics killed it: qwen3.5:0.8b, qwen2.5-coder:1.5b
   and llama3.2:1b all fail (broken format or judgment that drops the top hit); even
   qwen2.5-coder:7b drops the #1 entry and doesn't fit a 4GB server. **Embeddings out-judge
   them.** The small chat model's real job is the **cover letter** (Phase 4c), not filtering.
3. **Conversational** (`ai_conversational_tailor`, unchanged) — only when the user brings a
   big/paid reasoning model.

Final ladders (`ai_tailor_with_fallback`):
```
strong   : conversational -> filter -> embed -> unfiltered
standard : filter -> embed -> unfiltered
light    : embed -> unfiltered            (the default)
```
`embed` is the floor of every ladder (it always uses the server's qwen3-embedding, independent
of the chat model). Does **not** touch the cover letter (4c) or German output (4d).

## 2. Preflight
- Guide A applied (native ollama provider, `embed()`, autodetect) — `embed(['a','b'])` returns
  two 1024-vectors.
- `python manage.py test jac llm_connector` green at the start of this guide.

## 3. Deletions — the self-coded tag-word filter

Delete these entirely:
- **File:** `backend/jac/stopwords.py`.
- **`backend/jac/llm.py`:** `extract_job_keywords`, `_parse_keyword_lines`, and the
  `_FENCE_LINE`/`_LIST_MARKER`-only-for-keywords nothing-else — keep `_clean_lines`,
  `_strip_marker`, `_parse_scored_lines`, `_parse_selection_lines`, the analysis helpers,
  `_entries_block`, `_analysis_block` (still used by the scoring/conversational tiers).
- **`backend/jac/cv.py`:** `extract_keywords`, `deterministic_filter`, `_apply_keyword_filter`,
  `ai_extract_keywords`, `ai_keyword_filter`, and the `from jac.stopwords import get_stopwords`
  import. Keep `_entries_for_llm`, `_apply_scores`, `_filter_with_floor`, `ai_filter_entries`,
  `ai_rank_entries`, `ai_analyze_job`, `agentic_tailor`, `ai_conversational_tailor`.

(`agentic_tailor` stays — `cv_test` still drives it. `ai_filter_entries` stays — it's the
`filter` tier for standard/strong.)

## 4. Additions

### 4a. `backend/jac/llm.py` — embedding ranking (top of file + a new section)
At the imports, add `math` and pull `embed` from the connector:
```python
import math
import re

from llm_connector import complete, embed

# qwen3-embedding is asymmetric and instruction-tuned: the QUERY gets an
# "Instruct: <task>\nQuery:<text>" wrapper, DOCUMENTS are embedded raw. (This
# differs from nomic's search_query:/search_document: prefixes — model-specific.)
_EMBED_TASK = "Given a job posting, retrieve the CV entries most relevant to it."
```
Add a new section (e.g. after the scoring wrappers, before the conversational one):
```python
# ---------- Embedding-based ranking (no generation — the universal filter) ----------

# Cap the query so a long posting stays within the embedder's context window.
_EMBED_QUERY_CHARS = 6000


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors. 0.0 if either is empty/zero-norm."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank_entries_by_embedding(
    job_text: str,
    entries: list[dict],
    llm: str = "default",
    user=None,
) -> list[dict]:
    """Score entries by cosine similarity of their embeddings to the job posting.

    Generation-free: it uses the alias's embedding model (qwen3-embedding:0.6b), so it
    produces a complete, deterministic relevance ranking over ALL entries — exactly
    where small chat models fail at per-entry judgement. Cross-lingual, so a German
    posting still matches an English CV on meaning.

    entries: [{"id", "type", "text"}, ...]
    returns: [{"id", "score": <cosine>, "reason": ""}, ...] in input order.
    Returns [] if the embedding count doesn't line up (caller treats as failure).
    """
    if not entries:
        return []
    query = f"Instruct: {_EMBED_TASK}\nQuery:{job_text[:_EMBED_QUERY_CHARS]}"
    docs = [e.get("text") or "" for e in entries]  # qwen3-embedding embeds documents raw
    vectors = embed([query] + docs, alias=llm, user=user)
    if len(vectors) != len(entries) + 1:
        return []
    query_vec, doc_vecs = vectors[0], vectors[1:]
    return [
        {"id": e.get("id"), "score": _cosine(query_vec, dv), "reason": ""}
        for e, dv in zip(entries, doc_vecs)
    ]
```

### 4b. `backend/jac/cv.py` — the embed rung
Add (e.g. beside `ai_filter_entries`):
```python
    def ai_embed_filter(self, job_text: str, threshold: float = 0.42) -> None:
        """Rank entries by embedding similarity to the posting, drop below threshold.

        Generation-free, so it works for any model (and the no-LLM case): embeddings
        come from the server's default Ollama + qwen3-embedding, independent of the chat
        model — a fixed project capability and the universal floor. `threshold` is on the
        cosine scale — qwen3-embedding (instruction format) lands cosines in a ~0.17–0.58
        band (median ~0.36) on the dogfood posting, so 0.42 keeps roughly the top quartile;
        per-posting count variance is expected and desired (it reflects fit, see
        [[selection-size-is-intentional]]). Sections below their _MIN_PER_SECTION floor
        fall back to top-K, so output is never empty. Raises ValueError when the embedder
        returns nothing usable, so the ladder falls through.
        """
        flat = self._entries_for_llm()
        if not flat:
            return
        # Always the server default (Ollama + qwen3-embedding), NOT self.user's chat alias.
        scores = jac_llm.rank_entries_by_embedding(job_text, flat, llm="default", user=None)
        if not scores:
            raise ValueError("ai_embed_filter: embedder returned no usable scores")
        self._apply_scores(scores)
        self._filter_with_floor(threshold)
        total = sum(len(v) for v in self.entries.values())
        logger.debug("ai_embed_filter: %d entries remaining after ranking", total)
```

### 4c. `backend/jac/cv.py` — rewrite `ai_tailor_with_fallback`
Replace the whole method with the 3-tier version (drop the `language` param — it only fed
the deleted deterministic tier; no caller passes it):
```python
    def ai_tailor_with_fallback(
        self,
        job_text: str,
        llm: str = "default",
        threshold: float = 0.25,
    ) -> dict:
        """Tier the tailoring pipeline to the chat model's capability (its `strength`,
        see llm_connector.conf.get_alias_strength):

          strong   : conversational -> filter -> embed -> unfiltered
          standard : filter -> embed -> unfiltered
          light    : embed -> unfiltered            (the default / tiny model)

        `embed` ranks entries by embedding similarity — generation-free, complete, and
        the universal floor (a tiny model that can't grade still gets a real ranking; it
        always uses the server's qwen3-embedding regardless of the chat model). The self-coded
        tag-word filter was deleted; generative per-entry grading was tried on every
        sub-2B local model and dropped (none produced usable grades). Each rung falls
        through on any exception or empty result, restoring the pre-call snapshot. Returns:
          {"tier":      <which tier won>,                # "conversational"|"filter"|"embed"|"unfiltered"
           "selection": [{id, reason}, ...] | None,   # conversational only
           "keywords":  None}                          # retained for shape compatibility
        """
        from llm_connector.conf import get_alias_strength

        snapshot = {k: list(v) for k, v in self.entries.items()}

        def restore() -> None:
            self.entries = {k: list(v) for k, v in snapshot.items()}

        def tier_conversational():
            try:
                selection = self.ai_conversational_tailor(job_text, llm=llm)
                if any(self.entries.values()):
                    return {"tier": "conversational", "selection": selection, "keywords": None}
            except Exception:
                logger.warning("ai_tailor_with_fallback: conversational failed", exc_info=True)
            restore()
            return None

        def tier_filter():
            try:
                self.ai_filter_entries(job_text, threshold=threshold, llm=llm)
                if any(self.entries.values()):
                    return {"tier": "filter", "selection": None, "keywords": None}
            except Exception:
                logger.warning("ai_tailor_with_fallback: filter failed", exc_info=True)
            restore()
            return None

        def tier_embed():
            try:
                self.ai_embed_filter(job_text)
                if any(self.entries.values()):
                    return {"tier": "embed", "selection": None, "keywords": None}
            except Exception:
                logger.warning("ai_tailor_with_fallback: embed failed", exc_info=True)
            restore()
            return None

        ladders = {
            "strong": [tier_conversational, tier_filter, tier_embed],
            "standard": [tier_filter, tier_embed],
            "light": [tier_embed],
        }
        strength = get_alias_strength(llm, user=self.user)
        ladder = ladders.get(strength, ladders["light"])
        logger.debug("ai_tailor_with_fallback: strength=%s (%d tiers)", strength, len(ladder))

        for tier in ladder:
            result = tier()
            if result is not None:
                return result

        logger.info("ai_tailor_with_fallback: every tier filtered out — returning unfiltered CV")
        return {"tier": "unfiltered", "selection": None, "keywords": None}
```

## 5. Tests

### Delete (they cover removed code)
- `backend/jac/tests.py`: `CVDeterministicFilterTests`, `CVExtractKeywordsTests`,
  `CVDeterministicFilterTextRetryTests`; in `CVAIMethodsTests` the
  `test_ai_extract_keywords_delegates_to_llm`; in `LineParserTests` the two
  `test_keyword_lines_*`; in `LLMWrappersTests` the three `test_extract_job_keywords_*`.
- Replace `CVTailorWithFallbackTests` (it tested the keyword/deterministic fall-through) and
  the `CVTailorStrengthTests` with the versions below.

### Add / replace
```python
# ---- embedding ranking (jac.llm) ----  (in LLMWrappersTests or a new class)
    def test_cosine_basic(self):
        self.assertAlmostEqual(jac_llm._cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(jac_llm._cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(jac_llm._cosine([0, 0], [1, 1]), 0.0)

    @patch("jac.llm.embed")
    def test_rank_entries_by_embedding_scores_by_cosine(self, mock_embed):
        mock_embed.return_value = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]  # query, doc1, doc2
        entries = [{"id": "skill:1", "text": "a"}, {"id": "job:2", "text": "b"}]
        out = jac_llm.rank_entries_by_embedding("posting", entries)
        self.assertEqual([o["id"] for o in out], ["skill:1", "job:2"])
        self.assertAlmostEqual(out[0]["score"], 1.0)
        self.assertAlmostEqual(out[1]["score"], 0.0)

    @patch("jac.llm.embed")
    def test_rank_entries_by_embedding_empty_on_shape_mismatch(self, mock_embed):
        mock_embed.return_value = [[1.0, 0.0]]  # query only
        self.assertEqual(jac_llm.rank_entries_by_embedding("p", [{"id": "a", "text": "x"}]), [])

    @patch("jac.llm.embed")
    def test_rank_entries_by_embedding_formats_query_instruction(self, mock_embed):
        mock_embed.return_value = [[1.0], [1.0]]
        jac_llm.rank_entries_by_embedding("posting", [{"id": "a", "text": "x"}])
        inputs = mock_embed.call_args.args[0]
        self.assertTrue(inputs[0].startswith("Instruct:"))
        self.assertIn("Query:posting", inputs[0])
        self.assertEqual(inputs[1], "x")  # documents are embedded raw


class CVEmbedFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="embed")
        cls.skill_py = Skill.objects.create(user=cls.user, name="Python")
        cls.skill_excel = Skill.objects.create(user=cls.user, name="Excel")
        cls.job = Job.objects.create(user=cls.user, title="Engineer", company="Acme",
                                     started=date(2022, 1, 1))

    @patch("jac.cv.jac_llm.rank_entries_by_embedding")
    def test_drops_below_threshold(self, mock_rank):
        mock_rank.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.7, "reason": ""},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.1, "reason": ""},
            {"id": f"job:{self.job.pk}", "score": 0.6, "reason": ""},
        ]
        cv = CV(user_pk=self.user.pk)
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False):
            cv.ai_embed_filter("posting", threshold=0.5)
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})
        self.assertEqual(len(cv.entries["jobs"]), 1)

    @patch("jac.cv.jac_llm.rank_entries_by_embedding")
    def test_empty_scores_raises(self, mock_rank):
        mock_rank.return_value = []
        cv = CV(user_pk=self.user.pk)
        with self.assertRaises(ValueError):
            cv.ai_embed_filter("posting")


@patch("llm_connector.conf.get_alias_strength", return_value="strong")
class CVTailorWithFallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        cls.skill_py = Skill.objects.create(user=cls.user, name="Python", description="Backend.")
        cls.skill_excel = Skill.objects.create(user=cls.user, name="Excel", description="Sheets.")
        cls.job = Job.objects.create(user=cls.user, title="Backend Engineer", company="Acme",
                                     started=date(2022, 1, 1), description="Python services.")

    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_conversational_happy_path(self, mock_tailor, mock_strength):
        mock_tailor.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "reason": "direct"},
            {"id": f"job:{self.job.pk}", "reason": "direct"},
        ]
        cv = CV(user_pk=self.user.pk)
        result = cv.ai_tailor_with_fallback("python backend engineer")
        self.assertEqual(result["tier"], "conversational")
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})

    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_falls_through_to_filter(self, mock_tailor, mock_score, mock_strength):
        mock_tailor.side_effect = TimeoutError("boom")
        mock_score.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.9},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.05},
            {"id": f"job:{self.job.pk}", "score": 0.8},
        ]
        cv = CV(user_pk=self.user.pk)
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False):
            result = cv.ai_tailor_with_fallback("posting", threshold=0.4)
        self.assertEqual(result["tier"], "filter")
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})

    @patch("jac.cv.jac_llm.rank_entries_by_embedding")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_falls_through_to_embed(self, mock_tailor, mock_score, mock_rank, mock_strength):
        mock_tailor.side_effect = RuntimeError("boom")
        mock_score.side_effect = RuntimeError("boom")
        mock_rank.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.7, "reason": ""},
            {"id": f"job:{self.job.pk}", "score": 0.6, "reason": ""},
        ]
        cv = CV(user_pk=self.user.pk)
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False):
            result = cv.ai_tailor_with_fallback("posting")
        self.assertEqual(result["tier"], "embed")

    @patch("jac.cv.jac_llm.rank_entries_by_embedding")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_all_fail_returns_unfiltered(self, mock_tailor, mock_score, mock_rank, mock_strength):
        mock_tailor.side_effect = RuntimeError("boom")
        mock_score.side_effect = RuntimeError("boom")
        mock_rank.side_effect = RuntimeError("boom")
        cv = CV(user_pk=self.user.pk)
        before = {k: len(v) for k, v in cv.entries.items()}
        result = cv.ai_tailor_with_fallback("posting")
        self.assertEqual(result["tier"], "unfiltered")
        self.assertEqual(before, {k: len(v) for k, v in cv.entries.items()})


class CVTailorStrengthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lara")
        cls.skill_py = Skill.objects.create(user=cls.user, name="Python")
        cls.job = Job.objects.create(user=cls.user, title="Backend Engineer", company="Acme",
                                     started=date(2022, 1, 1))

    @patch("llm_connector.conf.get_alias_strength", return_value="light")
    @patch("jac.cv.jac_llm.rank_entries_by_embedding")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_light_routes_straight_to_embed(self, mock_tailor, mock_score, mock_rank, mock_str):
        mock_rank.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.7, "reason": ""},
            {"id": f"job:{self.job.pk}", "score": 0.6, "reason": ""},
        ]
        cv = CV(user_pk=self.user.pk)
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False):
            result = cv.ai_tailor_with_fallback("python backend engineer")
        self.assertEqual(result["tier"], "embed")
        mock_tailor.assert_not_called()
        mock_score.assert_not_called()
```

## Verify (run by Lukas)
1. `python manage.py test jac llm_connector` → green.
2. `grep -rn "stopwords\|deterministic_filter\|extract_keywords\|ai_keyword_filter" backend/jac/` → only comments/none.
3. `python manage.py cv_eval --user 1 --job-file data/test_job.md --verbose` → `tier=embed`, ~1s, a tailored selection (not the whole CV, not empty). Eyeball the rendered CV: technical/transferable entries up top, music/accounting/languages gone.
4. Re-run with a different posting — selection size should *vary* (that's intended).

## What you should have
```
backend/jac/stopwords.py        # DELETED
backend/jac/llm.py              # - extract_job_keywords/_parse_keyword_lines; + _cosine/rank_entries_by_embedding
backend/jac/cv.py               # - tag-word methods; + ai_embed_filter; ladder rewritten to 3 tiers
backend/jac/tests.py            # tag-word tests removed; embed + 3-tier tests added
```
Commit **both guides together**: `Phase 4b: native Ollama provider + embeddings replace tag-word filter (3-tier ladder)`.
Then update CLAUDE.md's LLM/pipeline lines + the roadmap "Shipped" list + memory.

## Known gaps
- **Embed threshold 0.42** is calibrated on one posting (qwen3-embedding's ~0.17–0.58 band);
  revisit with a wider `cv_eval` corpus. Absolute threshold is deliberate (keeps the intentional
  size variance); a percentile cut is a later option.
- **No per-entry reason** on the embed rung (cosine gives a number) — fine until the letter phase.
- **`filter`/`conversational` float scoring** kept for standard/strong (paid models). Untouched here.
- **Tier 2 (small-model filtering) intentionally empty** — diagnostics ruled it out across the whole
  sub-7B band. Revisit only if a future small model demonstrably beats embeddings on the `cv_eval` gate.

## What's next
**Phase 4c — cover-letter generator**: `backend/jac/letter.py` with a `CoverLetter` builder that
stitches `ResumeSnippet`s onto the tailored CV, generated by the small chat model (llama3.2:1b is a
coherent writer) with the same capability tiering, rendered via a `LetterRender`.
