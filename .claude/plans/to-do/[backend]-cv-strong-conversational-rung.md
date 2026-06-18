# [backend] CV ladder — the `strong` (Conversational) rung

## Context / goal

Roadmap item **1**, final rung. With `light` (embeddings) and `standard` (Instruct labels) in place,
`strong` is the top rung: a **conversational LLM selects holistically** — it reads the posting and
every entry and returns the *chosen, ordered set* with a one-line rationale each. The deterministic
layer then does **guardrails only** (pin favourites, guarantee `min_keep`); no floors, no
propagation, no count clamp.

> **Depends on** `[backend]-cv-standard-instruct-rung.md` being implemented first — this guide reuses
> the `re` import, the `_SECTION_POLICY` `min_keep` reads, and the `output()` routing that guide
> introduced. Implement standard, then this.

### Why a different contract from `standard`

This is the payoff of the architecture decision (see the standard-rung guide): the deterministic
`_select()` machinery is a crutch for weak scorers. A conversational model's native strength *is*
holistic judgment — "these two projects tell one story, keep both; this skill is implied by that
kept job, drop it; lead with X." Reducing that to a per-entry scalar and re-deriving selection would
throw it away and double-count relationships the model already reasoned about.

So `strong` doesn't emit scores at all. It emits a **selection**: an ordered list of chosen entry
ids, each with a short `why`. The shared layer's only job is to enforce the two things the user's
intent outranks the model on — **favourites are pinned** and **`min_keep` floors hold** — plus the
standing rule that **languages are never dropped**. Everything else is the model's call, so
kept-count varies freely with fit (intended; see `selection-size-is-intentional`).

The rung still **degrades gracefully**: a failed/empty conversational selection falls back to the
standard scorer, and standard in turn falls back to light.

| rung | scorer emits | selection |
| --- | --- | --- |
| light (embed) | `{id: cosine}` | propagation + absolute floors — `_select` |
| standard (instruct) | `{id: label 0–3}` | keep-by-verdict — `_select_ranked` |
| **strong** (conversational) | **ordered `[{id, why}]`** | **guardrails only — `_select_holistic` (new)** |

Provider-agnostic, same as the other rungs, and **no JSON** (see the `no-json-llm-io` memory): the
model returns a **line format** — one kept id per line, best first, with a short reason after it
(`<id> — <why>`). The id pattern (`type:pk`) is the parse anchor; lines it can't read are skipped,
so a truncated reply still yields every complete pick in order. No provider-specific kwargs.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/llm_prompts.py` | implement the `Conversational` class — the strong-rung holistic selector |
| `backend/jac/cv.py` | replace `_strong_scores` stub with `_strong_selection`; add `_select_holistic`; extend `output()` cascade |
| `backend/jac/cv.py` (`CV.apply_selection`) | carry the per-entry `reason` onto surviving instances (`relevance_reason`) so the rationale isn't lost downstream |
| `backend/jac/tests.py` | offline tests for `_select_holistic`, `Conversational` parsing, and `output()` routing |

No migration, no API, no frontend. `cv_eval` / `cv_test` need no change — `strong` entries carry
`score=None`, which `cv_eval` already renders as `n/a` (it ranks by list order, not score).

## The code

### 1. `backend/jac/llm_prompts.py`

Imports are already in place from the standard-rung guide (`logging`, `re`, `complete`, `logger`).
Replace the empty `Conversational` stub (`class Conversational: pass`) with:

```python
class Conversational:
    """`strong` rung: a conversational LLM selects the CV holistically. It returns an
    ORDERED list of chosen entry ids (priority order, best first) each with a short `why`,
    rather than per-entry scores — CVFilter applies only guardrails (favourites, min_keep)
    on top, so the model's judgment drives the selection.

    Provider-agnostic (no provider-specific kwargs). The reply is a **line format**
    (`<id> — <why>`, one pick per line), not JSON — token-cheap and robust to truncation
    (see the `no-json-llm-io` memory). Any failure returns [] -> CVFilter degrades to the
    standard rung.
    """

    _INSTRUCTION = (
        "You are a senior CV editor tailoring a ONE-PAGE CV to a specific job posting.\n"
        "From the candidate's full entry list below, choose the entries that make the "
        "strongest, most relevant CV for THIS posting and drop the rest. Use judgment:\n"
        "  - prefer entries the posting actually calls for; drop weak or off-topic ones;\n"
        "  - keep a skill if a job/project you are keeping clearly relies on it;\n"
        "  - it is fine to keep few entries for a poorly-matched posting, or many for a "
        "strong match — fit should decide the count, not a fixed quota.\n"
        "Output the entries you are KEEPING, best first, ONE per line: the entry id, "
        "then ' — ', then a short reason (≤12 words).\n"
        "Example:\n"
        "job:2 — leads the relevant backend story\n"
        "skill:7 — required stack\n"
        "Include only ids you are keeping. No prose, no markdown, no other text."
    )
    _MAX_POST_CHARS = 12000

    # entry ids are  type:pk  (e.g. job:2); anchor on a leading id, the rest of the line is why.
    _PICK_RE = re.compile(r"([a-z]+:\d+)\s*[-—:.)\]]*\s*(.*)")

    def __init__(self, job_post_text: str, entries: list[dict], user=None):
        self.job_post_text = job_post_text
        self.entries = entries
        self.user = user

    def selection(self) -> list[dict]:
        """Return an ordered [{id, why}] of chosen entries. [] on any failure."""
        try:
            raw = complete(prompt=self._prompt(), user=self.user)
        except Exception:
            logger.exception("Conversational selector: LLM call failed")
            return []
        chosen = self._parse(raw)
        if not chosen:
            logger.warning("Conversational selector: no parseable selection in reply")
        return chosen

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"CANDIDATE ENTRIES (id — text):\n{self._grouped_entries()}\n\nSELECTION:"
        )

    def _grouped_entries(self) -> str:
        """List entries grouped by type for readability, ids verbatim."""
        by_type: dict[str, list[dict]] = {}
        for e in self.entries:
            by_type.setdefault(e["type"], []).append(e)
        blocks = []
        for etype, items in by_type.items():
            lines = "\n".join(f'  {e["id"]} — {e.get("text") or ""}' for e in items)
            blocks.append(f"{etype.upper()}S:\n{lines}")
        return "\n\n".join(blocks)

    def _parse(self, raw: str) -> list[dict]:
        """Extract an ordered [{id, why}] from a line format (`<id> — <why>`, best first).

        Scans each line for a leading id pattern, taking the rest of the line as the reason,
        and ignores lines it can't read (so a truncated reply still yields every complete pick
        in order). Keeps only ids in this entry set, de-dupes preserving order, truncates
        `why`. Returns [] when nothing usable is found.
        """
        valid = {e["id"] for e in self.entries}
        out: list[dict] = []
        seen: set[str] = set()
        for line in (raw or "").splitlines():
            m = self._PICK_RE.search(line)
            if not m:
                continue
            eid = m.group(1)
            if eid in valid and eid not in seen:
                seen.add(eid)
                out.append({"id": eid, "why": m.group(2).strip()[:200]})
        return out
