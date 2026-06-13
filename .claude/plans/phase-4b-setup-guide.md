# Phase 4b — SLM-robust CV pipeline

> Setup guide, authored before the code. First of two slices that close the
> Phase-4b "CV pipeline refinement" gap, driven by the `cv_test` dogfooding
> finding that the pipeline only works on a fast paid model. Follow top to
> bottom; every step ends with a **Verify** you must pass before moving on.
> The iterative breadth loop is the **next** slice
> ([phase-4b-bis-setup-guide.md](phase-4b-bis-setup-guide.md)) — don't pull it
> forward.

## 1. Goal

By the end, `python manage.py cv_test --user 1 --job-file data/test_job.md` returns
a **populated CV from the local Ollama model** (`default` and `ollama` aliases) —
**without timing out and without a JSON-parse failure** — instead of the two hard
failures we hit dogfooding. The paid `opeani` pass is unchanged.

Three changes get us there:
1. **Line-oriented LLM protocol (no JSON)** — every wrapper's *output* becomes
   self-contained, id-first plain-text lines instead of a JSON envelope, so the
   trailing-comma / stray-brace / unterminated-string failures small models emit
   are structurally impossible. This **replaces** the original "add `json-repair`"
   plan: the LLM only ever needs to echo the entry's `type:pk` id back (we
   re-hydrate everything else from the DB), so there is nothing JSON-shaped to
   carry — and we add **no third-party parsing dependency** for semi-trusted LLM
   output.
2. **Capability-tiered fallback** — a per-config `strength` hint routes weak models
   to the cheap keyword tier *first*, so they stop burning minutes failing the heavy
   per-entry-scoring tiers they can't do.
3. **Token diet** — feed CV entries as compact `id | text` lines, not `json.dumps`;
   the output protocol (change 1) mirrors it on the way back.

> **Why no `json-repair`.** An LLM response is semi-trusted input; adding a package
> whose only job is to parse it is exactly the supply-chain surface worth avoiding.
> A line protocol is strictly *more* robust than JSON+repair for small models —
> each row is independently skippable and id-first, so one mangled row never loses
> the whole response (one bad brace in a JSON array does).

This slice explicitly does **not** add the iterative distill loop (4b-bis), the
`builds_on`/`related_skills` closure, the cover letter (4c), or German output (4d).

## 2. Preflight

Stop and fix if any check fails.

- **Clean tree at HEAD.** `git log --oneline -1` → `3e53311 pagination, skill ux,
  bug fixes`; `git status` clean (commit/stash the in-flight plan-archive churn first).
- **Suite green.** From `backend/`: `python manage.py test` → `Ran N tests … OK`.
  Record N here: ____ (it ticks up at the end of this slice).
- **A real CV exists for user 1.** `python manage.py shell -c "from jac.cv import CV;
  cv=CV(user_pk=1); print({k: len(v) for k,v in cv.entries.items()})"` → non-trivial
  counts (the dogfood run already confirmed ~19 selected, so the DB is populated).
- **Reproduce the failure.** `python manage.py cv_test --user 1 --job-file
  data/test_job.md` → confirm the `default`/`ollama` passes still fail (timeout /
  invalid JSON). This is the baseline this slice fixes.

## 3. The contract you're coding against

- **Wrapper output is line-oriented plain text, never JSON.** Each wrapper in
  [backend/jac/llm.py](../../backend/jac/llm.py) parses its completion with a small
  stdlib-only `_parse_*` helper (`_parse_keyword_lines`, `_parse_scored_lines`,
  `_parse_selection_lines`, `_parse_analysis_block`) that fence-strips, splits on
  lines, and tolerates malformed rows. The old `_parse_json` is gone, along with
  `import json`. The load-bearing contract is only that the model echoes each
  entry's `type:pk` id back verbatim — `cv.py` re-hydrates the rest from the DB.
