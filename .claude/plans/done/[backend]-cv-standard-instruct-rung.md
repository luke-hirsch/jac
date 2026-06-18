# [backend] CV ladder — the `standard` (Instruct) rung

## Context / goal

Roadmap item **1** — the `standard` rung of the CV filter ladder. Today `light` (embeddings) is the
only working scorer; `CVFilter._standard_scores()` returns `{}` and silently falls back to `light`.

This guide implements `standard`: an **instruction-tuned LLM rates each CV entry's relevance** to a
posting, and the filter selects on those ratings. Crucially it also makes a small **architecture
change** to the selection layer — agreed this session — so the LLM isn't handcuffed by machinery
built to prop up a weak scorer.

### The architecture decision (why this isn't just "swap the scorer")

`CVFilter._select()` (graph propagation + **absolute cosine floors** like `skill 0.35`, `job 0.20`)
exists to compensate for embeddings being dumb: embeddings can't see that an obscure skill matters
*because* the most-relevant job uses it, so we inject that relational intelligence by hand, and the
floors are tuned to the cosine value distribution embeddings happen to produce.

That's a crutch for a weak scorer. Forcing an LLM through it does two bad things:

1. **Distribution mismatch** — an LLM's "0–1 relevance" floats don't share the embedding cosine
   distribution, so cosine-tuned floors fire wrong (the "too strict / too loose" failure).
2. **Double-counting + wasted judgment** — an LLM already reasons relationally from the entry text,
   so re-lifting via propagation double-counts, and reducing its verdict to a thresholded scalar
   throws away the thing it's actually good at.

So the rungs now differ in **both scorer and selection strategy**:

| rung | scorer emits | selection |
| --- | --- | --- |
| **light** (embed) | `{id: cosine}` | propagation + absolute floors — `_select()` *(unchanged)* |
| **standard** (instruct) | `{id: label}` (integer 0–3 relevance) | **keep-by-verdict** — `_select_ranked()` *(new)* |
| **strong** (conversational) | *holistic selection (future)* | guardrails only *(future guide)* |

`standard`'s keep rule is **count-variant by design**: keep every entry the model rates relevant
(`label >= 1`) plus pinned favourites, guarantee each section's `min_keep`, and **never clamp to a
target count** — per-posting entry-count variance reflects fit and is intentional (see the
`selection-size-is-intentional` memory). The one-page targets in `cv_eval` stay a *colour reference
only*; they do not enter selection.

The scorer is **provider-agnostic** like `Embed`: no provider-specific kwargs (ollama wants
`options.temperature`; openai/anthropic want top-level `temperature`; `format`/`options` would
*error* on the cloud providers). It works across Ollama / OpenAI / Anthropic configs unchanged.

**No JSON.** The reply format is **line-oriented** (`<id> <rating>`, one entry per line), not JSON
(see the `no-json-llm-io` memory). JSON is a token hog and brittle on small local models — one
dropped `{` corrupts the whole reply. The CV entry ids are already a greppable pattern (`type:pk`,
e.g. `skill:3`), so we anchor on them: a per-line regex extracts each `(id, rating)` pair and skips
anything it can't read, so a truncated or noisy reply still yields every complete line.

`strong` is **out of scope** here — it keeps its stub and, until the holistic selector lands, routes
through the (now-working) standard scorer. The holistic `strong` rung gets its own guide.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/llm_prompts.py` | new `Instruct` class — the standard-rung scorer (mirrors `Embed`'s shape) |
| `backend/jac/cv.py` | import `Instruct`; wire `_standard_scores()`; add `_select_ranked()` + label constants; route `output()` by rung |
| `backend/jac/tests.py` | unit tests for `_select_ranked`, `Instruct` parsing, and `output()` routing (no network) |

No migration, no API, no frontend. `cv_eval` / `cv_test` need **no change** — they already call
`filter_cv(grade="standard")` and render whatever scores come back (labels render fine as `2.0000`).

## The code

### 1. `backend/jac/llm_prompts.py`

Add the imports at the top of the file (next to the existing `from llm_connector import embed`):

```python
import logging
import math
import re