```

### 2. `backend/jac/cv.py`

**2a. Replace the `_strong_scores` stub with `_strong_selection`.** The strong rung no longer
produces scores, so swap the method:

```python
    def _strong_selection(self) -> list[dict]:
        """Conversational holistic selection: ordered [{id, why}]. Empty -> standard fallback."""
        return Conversational(
            self.job_post_text, self.entries, user=self.user
        ).selection()
```

And extend the import to bring in `Conversational`:

```python
from jac.llm_prompts import Conversational, Embed, Instruct
```

**2b. Extend `output()`** to a full strong → standard → light cascade. Replace the method the
standard guide installed:

```python
    def output(self) -> dict:
        """Return {section: [entry dicts + score], ...}, each section ranked desc.

        Rungs differ in BOTH scorer and selection strategy, and each degrades to the next:
          - strong:   conversational LLM holistic selection (_select_holistic);
          - standard: Instruct-LLM relevance labels -> keep-by-verdict (_select_ranked);
          - light:    embedding cosine -> propagation + absolute floors (_select).
        """
        if self.grade == "strong":
            selected = self._strong_selection()
            if selected:
                return self._select_holistic(selected)
            # conversational selector failed -> degrade to standard
        if self.grade in ("standard", "strong"):
            labels = self._standard_scores()
            if labels:
                return self._select_ranked(labels)
        return self._select(self._light_scores())
