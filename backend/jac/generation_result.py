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
    return (
        f"{head} @ {e.institution} ({window})"
        if head
        else f"{e.institution} ({window})"
    )


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
                    "pinned": getattr(obj, "pinned", False),
                    "warning": getattr(obj, "selection_warning", ""),
                }
            )
        out[section] = rows
    return out
