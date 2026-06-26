# [backend] Generation pipeline (real task + result serialization)

> Guide 2 of 3 for **roadmap #1**. Branch: `backend/generation-pipeline` (cut off `main` after
> guide 1 merges). Depends on `[backend]-generation-async-plumbing.md`.

## Context / goal

Guide 1 proved the async loop with a stub task. This guide swaps the stub for the **real
pipeline** — the same recipe the `cover_letter` management command runs — and serializes the
output into the JSON contract the frontend renders. When done, a run produces a tailored CV
selection + a full cover-letter dict (with `ai_share`, `grounding`, `personal_paragraph`).

The orchestration is lifted almost verbatim from
`backend/jac/management/commands/cover_letter.py::_one` (the proven path) — `CV.filter_cv` +
`apply_selection`, `AddressExtract`, `JobPosting`/`JobPostAddress`, `CoverLetter.build()`. The only
genuinely new code is **`serialize_cv_selection`**, which turns the pruned `cv.entries` into a small
render payload.

## Affected files

| File | Change |
| --- | --- |
| `backend/jac/generation_result.py` | **new** — `serialize_cv_selection(cv)` |
| `backend/jac/tasks.py` | edit — replace the stub body of `generate_run` with the real pipeline |
| `backend/jac/views.py` | edit — set `JobPosting.language` from the run (optional polish) |

## The code

### 1. `backend/jac/generation_result.py` (new)

Why a dedicated serializer instead of the CRUD serializers: the task runs in a Celery worker with
**no `request`** in context, and several CRUD serializers assume `context["request"]`. A focused
function also keeps the render payload small and stable. Labels reuse the same text idiom as
`CV._flatten_entries` (see `cv.py`) so the CV reads the way the pipeline "saw" it.

```python
"""Shape a filtered CV (post-CV.apply_selection) into a compact render payload.

apply_selection prunes `cv.entries` to the kept instances in ranked order and stamps each with a
`relevance_score` (None for the strong rung's holistic pick). We emit `{section: [{id, label,
relevance_score}]}`; the frontend renders sections in this order. Labels mirror cv.py's
_flatten_entries so the UI shows what the ranker scored.
"""

from __future__ import annotations


def _skill_label(s) -> str:
    domains = ", ".join(d.name for d in s.domains.all())
    text = f"{s.name} ({s.proficiency}, {s.category})"
    if domains:
        text += f" | domains: {domains}"
    return text


def _job_label(j) -> str:
    window = f"{j.started or '?'}–{j.ended or 'present'}"
    return f"{j.title} at {j.company} ({window})"


def _education_label(e) -> str:
    window = f"{e.started or '?'}–{e.ended or 'present'}"
    head = f"{e.degree or ''} {e.field_of_study or ''}".strip()
    return f"{head} @ {e.institution} ({window})" if head else f"{e.institution} ({window})"


def _certification_label(c) -> str:
    text = f"{c.name} — {c.issuer}"
    if c.issued_on:
        text += f" ({c.issued_on})"
    return text


def _project_label(p) -> str:
    window = f"{p.started or '?'}–{p.ended or 'present'}"
    return f"{p.name} ({window})"


def _language_label(la) -> str:
    return f"{la.name} ({la.fluency})"


_LABELERS = {
    "skills": ("skill", _skill_label),
    "jobs": ("job", _job_label),
    "educations": ("education", _education_label),
    "certifications": ("certification", _certification_label),
    "projects": ("project", _project_label),
    "languages": ("language", _language_label),
}


def serialize_cv_selection(cv) -> dict:
    out: dict = {}
    for section, (singular, label_fn) in _LABELERS.items():
        rows = []
        for obj in cv.entries.get(section, []):
            rows.append(
                {
                    "id": f"{singular}:{obj.pk}",
                    "label": label_fn(obj),
                    "relevance_score": getattr(obj, "relevance_score", None),
                }
            )
        out[section] = rows
    return out
```

### 2. `backend/jac/tasks.py` — replace the stub body

Keep the lifecycle / event contract from guide 1 (status transitions, `publish_event`, `_progress`,
the try/except → `failed`). Only the body between `running` and `done` changes. New imports at top:

```python
from llm_connector.conf import get_alias_strength

from jac.cover_letter import CoverLetter
from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.llm_prompts import AddressExtract
from jac.models import GenerationRun, JobPostAddress

_ADDRESS_FIELDS = (
    "company", "contact_name", "street", "address_line2",
    "zip", "city", "country", "email", "phone",
)
```

Real body (replaces the `# --- STUB pipeline ---` block):