```

**2c. Add `_select_holistic()`** next to `_select_ranked()`:

```python
    def _select_holistic(self, selected: list[dict]) -> dict:
        """Selection for the strong rung: trust the conversational model's chosen, ordered
        set and apply guardrails only — pin favourites, never drop languages, guarantee
        min_keep. No floors, no propagation, no count clamp (count tracks fit, by design).

        `selected` is an ordered [{id, why}]; entries absent from it are dropped, except as
        forced back by the guardrails. Surviving entries carry score=None (the rung emits no
        numeric score) and reason=<why>.
        """
        entry_by_id = {e["id"]: e for e in self.entries}
        why_by_id = {s["id"]: s["why"] for s in selected}

        by_section_all: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section_all.setdefault(e["type"], []).append(e)

        # the model's chosen entries, per section, in its priority order
        chosen_by_section: dict[str, list[dict]] = {}
        for s in selected:
            e = entry_by_id.get(s["id"])
            if e is not None:
                chosen_by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section_all.items():
            policy = self._SECTION_POLICY.get(section, {"min_keep": 0})
            min_keep = policy["min_keep"]

            keep = list(chosen_by_section.get(section, []))
            kept_ids = {e["id"] for e in keep}

            # favourites are a user override — pin any the model didn't pick.
            for e in items:
                if e.get("favourite") and e["id"] not in kept_ids:
                    keep.append(e)
                    kept_ids.add(e["id"])

            # languages are never dropped; otherwise top up to min_keep from the remainder
            # (natural order — date / name) without re-ordering the model's picks.
            if min_keep is None:
                for e in items:
                    if e["id"] not in kept_ids:
                        keep.append(e)
                        kept_ids.add(e["id"])
            elif len(keep) < min_keep:
                for e in items:
                    if e["id"] not in kept_ids:
                        keep.append(e)
                        kept_ids.add(e["id"])
                        if len(keep) >= min_keep:
                            break

            out[section] = [
                {**e, "score": None, "reason": why_by_id.get(e["id"], "")} for e in keep
            ]
        return out
```

**2d. Carry the rationale through `apply_selection`.** The strong rung's value is the `why`; today
`apply_selection` only reads `score`. Add one line so the reason lands on the instance for
downstream render / cover-letter use. In `CV.apply_selection`, alongside the existing
`obj.relevance_score = item.get("score")`:

```python
                obj.relevance_score = item.get("score")
                obj.relevance_reason = item.get("reason")
                pruned[section].append(obj)