from llm_connector import complete, embed

logger = logging.getLogger(__name__)
```

> `math` is already imported at the top today — keep the single import line, just add `logging` and
> `re`, and extend the `llm_connector` import to also pull `complete`. No `json` import — the
> standard rung parses a line format, not JSON.

Replace the empty `Instruct` stub (currently `class Instruct: pass`) with:

```python
class Instruct:
    """`standard` rung: an instruction-tuned LLM rates each CV entry's relevance to the
    posting on a small integer scale (0–3). Mirrors `Embed`'s shape — construct, then call
    `ranked_entries()` — but returns LLM relevance *labels*, not cosine scores: `CVFilter`
    selects on the labels (keep-by-verdict), so there is no absolute floor to mis-calibrate.

    Provider-agnostic: like `Embed`, it sends no provider-specific generation kwargs, so the
    same code works for Ollama / OpenAI / Anthropic configs. The reply is a **line format**
    (`<id> <rating>`), not JSON — token-cheap and robust to truncation (see the `no-json-llm-io`
    memory). Any failure returns [] -> CVFilter degrades to light.
    """

    _INSTRUCTION = (
        "You are screening CV entries for relevance to a job posting.\n"
        "Rate EVERY entry from 0 to 3:\n"
        "  3 = directly required by the posting / strong match\n"
        "  2 = clearly relevant, worth showing\n"
        "  1 = weakly or tangentially relevant\n"
        "  0 = not relevant to this posting\n"
        "Output ONE line per entry: the entry id, a space, then its rating 0-3.\n"
        "Example:\n"
        "skill:3 2\n"
        "job:1 0\n"
        "No prose, no markdown, no other text."
    )
    _MAX_POST_CHARS = 12000  # crude cap; entry text is already capped in _flatten_entries
    _LABEL_MAX = 3

    # entry ids are  type:pk  (e.g. skill:3); anchor on that, then the trailing rating.
    _LINE_RE = re.compile(r"([a-z]+:\d+)\D+(\d+)")

    def __init__(self, job_post_text: str, entries: list[dict], user=None):
        self.job_post_text = job_post_text
        self.entries = entries
        self.user = user

    def ranked_entries(self) -> list[dict]:
        """Return [{id, score, reason}] with score = integer relevance label (0.._LABEL_MAX).

        Entries the model omits default to 0. Returns [] on any failure so CVFilter falls
        back to the light rung.
        """
        try:
            raw = complete(prompt=self._prompt(), user=self.user)
        except Exception:
            logger.exception("Instruct scorer: LLM call failed")
            return []
        labels = self._parse(raw)
        if not labels:
            logger.warning("Instruct scorer: no parseable labels in reply")
            return []
        return [
            {"id": e["id"], "score": labels.get(e["id"], 0), "reason": ""}
            for e in self.entries
        ]

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        lines = "\n".join(f'{e["id"]} — {e.get("text") or ""}' for e in self.entries)
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"CV ENTRIES (id — text):\n{lines}\n\nRATINGS:"
        )

    def _parse(self, raw: str) -> dict:
        """Tolerant {id: int} extraction from a line format (`<id> <rating>`).

        Scans each line for the id pattern + a trailing integer, ignoring anything it can't
        read (markdown bullets, prose, blank lines), so a truncated reply still yields every
        complete line. Keeps only ids present in this entry set and clamps ratings to
        0.._LABEL_MAX. Returns {} when nothing usable is found.
        """
        valid = {e["id"] for e in self.entries}
        out: dict = {}
        for line in (raw or "").splitlines():
            m = self._LINE_RE.search(line)
            if not m:
                continue
            eid, rating = m.group(1), m.group(2)
            if eid in valid:
                out[eid] = max(0, min(self._LABEL_MAX, int(rating)))
        return out