```python
        user = run.user
        alias = run.alias or "default"
        grade = run.grade or get_alias_strength(alias, user=user)

        # 1. Tailor the CV.
        _progress(run, "filtering CV")
        cv = CV(
            user_pk=user.pk,
            domains=run.domains or None,
            started=run.started,
            ended=run.ended,
            min_skill_proficiency=run.min_skill_proficiency or None,
        )
        cv.apply_selection(cv.filter_cv(run.posting_text, grade=grade, alias=alias))

        # 2. Extract the recipient address; refresh the persisted JobPosting.
        _progress(run, "reading posting")
        extracted = AddressExtract(run.posting_text, alias=alias, user=user).extract()
        jp = run.job_posting
        if jp is not None:
            jp.title = extracted.get("title", "") or jp.title
            jp.language = extracted.get("language", "en") or "en"
            jp.save(update_fields=["title", "language", "updated_at"])
        addr = JobPostAddress(**{f: extracted.get(f, "") for f in _ADDRESS_FIELDS})

        # 3. Build the cover letter.
        _progress(run, "researching company" if run.personal_paragraph else "writing letter")
        letter = CoverLetter(
            user,
            jp,
            cv,
            address=addr,
            grade=grade,
            alias=alias,
            max_body_snippets=run.max_body_snippets,
            verify_grounding=run.verify_grounding,
            verifier_alias=run.verifier_alias or None,
            personal_paragraph=run.personal_paragraph,
            research_alias=run.research_alias or None,
        ).build()

        run.result = {
            "meta": {"grade": grade, "alias": alias},
            "cv": serialize_cv_selection(cv),
            "cover_letter": letter,
        }
        run.status = GenerationRun.Status.done
        run.stage = "done"
        run.save(update_fields=["result", "status", "stage", "updated_at"])
        publish_event(run.pk, {"event": "done", "status": run.status, "result": run.result})
```

> Note: `time` import from guide 1 is no longer used — drop it.

### 3. `backend/jac/views.py` — minor polish (optional)

`perform_create` set `JobPosting.language="en"`; the task now overwrites it from `AddressExtract`,
so no change is required. Leave as-is.

## Result shape (frontend contract)

```jsonc
{
  "meta": { "grade": "standard", "alias": "default" },
  "cv": {
    "jobs":    [{ "id": "job:5", "label": "Senior Dev at …", "relevance_score": 0.88 }],
    "skills":  [...], "educations": [...], "certifications": [...],
    "projects": [...], "languages": [...]
  },
  "cover_letter": {            // verbatim CoverLetter.build() — see cover_letter.py
    "language", "subject", "salutation", "body", "sender", "recipient", "date",
    "snippets_used", "snippet_provenance", "ai_share",
    "grounding": { "count": <int|null>, "claims": [..] },
    "personal_paragraph", "personal_paragraph_is_stub",
    "personal_paragraph_sources", "personal_paragraph_grounding": { "count", "claims" },
    "text"
  }
}
```

## Tests (already written, start red)

- `backend/jac/tests/test_generation_task.py`:
  - `serialize_cv_selection` produces the `{section: [{id, label, relevance_score}]}` shape, in
    ranked order, with `relevance_score` carried from the instances (fixture CV via the existing
    `_cv_with` / model factories).
  - `generate_run` with `CV` and `CoverLetter` **patched** (so no Ollama/LLM needed): writes a
    `result` with `meta`/`cv`/`cover_letter`, sets `status=done`, and the failure path
    (`CoverLetter.build` raises) → `status=failed` + `error` populated (wrapped in `_muted()`).

Run: `cd backend && python manage.py test jac.tests.test_generation_task`

## Verification (human)

1. With Redis + worker + daphne running (guide 1), create a run with `alias` pointing at a real
   `LLMConfig` and `grade: "light"`:
   ```js
   await fetch("/api/jac/generations/", { method: "POST", headers: {...},
     body: JSON.stringify({ posting_text: "<paste a real posting>", alias: "ollama", grade: "light" }) })
   ```
2. Watch the worker log: `filtering CV → reading posting → writing letter → done`.
3. `GET /api/jac/generations/<id>/` → `result.cv` has ranked sections, `result.cover_letter` has
   `text`, `ai_share`, `grounding`.
4. Try `grade: "standard"`, `personal_paragraph: true`, `verify_grounding: true` with a
   web-search-capable alias → `cover_letter.personal_paragraph_is_stub === false`, sources present;
   on `light` or a non-capable alias → the loud `PERSONAL_STUB` text with `is_stub === true`.
5. Done = a real tailored CV + cover letter land in `result`, matching the contract above.
