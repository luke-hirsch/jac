# Plan: Hook up LLMs to the CV class

## Context

The `CV` class in [backend/jac/cv.py](backend/jac/cv.py) already has two deterministic methods that operate on `self.entries`:

- `extract_keywords(text)` — finds CV vocabulary terms in a job posting (returns list).
- `deterministic_filter(keywords)` — keeps only entries that match at least one keyword (mutates `self.entries`).

A stub `ai_filter_entries()` sits at the bottom of the file. The `llm_connector` app is fully built and configured (aliases: `default` → claude-sonnet-4-6, `fast` → gpt-4o-mini, `local` → Ollama, `gemini`), but **no JAC code calls it yet**, and [backend/jac/llm.py](backend/jac/llm.py) does not exist (CLAUDE.md says it should).

This plan adds AI-driven companions to the deterministic methods in a layered way:

1. **Non-agentic layer**: single-call LLM methods that mirror the deterministic ones. A small/fast model (gpt-4o-mini) can serve them.
2. **Agentic layer**: multi-step methods built on top of the non-agentic primitives, intended for reasoning models. These analyze the job posting first, then use that analysis to drive ranking/filtering with much richer context.

All AI methods accept an `llm: str = "default"` argument that maps directly to `llm_connector` aliases — callers can escalate from `"fast"` (SLM) to `"default"` (Sonnet) to a new `"reasoning"` alias without touching the method bodies.

Design decisions confirmed with the user:
- Prompt templates and `llm_connector` calls live in a new `backend/jac/llm.py`. CV methods stay thin and delegate.
- AI filter/rank methods **mutate `self.entries` in place**, matching `deterministic_filter`'s style.
- Ranking attaches transient `.relevance_score` (0.0–1.0) and `.relevance_reason` (str) attributes onto the Django model instances — no DB writes.
- The SLM keyword extractor returns **free-form keywords** (whatever the model judges important from the job text), complementing the existing vocabulary-bound `extract_keywords`.

---

## File-by-file changes

### 1. New: `backend/jac/llm.py`

Single home for all JAC prompt templates and `llm_connector` calls. Pure functions — no Django model objects in/out at this layer; the CV class is responsible for marshalling entries into dicts and applying results back to models.

```python
"""JAC-specific LLM prompt wrappers. All calls go through llm_connector."""

import json
from llm_connector import complete


# ---------- Non-agentic primitives (single LLM call each) ----------

def extract_job_keywords(job_text: str, llm: str = "fast") -> list[str]:
    """Free-form keyword extraction from a job posting. SLM-friendly."""

def score_entries_for_job(
    job_text: str,
    entries: list[dict],
    llm: str = "default",
) -> list[dict]:
    """Score each entry's relevance to the job.

    entries:  [{"id": <opaque>, "type": "skill"|"job"|..., "text": "<summary>"}, ...]
    returns:  [{"id": ..., "score": float, "reason": str}, ...]  one per input entry
    """

def analyze_job(job_text: str, llm: str = "default") -> dict:
    """Structured analysis of a job posting.

    returns: {
        "role_title": str,
        "seniority": str,                  # e.g. "junior" | "mid" | "senior" | "lead"
        "must_have": [str, ...],           # hard requirements
        "nice_to_have": [str, ...],
        "domains": [str, ...],
        "soft_skills": [str, ...],
        "company_signals": [str, ...],     # culture/values hints
        "summary": str,                    # 2-3 sentence summary
    }
    """
```

Implementation notes:
- All three call `llm_connector.complete(messages=..., alias=llm, ...)`.
- `score_entries_for_job` and `analyze_job` request JSON output via system prompt ("respond with valid JSON only, no prose"), then `json.loads()`. Wrap in try/except and re-raise with the raw response in the message for debuggability — do not silently fall back.
- The `id` field in `score_entries_for_job` is opaque; CV class will pass `f"{type}:{pk}"` so it can route scores back to the right model instance.
- Keep prompts short and explicit. Example system for the scorer: "You score CV entries by relevance to a job posting. Return a JSON array; each element has id, score (0.0–1.0), and reason (one sentence)."

### 2. Edit: `backend/jac/cv.py`

Replace the `ai_filter_entries()` stub with a real layered API. Add helper that serialises entries to the dict shape `jac.llm` expects.

**New imports** at the top:
```python
from jac import llm as jac_llm
```

**New private helper** — single source of truth for "how do we describe an entry to an LLM":

```python
def _entries_for_llm(self) -> list[dict]:
    """Flatten self.entries into [{id, type, text}, ...] for LLM scoring."""
```

Builds one dict per entry across all six categories. `id = f"{type}:{pk}"`. `text` is a short human-readable summary (e.g. for a Job: `"{title} at {company} ({started}-{ended}): {description[:300]} | skills: ..."`). Used by both filtering and ranking.

**Replace the stub with three non-agentic methods**:

```python
# AI methods (single LLM call) -----------------------------------------

def ai_extract_keywords(self, text: str, llm: str = "fast") -> list[str]:
    """Free-form keyword extraction via LLM. Complements extract_keywords()."""
    return jac_llm.extract_job_keywords(text, llm=llm)

def ai_filter_entries(self, job_text: str, threshold: float = 0.4, llm: str = "default") -> None:
    """Score every entry against the job posting, drop those below threshold.

    Mutates self.entries in place. Calls jac_llm.score_entries_for_job once
    with all entries in a single batch.
    """

def ai_rank_entries(self, job_text: str, llm: str = "default") -> None:
    """Score every entry, attach .relevance_score and .relevance_reason,
    and reorder each list in self.entries by score (descending).

    Mutates self.entries in place. Single LLM call.
    """
```