```

> Harmless for the other rungs — `Embed` / `Instruct` already include `reason` (empty string) in
> their output dicts, and the floored/ranked selection paths pass entries through with that key
> intact, so `relevance_reason` is just `""` there.

## Tests

Add to `backend/jac/tests.py`. Offline throughout — `_select_holistic` is pure, and the selector is
exercised with `complete` patched.

```python
class CVSelectHolisticTests(TestCase):
    """CVFilter._select_holistic: model's selection + guardrails (favourites, min_keep, langs)."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="strong")

    def _sel(self, *ids):
        return [{"id": i, "why": f"why {i}"} for i in ids]

    def test_keeps_selected_in_order_drops_rest(self):
        # projects: min_keep 0 -> unselected are genuinely dropped.
        entries = [
            {"id": f"project:{i}", "type": "project", "text": "", "refs": [], "favourite": False}
            for i in range(1, 4)
        ]
        out = self._filter(entries)._select_holistic(self._sel("project:3", "project:1"))
        self.assertEqual([e["id"] for e in out["project"]], ["project:3", "project:1"])

    def test_reason_carried_and_score_none(self):
        entries = [
            {"id": "project:1", "type": "project", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_holistic(self._sel("project:1"))
        self.assertEqual(out["project"][0]["reason"], "why project:1")
        self.assertIsNone(out["project"][0]["score"])

    def test_favourite_pinned_when_model_omits_it(self):
        entries = [
            {"id": "project:1", "type": "project", "text": "", "refs": [], "favourite": False},
            {"id": "project:2", "type": "project", "text": "", "refs": [], "favourite": True},
        ]
        out = self._filter(entries)._select_holistic(self._sel("project:1"))
        kept = {e["id"] for e in out["project"]}
        self.assertEqual(kept, {"project:1", "project:2"})

    def test_min_keep_tops_up_from_remainder(self):
        # jobs min_keep 3; model picks only 1 -> two more topped up from natural order.
        entries = [
            {"id": f"job:{i}", "type": "job", "text": "", "refs": [], "favourite": False}
            for i in range(1, 5)
        ]
        out = self._filter(entries)._select_holistic(self._sel("job:2"))
        kept = [e["id"] for e in out["job"]]
        self.assertEqual(kept[0], "job:2")          # model's pick stays first
        self.assertEqual(len(kept), 3)              # topped up to min_keep

    def test_count_varies_with_fit_no_clamp(self):
        # skills min_keep 5; model picks 7 -> all 7 kept (never clamped to a target).
        entries = [
            {"id": f"skill:{i}", "type": "skill", "text": "", "refs": [], "favourite": False}
            for i in range(1, 9)
        ]
        out = self._filter(entries)._select_holistic(self._sel(*[f"skill:{i}" for i in range(1, 8)]))
        self.assertEqual(len(out["skill"]), 7)

    def test_languages_never_dropped(self):
        entries = [
            {"id": f"language:{i}", "type": "language", "text": "", "refs": [], "favourite": False}
            for i in range(1, 3)
        ]
        out = self._filter(entries)._select_holistic(self._sel("language:1"))
        self.assertEqual({e["id"] for e in out["language"]}, {"language:1", "language:2"})


class ConversationalSelectorTests(TestCase):
    """Conversational._parse / selection(): tolerant, validating, ordered — no network."""

    def _selector(self):
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:1", "type": "job", "text": "Dev at X"},
        ]
        return Conversational("posting", entries)

    def test_parses_ordered_selection(self):
        raw = "job:1 — core\nskill:1 — req"
        self.assertEqual(
            self._selector()._parse(raw),
            [{"id": "job:1", "why": "core"}, {"id": "skill:1", "why": "req"}],
        )

    def test_tolerates_markdown_and_extracts_amid_prose(self):
        # bullets, code fences, a reasonless pick, and an unknown id -> only valid kept.
        raw = "Here is my pick:\n```\n- skill:1\n2. skill:999 — x\n```"
        self.assertEqual(self._selector()._parse(raw), [{"id": "skill:1", "why": ""}])

    def test_dedupes_preserving_order(self):
        raw = "job:1 — a\njob:1 — b\nskill:1 — c"
        self.assertEqual(
            [s["id"] for s in self._selector()._parse(raw)], ["job:1", "skill:1"]
        )

    def test_partial_reply_keeps_complete_picks(self):
        # truncated mid-reply: job:1 parses in order, dangling line ignored.
        self.assertEqual(
            self._selector()._parse("job:1 — core\nski"), [{"id": "job:1", "why": "core"}]
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(self._selector()._parse("no picks here"), [])

    def test_selection_empty_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._selector().selection(), [])


class CVFilterStrongRoutingTests(TestCase):
    """output() strong path: holistic when available, else standard, else light."""

    def _entries(self):
        return [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
        ]

    def test_strong_uses_holistic_selection(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(
            CVFilter, "_strong_selection", return_value=[{"id": "job:2", "why": "best"}]
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:2")
        self.assertEqual(out["job"][0]["reason"], "best")

    def test_strong_falls_back_to_standard(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(CVFilter, "_strong_selection", return_value=[]), patch.object(
            CVFilter, "_standard_scores", return_value={"job:1": 3, "job:2": 1}
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:1")
        self.assertEqual(out["job"][0]["score"], 3)  # standard labels, not holistic

    def test_strong_falls_back_to_light_when_both_empty(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(CVFilter, "_strong_selection", return_value=[]), patch.object(
            CVFilter, "_standard_scores", return_value={}
        ), patch.object(
            CVFilter, "_light_scores", return_value={"job:1": 0.9, "job:2": 0.2}
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["score"], 0.9)  # cosine -> light path
```

> `Conversational` needs importing in `tests.py`: `from jac.llm_prompts import Conversational, Instruct`
> (extend the line the standard guide added). `patch` and `CVFilter` are already imported.

## Verification

From `backend/` with the `jac` virtualenv active.

**1. Static + unit tests:**

```bash
python manage.py check
python manage.py test jac.tests.CVSelectHolisticTests jac.tests.ConversationalSelectorTests \
  jac.tests.CVFilterStrongRoutingTests -v 2
```

Then re-run the lower rungs to confirm nothing regressed (the `apply_selection` change touches them):

```bash
python manage.py test jac.tests.CVSelectionTests jac.tests.CVSelectRankedTests \
  jac.tests.CVApplySelectionTests -v 2
```

**2. Live, against a real conversational model.** `strong` autodetects from model size (>14b →
`strong`; see `get_alias_strength`), but any capable instruct/chat model works for a smoke test.
Point your user's `default` `LLMConfig` at a solid conversational model (e.g. a 14b+ Ollama model,
or set `"strength": "strong"` explicitly on a smaller one to force the rung), then:

```bash
python manage.py cv_eval --user 1 --job-file data/test_job.md --grade strong --show-ranks
```

What "done" looks like:
- The run completes without falling back — entry scores show as `n/a` (the strong rung emits no
  numeric score; that's the tell it ran holistically, not via standard/light).
- The selection reads like a deliberately edited CV: tightly relevant entries, off-topic ones gone,
  count tracking fit (fewer on a weak-match posting, more on a strong one) — never clamped.
- Guardrails hold: jobs ≥ 3, skills ≥ 5, educations ≥ 2; all languages present; every favourite
  present regardless of the model's picks.

**3. Compare all three rungs** on one posting to see the ladder:

```bash
python manage.py cv_test --user 1 --job-file data/test_job.md --grades light standard strong
```

Expect three meaningfully different CVs — embedding-floor vs label-verdict vs holistic-edit. The
divergence is the ladder working.

## Out of scope / next

- The `why` rationale now rides on `relevance_reason`; **surfacing it** in `CvRender` and feeding it
  to the cover-letter generator belong to roadmap items 2–3, not here.
- **Batching / context limits** — like standard, this scores all entries in one call. If the entry
  set ever outgrows a model's context, chunked selection (rank within chunks, then a final merge
  pass) is the fallback; the `selection()` → `_select_holistic` contract stays the same.