```

> Leave the `Conversational` stub untouched — it belongs to the `strong` rung.

### 2. `backend/jac/cv.py`

**2a. Extend the import** (currently `from jac.llm_prompts import Embed`):

```python
from jac.llm_prompts import Embed, Instruct
```

**2b. Add the label constants** to `CVFilter`, next to `_FAVOURITE_BONUS` / `_SECTION_POLICY`:

```python
    # LLM relevance-label scale for the standard/strong rungs. The model rates each entry
    # 0.._LABEL_MAX; _select_ranked keeps everything rated >= _KEEP_LABEL (plus favourites),
    # so the model's own verdict sets the cut — no absolute floor, no propagation.
    _LABEL_MAX = 3
    _KEEP_LABEL = 1
```

**2c. Rewrite `output()`** to route by rung. Replace the current method:

```python
    def output(self) -> dict:
        """Return {section: [entry dicts + score], ...}, each section ranked desc.

        Rungs differ in BOTH scorer and selection strategy:
          - light:    embedding cosine -> propagation + absolute section floors (_select).
          - standard: Instruct-LLM relevance labels -> keep-by-verdict (_select_ranked).
          - strong:   holistic selector (TBD) — currently reuses the standard scorer.
        Each LLM rung degrades to the light floor when its scorer returns nothing.
        """
        if self.grade in ("standard", "strong"):
            labels = self._standard_scores()
            if labels:
                return self._select_ranked(labels)
        return self._select(self._light_scores())
```

**2d. Wire `_standard_scores()`.** Replace the stub:

```python
    def _standard_scores(self) -> dict:
        """Instruct-LLM relevance labels {id: 0.._LABEL_MAX}. Empty on failure -> light fallback."""
        ranked = Instruct(
            self.job_post_text, self.entries, user=self.user
        ).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}
```

> Leave `_strong_scores()` as its TODO stub — it's not called by `output()` yet.

**2e. Add `_select_ranked()`** below `_select()` (before `_group_all()`):

```python
    def _select_ranked(self, labels: dict) -> dict:
        """Selection for LLM relevance *labels* (0.._LABEL_MAX) — the standard rung.

        Keep by the model's own verdict rather than an absolute floor, and do NOT propagate
        (the LLM already reasoned relationally from the entry text). Per section:
          - rank by label desc, stable (ties keep the CV's natural order — recency / name);
          - keep every entry rated >= _KEEP_LABEL, plus all favourites (pinned);
          - guarantee min_keep by topping up from the highest-ranked remainder;
          - languages (min_keep None) keep everything.
        Kept-count therefore varies with fit (intended) — never clamped to a target.
        """
        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(section, {"min_keep": 0})
            min_keep = policy["min_keep"]
            items.sort(key=lambda e: labels.get(e["id"], 0), reverse=True)

            if min_keep is None:
                keep = list(items)
            else:
                keep = [
                    e
                    for e in items
                    if labels.get(e["id"], 0) >= self._KEEP_LABEL or e.get("favourite")
                ]
                if len(keep) < min_keep:
                    kept_ids = {e["id"] for e in keep}
                    for e in items:  # already label-desc sorted
                        if e["id"] not in kept_ids:
                            keep.append(e)
                            kept_ids.add(e["id"])
                            if len(keep) >= min_keep:
                                break
                    keep.sort(key=lambda e: labels.get(e["id"], 0), reverse=True)

            out[section] = [{**e, "score": labels.get(e["id"], 0)} for e in keep]
        return out
```

**2f. Refresh the `CVFilter` docstring** so it no longer claims selection is fully shared. Replace:

```python
    """Turns per-entry relevance scores into a ranked, weakly-filtered CV.

    Scoring is pluggable (embeddings / instruct LLM / conversational LLM); the selection
    strategy is matched to the scorer's strength:
      - embeddings (light): graph propagation + absolute section floors (_select), since
        embeddings can't reason relationally and produce a known cosine distribution;
      - instruct LLM (standard): keep-by-verdict on the model's relevance labels
        (_select_ranked) — no floors (wrong distribution) and no propagation (the LLM
        already reasons relationally).
    """