Both `ai_filter_entries` and `ai_rank_entries`:
1. Call `self._entries_for_llm()` to get the flat list.
2. Call `jac_llm.score_entries_for_job(job_text, flat, llm=llm)`.
3. Build a `scores: dict[str, tuple[float, str]]` keyed by `id`.
4. Walk `self.entries`, looking up each model by `f"{type}:{pk}"`. Attach `relevance_score` / `relevance_reason` attrs; for filter, drop where score < threshold; for rank, sort each list by score descending.

Sharing this score-then-apply path between filter and rank avoids duplicating the marshalling logic. If we want to be fancy we can factor out a private `_score_entries(job_text, llm)` that both call, but two short methods is fine for now.

**Add agentic methods** below the non-agentic ones:

```python
# Agentic methods (multi-step, reasoning-model friendly) ---------------

def ai_analyze_job(self, job_text: str, llm: str = "default") -> dict:
    """Return structured analysis of the job posting. See jac_llm.analyze_job."""
    return jac_llm.analyze_job(job_text, llm=llm)

def agentic_tailor(self, job_text: str, llm: str = "default", threshold: float = 0.4) -> dict:
    """Full pipeline: analyze job, then filter+rank entries using the analysis.

    Steps:
      1. analysis = jac_llm.analyze_job(job_text, llm=llm)
      2. Build an enriched scoring prompt that includes the analysis (must-haves,
         seniority, etc.) as system context.
      3. Score entries against that enriched context (one LLM call).
      4. Mutate self.entries: filter below threshold, sort by score, attach
         .relevance_score / .relevance_reason.
      5. Return the analysis dict (caller may want it for the cover letter).

    Best paired with a reasoning model alias (see Settings change below).
    """
```

`agentic_tailor` requires a second prompt template in `jac/llm.py`:
```python
def score_entries_with_analysis(
    job_text: str,
    analysis: dict,
    entries: list[dict],
    llm: str = "default",
) -> list[dict]:
```
…which is `score_entries_for_job` plus the structured analysis embedded into the system prompt. This is where a reasoning model earns its keep: it can weigh must-haves vs nice-to-haves and explain trade-offs in `reason`.

### 3. Edit: `backend/lukehirsch/settings.py`

Add a `"reasoning"` alias so callers can escalate to extended thinking without changing code:

```python
LLM = {
    "default": { ... },
    "fast": { ... },
    "local": { ... },
    "gemini": { ... },
    "reasoning": {
        "provider": "anthropic",
        "api_key": os.getenv("ANTHROPIC_API_KEY"),
        "model": "claude-opus-4-7",
        "max_tokens": 8192,
    },
}
```

(Extended-thinking kwargs like `thinking={"type": "enabled", "budget_tokens": ...}` can be passed per-call via `**kwargs` since `llm_connector.complete` forwards them. We don't bake this into the alias because not every reasoning call needs thinking enabled.)

---

## Typical call sequences

**Deterministic only (existing, unchanged):**
```python
cv = CV(user_pk=1)
keywords = cv.extract_keywords(job_text)
cv.deterministic_filter(keywords)
```

**Non-agentic AI (SLM extracts keywords, default model ranks):**
```python
cv = CV(user_pk=1)
cv.deterministic_filter(cv.ai_extract_keywords(job_text))   # narrow with SLM
cv.ai_rank_entries(job_text)                                # rank survivors
```

**Agentic (one call, reasoning model):**
```python
cv = CV(user_pk=1)
analysis = cv.agentic_tailor(job_text, llm="reasoning", threshold=0.5)
# cv.entries is now filtered, ranked, and annotated with .relevance_score
# analysis is structured job intel for cover-letter generation
```

---

## Verification

1. **Connectivity**: `cd backend && python manage.py llm_check` — confirms all aliases including new `reasoning` one resolve and round-trip.
2. **Unit-ish smoke test via shell**:
   ```bash
   cd backend && python manage.py shell
   >>> from jac.cv import CV
   >>> cv = CV(user_pk=1)
   >>> kws = cv.ai_extract_keywords("Senior Python engineer with Django + Postgres...", llm="fast")
   >>> assert isinstance(kws, list) and all(isinstance(k, str) for k in kws)
   >>> cv.ai_rank_entries("Senior Python engineer with Django + Postgres...")
   >>> top_job = cv.entries["jobs"][0]
   >>> print(top_job.title, top_job.relevance_score, top_job.relevance_reason)
   ```
3. **Agentic pipeline**:
   ```python
   >>> analysis = cv.agentic_tailor(job_text, llm="reasoning")
   >>> assert "must_have" in analysis
   >>> assert all(hasattr(s, "relevance_score") for s in cv.entries["skills"])
   ```
4. **Check `LLMRequestLog`** in admin to confirm requests are being logged with the correct alias.

---

## What this plan does NOT do

- No new Django models (no `JobPosting`, no `Application`, no persistence of analyses — those land in a later plan).
- No API views or DRF endpoints — only the CV class API. Wiring to `/api/jac/` comes later.
- No streaming UI — `complete()` only. Streaming is a follow-up once a view layer exists.
- No automatic retries or JSON repair — if the LLM returns malformed JSON we raise; we can add a single repair retry if it turns out to be flaky in practice.
- No `PortfolioLink` auto-creation (deferred to the application-generation plan).

---

## Critical files touched

- **New**: [backend/jac/llm.py](backend/jac/llm.py)
- **Edit**: [backend/jac/cv.py](backend/jac/cv.py) — add imports, `_entries_for_llm`, replace `ai_filter_entries` stub, add `ai_extract_keywords`, `ai_rank_entries`, `ai_analyze_job`, `agentic_tailor`
- **Edit**: [backend/lukehirsch/settings.py](backend/lukehirsch/settings.py) — add `"reasoning"` alias to `LLM` dict