- **Config resolution.** `get_alias_config(alias, user=)` in
  [backend/llm_connector/conf.py](../../backend/llm_connector/conf.py#L47) returns a
  flat config dict — from `LLMConfig.to_config_dict()` for per-user rows (which spreads
  `extra` in), or `settings.LLM[alias]` for the `default` fallback. The custom adapter
  forwards **unknown** config keys into the HTTP payload
  ([custom.py:42](../../backend/llm_connector/providers/custom.py#L42)); the OpenAI
  adapter ignores unknown keys ([openai.py:52](../../backend/llm_connector/providers/openai.py#L52)).
- **The production entry point.** `CV.ai_tailor_with_fallback(job_text, llm, threshold,
  language)` in [backend/jac/cv.py:595](../../backend/jac/cv.py#L595) — a ladder of
  try/except tiers, each restoring the pre-call snapshot on failure, returning
  `{"tier", "selection", "keywords"}`.

> Two non-obvious choices: (1) the capability hint is a **config-dict key**
> (`strength`), not a new `LLMConfig` field — because the local default lives in the
> `settings.LLM["default"]` *dict*, which has no model fields, and we want one
> mechanism for both resolution paths. (2) The custom adapter must **strip** `strength`
> so it isn't POSTed to Ollama as a bogus body key (harmless there, but sloppy, and a
> strict server would 400).

## 4. Stack additions

**None.** The original draft added `json-repair` to salvage malformed JSON; the
line-protocol approach removes the need for it (and for `import json` in `llm.py`).
No new dependency — that is the point. `requirements.txt` is untouched.

## 5. The changes, in order

### 5a. Line-oriented output protocol — [backend/jac/llm.py](../../backend/jac/llm.py)

Drop `import json` and `_parse_json` entirely. Add stdlib-only line parsers, each
tolerant (a malformed row is skipped, never fatal) and **id-first** (the id leads
every row, so a garbled score/reason still yields the pick). The output formats:

| Wrapper | Output (one row per line) |
|---|---|
| `extract_job_keywords` | one keyword per line |
| `score_entries_for_job` | `id \| score \| reason` |
| `score_entries_with_analysis` | `id \| score \| reason` |
| `tailor_cv_conversationally` | `id \| reason` (most compelling first) |
| `analyze_job` | labeled block (`ROLE:` / `MUST_HAVE:` + `- item` bullets / …) |

```python
import re

from llm_connector import complete

_FENCE_LINE = re.compile(r"^```[A-Za-z0-9]*$")
_LIST_MARKER = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def _clean_lines(raw: str) -> list[str]:
    """Split into non-empty, stripped lines, dropping any code-fence lines."""
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or _FENCE_LINE.match(s):
            continue
        out.append(s)
    return out


def _parse_scored_lines(raw: str) -> list[dict]:
    """`id | score | reason` rows. id-first + tolerant: junk score -> 0.0, a '|'
    inside the reason survives (split maxsplit=2), rows without an id are skipped."""
    out = []
    for line in _clean_lines(raw):
        line = _LIST_MARKER.sub("", line).strip()
        if "|" not in line:
            continue
        parts = line.split("|", 2)
        sid = parts[0].strip()
        if not sid:
            continue
        try:
            score = float(parts[1].strip())
        except (IndexError, ValueError):
            score = 0.0
        reason = parts[2].strip() if len(parts) > 2 else ""
        out.append({"id": sid, "score": score, "reason": reason})
    return out
```

(`_parse_keyword_lines`, `_parse_selection_lines`, and `_parse_analysis_block`
follow the same shape — see the committed [backend/jac/llm.py](../../backend/jac/llm.py).)
The input side mirrors the output: `_entries_block(entries)` renders `id | text`
lines and `_analysis_block(analysis)` renders the labeled block, so `llm.py` carries
no JSON in either direction. Each wrapper's system prompt is rewritten to specify its
line format and forbid JSON/markdown.

**Verify:**
```bash
python manage.py shell -c "
from jac.llm import _parse_scored_lines, _parse_keyword_lines, _parse_analysis_block
print(_parse_scored_lines('skill:1 | 0.9 | direct\nGARBAGE NO PIPE\njob:2 | 0.5 | x'))  # junk row skipped
print(_parse_keyword_lines('- Python\nDjango\nPython'))                                  # markers + dedupe
print(_parse_analysis_block('ROLE: Eng\nMUST_HAVE:\n- Python\nSUMMARY: x'))              # labeled block
"
```
The middle row of the first call is dropped (not fatal); keywords dedupe and lose
their list markers; the analysis block parses with every key present.

### 5b. Capability-strength resolver — [backend/llm_connector/conf.py](../../backend/llm_connector/conf.py)

```python
_STRENGTHS = {"light", "standard", "strong"}


def get_alias_strength(alias: str = "default", user=None) -> str:
    """Pipeline capability hint for an alias: 'light' | 'standard' | 'strong'.

    Read from the resolved config dict (LLMConfig.extra for per-user rows, the
    settings.LLM dict for the default). Defaults to 'strong' when unset, which
    preserves the current full ladder for any alias you don't explicitly tag.
    """
    try:
        config = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — missing/broken config -> safe default
        return "strong"
    strength = config.get("strength", "strong")
    return strength if strength in _STRENGTHS else "strong"
```

> Default is **'strong'** on purpose: it reproduces today's conversational-first ladder
> for any untagged alias, so existing tests and the paid `opeani` config keep their
> current behaviour with zero changes. We opt *weak* models *down* to `light`.

### 5c. Strip the pipeline-only key — [backend/llm_connector/providers/custom.py](../../backend/llm_connector/providers/custom.py)

Line 42 — add `"strength"` so it's consumed, not forwarded into the Ollama payload:

```python
        _known = {"provider", "url", "model", "api_key", "max_tokens", "timeout", "strength"}
```

### 5d. Capability-tiered fallback — [backend/jac/cv.py](../../backend/jac/cv.py)

Replace the body of `ai_tailor_with_fallback` with an ordered ladder chosen by
`strength`. Each tier is a closure that returns the result dict on success (non-empty
entries) or `None` after restoring the snapshot:

```python
    def ai_tailor_with_fallback(
        self,
        job_text: str,
        llm: str = "default",
        threshold: float = 0.25,
        language: str | None = None,
    ) -> dict:
        """Tier the tailoring pipeline to the model's capability.

        The ladder is chosen by the alias's `strength` hint (see
        llm_connector.conf.get_alias_strength):

          strong   : conversational -> filter -> keyword -> deterministic
          standard : filter -> keyword -> deterministic
          light    : keyword -> deterministic        (cheap output a SLM can do)

        Every ladder ends in an `unfiltered` fallthrough. Each failed tier
        restores the pre-call snapshot so partial mutations don't leak. Returns
        {"tier", "selection" (tier 1 only), "keywords" (keyword/deterministic only)}.
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

        def tier_keyword():
            try:
                keywords = self.ai_keyword_filter(job_text, llm=llm)
                if any(self.entries.values()):
                    return {"tier": "keyword", "selection": None, "keywords": keywords}
            except Exception:
                logger.warning("ai_tailor_with_fallback: keyword failed", exc_info=True)
            restore()
            return None

        def tier_deterministic():
            try:
                keywords = self.deterministic_filter(job_text, language=language)
                if any(self.entries.values()):
                    return {"tier": "deterministic", "selection": None, "keywords": keywords}
            except Exception:
                logger.warning("ai_tailor_with_fallback: deterministic failed", exc_info=True)
            restore()
            return None

        ladders = {
            "strong":   [tier_conversational, tier_filter, tier_keyword, tier_deterministic],
            "standard": [tier_filter, tier_keyword, tier_deterministic],
            "light":    [tier_keyword, tier_deterministic],
        }
        strength = get_alias_strength(llm, user=self.user)
        ladder = ladders.get(strength, ladders["strong"])
        logger.debug("ai_tailor_with_fallback: strength=%s (%d tiers)", strength, len(ladder))

        for tier in ladder:
            result = tier()
            if result is not None:
                return result

        logger.info(
            "ai_tailor_with_fallback: every tier filtered too strictly — returning unfiltered CV"
        )
        return {"tier": "unfiltered", "selection": None, "keywords": None}
```

> `self.user` is the user **pk** (an int). `get_alias_strength(llm, user=self.user)` →
> `get_alias_config(..., user=<pk>)` → `LLMConfig.objects.get(user=<pk>, alias=...)`,
> which Django resolves as `user_id=<pk>`. No change needed there.

### 5e. Token diet — compact entry rendering — [backend/jac/llm.py](../../backend/jac/llm.py)

Add a helper and use it in all three entry-list builders instead of `json.dumps`:

```python
def _entries_block(entries: list[dict]) -> str:
    """Render entries as compact `id | text` lines (not JSON): ~30-40% fewer
    tokens and easier for small models to read. The id leads each line so the
    model can echo it verbatim in its JSON output."""
    return "\n".join(f"{e.get('id')} | {e.get('text', '')}" for e in entries)
```

Then in `score_entries_for_job`, `score_entries_with_analysis`,
`tailor_cv_conversationally`, swap the `user_msg` CV block, e.g.:

```python
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"CV ENTRIES (one per line, `id | summary`):\n{_entries_block(entries)}"
    )
```
(and in `score_entries_with_analysis` the `JOB ANALYSIS:` line is rendered by
`_analysis_block(analysis)`, not `json.dumps`; in `tailor_cv_conversationally` label
it `CAREER DATABASE`). With change 5a the model also **returns** line-oriented text,
so both directions are JSON-free.

### 5f. Tag the local models `light` (config — Django admin + settings)

No code, but load-bearing. Two configs to tag — **no JSON-mode**: the wire format is
the line protocol, and forcing `response_format: json_object` would fight it.

- **`settings.LLM["default"]`** ([settings.py:174](../../backend/lukehirsch/settings.py#L174)) —
  longer timeout + `strength`:
  ```python
  LLM = {
      "default": {
          "provider": "custom",
          "url": os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
          "model": "qwen3.5:0.8b",
          "timeout": 300,        # was 120; SLMs are slow
          "think": False,
          "strength": "light",   # consumed by get_alias_strength; stripped from the payload
      },
  }
  ```
- **User 1's `ollama` LLMConfig** (Django admin → LLM connector → LLM configs) — set
  `extra`:
  ```json
  {"think": false, "timeout": 300, "strength": "light"}
  ```
  and set `max_tokens` (the real field, e.g. 2000) to bound generation.

**Verify:**
```bash
python manage.py llm_check --user 1 ollama   # OK, and noticeably faster than before
```

## 6. Per-step Verify blocks

- After 5a: the `_parse_*` shell snippets parse (and skip the junk row) instead of raising.
- After 5b–5d: `python manage.py shell -c "from jac.cv import CV;
  cv=CV(user_pk=1); print(cv.ai_tailor_with_fallback('Python Django backend engineer',
  llm='ollama'))"` → returns a dict whose `tier` is `keyword` or `deterministic` (the
  light ladder), not `conversational`.
- After 5e: re-run the above; the request logged in `LLMRequestLog` shows the compact
  `id | summary` block in *and* `id | … | reason` lines out — no JSON either way.
- After 5f: `llm_check --user 1 ollama` is green and faster.

## 7. End-to-end verification — the full loop

```bash
python manage.py cv_test --user 1 --job-file data/test_job.md
```
1. The **`default`** pass returns a non-empty CV (no "invalid JSON" — there is no
   JSON to be invalid) and **completes** (light ladder = keyword-first, no per-entry
   scoring marathon).
2. The **`ollama`** pass returns a non-empty CV without timing out.
3. The **`opeani`** pass is unchanged (untagged → `strong` ladder → full quality).
4. Re-run once more to confirm stability (model warm-load can dominate the first call).

## 8. What you should have at the end

```
backend/jac/llm.py                              # line parsers (no JSON) + _entries_block/_analysis_block + rewritten wrappers
backend/jac/cv.py                               # capability-tiered ai_tailor_with_fallback
backend/llm_connector/conf.py                   # + get_alias_strength()
backend/llm_connector/providers/custom.py       # strip "strength" from payload
backend/lukehirsch/settings.py                  # default tagged light (no JSON mode)
requirements.txt                                # UNCHANGED (no json-repair)
```

Add tests (bump N): the `_parse_*` line parsers (a junk row is skipped, a bare id
yields a pick, a `|` in the reason survives, the analysis block parses); each wrapper
reformatted to feed `complete` line/labeled fixtures; `get_alias_strength` reads
`strength` and defaults to `strong`; `ai_tailor_with_fallback` with a mocked
`get_alias_strength="light"` skips conversational/filter and lands on keyword (mock
`extract_job_keywords` to populate entries). Re-run `python manage.py test` (green),
then commit code + this guide:

```
Phase 4b: SLM-robust CV pipeline (line-oriented LLM protocol, capability-tiered fallback, compact render)
```

## 9. Known gaps to revisit

- **Iterative breadth loop → 4b-bis.** The keyword tier here is single-shot; the
  count-targeted distill→broaden/narrow loop is the next slice and *replaces* the
  keyword rung in the light ladder.
- **`builds_on`/`related_skills` closure → later micro-slice.** Only if 4b-bis
  findings justify it.
- **`max_tokens` truncation risk.** Capping output can truncate the scoring rows
  mid-stream. The line protocol degrades gracefully — a half-written final row is
  skipped and every complete row above it still counts — but for `strong` models keep
  the cap generous so you don't silently lose the tail of the ranking.

## 10. What's next

**Phase 4b-bis — iterative breadth-controlled distill loop**
([phase-4b-bis-setup-guide.md](phase-4b-bis-setup-guide.md)): turn the single-shot
keyword tier into a multi-round loop that re-distills the posting broader when too few
entries survive and stricter when too many, converging on a target band.