```

## Tests

Add to `backend/jac/tests.py`. These are **offline** — `_select_ranked` and the parser take no
network, and `output()` routing is exercised with `_standard_scores` patched. Follows the existing
`CVSelectionTests` / `CVFavouriteBonusTests` style (synthetic entry dicts, direct method calls).

```python
class CVSelectRankedTests(TestCase):
    """CVFilter._select_ranked: keep-by-label, favourites pinned, min_keep honoured."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="standard")

    def test_keeps_relevant_drops_zero_label(self):
        # 4 jobs (min_keep 3): two rated relevant, two rated 0. min_keep forces a 3rd back.
        entries = [
            {"id": f"job:{i}", "type": "job", "text": "", "refs": [], "favourite": False}
            for i in range(1, 5)
        ]
        labels = {"job:1": 3, "job:2": 2, "job:3": 0, "job:4": 0}
        out = self._filter(entries)._select_ranked(labels)
        kept = [e["id"] for e in out["job"]]
        # two relevant kept + one zero-rated topped up to satisfy min_keep(3); ranked desc.
        self.assertEqual(kept[:2], ["job:1", "job:2"])
        self.assertEqual(len(kept), 3)

    def test_skills_count_varies_with_fit(self):
        # 8 skills (min_keep 5): 6 rated relevant -> all 6 kept (count tracks fit, no clamp).
        entries = [
            {"id": f"skill:{i}", "type": "skill", "text": "", "refs": [], "favourite": False}
            for i in range(1, 9)
        ]
        labels = {f"skill:{i}": (2 if i <= 6 else 0) for i in range(1, 9)}
        out = self._filter(entries)._select_ranked(labels)
        self.assertEqual(len(out["skill"]), 6)

    def test_favourite_pinned_despite_zero_label(self):
        # project min_keep 0; a 0-rated favourite is still kept (pinned), a 0-rated non-fav isn't.
        entries = [
            {"id": "project:1", "type": "project", "text": "", "refs": [], "favourite": True},
            {"id": "project:2", "type": "project", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_ranked({"project:1": 0, "project:2": 0})
        kept = {e["id"] for e in out["project"]}
        self.assertEqual(kept, {"project:1"})

    def test_languages_never_dropped(self):
        entries = [
            {"id": "language:1", "type": "language", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_ranked({"language:1": 0})
        self.assertEqual([e["id"] for e in out["language"]], ["language:1"])

    def test_ranked_descending_by_label(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:3", "type": "job", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_ranked({"job:1": 1, "job:2": 3, "job:3": 2})
        self.assertEqual([e["id"] for e in out["job"]], ["job:2", "job:3", "job:1"])

    def test_score_is_the_label(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_ranked({"job:1": 2})
        self.assertEqual(out["job"][0]["score"], 2)


class InstructScorerParseTests(TestCase):
    """Instruct._parse: tolerant line parsing, validating, clamping — no network."""

    def _scorer(self):
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:1", "type": "job", "text": "Dev at X"},
        ]
        return Instruct("posting", entries)

    def test_parses_clean_lines(self):
        self.assertEqual(
            self._scorer()._parse("skill:1 3\njob:1 1"),
            {"skill:1": 3, "job:1": 1},
        )

    def test_tolerates_markdown_and_separator_drift(self):
        # bullets, em-dash, colon, code fences, blank lines — all survive.
        raw = "```\n- skill:1: 2\n1. job:1 — 0\n```"
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_extracts_lines_amid_prose(self):
        raw = "Sure! Here are the ratings:\nskill:1 2\njob:1 0\nHope that helps."
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_partial_reply_keeps_complete_lines(self):
        # truncated mid-reply: skill:1 parses, the dangling line is ignored.
        self.assertEqual(self._scorer()._parse("skill:1 3\njob"), {"skill:1": 3})

    def test_unknown_ids_dropped_and_labels_clamped(self):
        raw = "skill:1 9\njob:1 0\nskill:999 2"
        # 9 -> clamped to _LABEL_MAX(3); unknown id dropped.
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 3, "job:1": 0})

    def test_garbage_returns_empty(self):
        self.assertEqual(self._scorer()._parse("no ratings here at all"), {})

    def test_ranked_entries_empty_on_parse_failure(self):
        with patch("jac.llm_prompts.complete", return_value="garbage"):
            self.assertEqual(self._scorer().ranked_entries(), [])

    def test_ranked_entries_empty_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._scorer().ranked_entries(), [])

    def test_ranked_entries_maps_labels(self):
        with patch("jac.llm_prompts.complete", return_value="skill:1 3\njob:1 1"):
            ranked = self._scorer().ranked_entries()
        self.assertEqual({r["id"]: r["score"] for r in ranked}, {"skill:1": 3, "job:1": 1})


class CVFilterRoutingTests(TestCase):
    """output() picks the right scorer + selection per grade, with fallback."""

    def _entries(self):
        return [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
        ]

    def test_standard_uses_ranked_selection(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="standard")
        with patch.object(
            CVFilter, "_standard_scores", return_value={"job:1": 3, "job:2": 1}
        ):
            out = f.output()
        # ranked by label desc; scores are the labels (not cosine).
        self.assertEqual([e["id"] for e in out["job"]], ["job:1", "job:2"])
        self.assertEqual(out["job"][0]["score"], 3)

    def test_standard_falls_back_to_light_when_scorer_empty(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="standard")
        with patch.object(CVFilter, "_standard_scores", return_value={}), patch.object(
            CVFilter, "_light_scores", return_value={"job:1": 0.9, "job:2": 0.2}
        ):
            out = f.output()
        # light path: floored selection, cosine scores preserved.
        self.assertEqual(out["job"][0]["score"], 0.9)

    def test_strong_currently_routes_through_standard_scorer(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(
            CVFilter, "_standard_scores", return_value={"job:1": 2, "job:2": 0}
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:1")
```

> `patch` and `CVFilter` are already imported at the top of `tests.py`. `Instruct` is **not** —
> add it to the existing `from jac.cv import ...` neighbours: `from jac.llm_prompts import Instruct`.

## Verification

From `backend/` with the `jac` virtualenv active.

**1. Static + unit tests:**

```bash
python manage.py check
python manage.py test jac.tests.CVSelectRankedTests jac.tests.InstructScorerParseTests \
  jac.tests.CVFilterRoutingTests -v 2
```

Expected: `check` clean, all new tests pass. Also re-run the existing selection tests to confirm the
`light` path is untouched:

```bash
python manage.py test jac.tests.CVSelectionTests jac.tests.CVFavouriteBonusTests -v 2
```

**2. Live, against a real Instruct model.** The default Ollama config is `llama3.2:1b` (a `light`
model), so to actually exercise the Instruct path point an alias at a mid model (≈7b, which
autodetects to `standard` — see `get_alias_strength`), e.g. pull `qwen2.5:7b-instruct` in Ollama and
set it as your user's `default` `LLMConfig`, then:

```bash
python manage.py cv_eval --user 1 --job-file data/test_job.md --grade standard --show-ranks
```

What "done" looks like:
- The run completes without falling back (scores show as small integers `0`–`3`, not `0.xxx`
  cosine values — that's how you know the Instruct rung ran and didn't degrade to light).
- Section counts **vary by posting** and differ from the `light` run on the same posting — a
  well-matched posting keeps more entries, a poorly-matched one fewer, never clamped to the
  one-page targets (which remain only the colour reference in the output).
- `min_keep` holds: jobs ≥ 3, skills ≥ 5, educations ≥ 2 even on a weak-fit posting; languages all
  present.
- Favourited entries are always present regardless of how the model rated them.

**3. Compare grades** to eyeball the difference between embedding-floor and LLM-verdict selection:

```bash
python manage.py cv_test --user 1 --job-file data/test_job.md --grades light standard
```

Expect the two exported CVs to diverge in which entries survive — that divergence is the rung
working, not a bug.

## Out of scope / next

- **`strong` (Conversational) holistic rung** — its own guide: the LLM returns a *selection*
  (chosen + ordered, with rationale), and the shared layer applies guardrails only (min_keep,
  favourites). Until then `strong` intentionally routes through this standard scorer.
- **Batching** — this guide scores all entries in one call. If a smaller `standard` model proves
  unreliable over the full set, a per-section variant (one call per entry type) is the fallback;
  the `_parse` / selection contract is unchanged, only `Instruct._prompt`/call loop would split.
