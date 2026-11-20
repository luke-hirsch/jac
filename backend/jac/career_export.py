"""A user's career as JSON: the shape `cv_import` (`career_import.import_career`) reads.

Shared by the `cv_export` command and the CV overview's download (`CareerExportView`).

References (locations, domains, skills, related_skills, builds_on, certifications, and a
project's source job by title) go out **by name**, not by database id, so the file is portable
across servers: export here, copy the file, import it on another box.

The computed `Skill.years_of_experience` is deliberately *not* exported, only the manual
`years_of_experience_override`: the computed value is derived on read.

Values stay plain Python (dates are `date`): the command writes them with `default=str`, DRF's
encoder with `isoformat()`, both "YYYY-MM-DD".
"""

from __future__ import annotations

from django.contrib.auth.models import User

from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    Skill,
)


def _names(manager) -> list[str]:
    """Sorted list of related rows' `name` values (for M2M references)."""
    return sorted(manager.values_list("name", flat=True))


def export_career(user: User) -> dict[str, list[dict]]:
    """Every section of `user`'s career, in the order `import_career` creates them."""
    return {
        "domains": _domains(user),
        "locations": _locations(user),
        "certifications": _certifications(user),
        "skills": _skills(user),
        "jobs": _jobs(user),
        "projects": _projects(user),
        "educations": _educations(user),
        "languages": _languages(user),
    }


def _domains(user: User) -> list[dict]:
    # Only the user's *own* domains. System-default domains the user has tagged onto entries are
    # deliberately excluded: the entry sections still reference them by name, and on import
    # `_resolve_domains` resolves those names against the target box's own system defaults
    # (reusing them) or auto-creates a user-owned fallback when absent, so exporting them here
    # would just create user-owned duplicates of names that already exist as shared defaults.
    return [
        {"name": d.name, "description": d.description}
        for d in Domain.objects.filter(user=user).order_by("name")
    ]


def _locations(user: User) -> list[dict]:
    return [
        {
            "city": loc.city,
            "country": loc.country,
            "street": loc.street,
            "zip": loc.zip,
            "longitude": loc.longitude,
            "latitude": loc.latitude,
        }
        for loc in Location.objects.filter(user=user).order_by("city")
    ]


def _certifications(user: User) -> list[dict]:
    return [
        {
            "name": c.name,
            "issuer": c.issuer,
            "issued_on": c.issued_on,
            "expires_on": c.expires_on,
            "credential_id": c.credential_id,
            "url": c.url,
            "description": c.description,
            "favourite": c.favourite,
            "skills": _names(c.skills),
            "domains": _names(c.domains),
        }
        for c in Certification.objects.filter(user=user).order_by("name")
    ]


def _skills(user: User) -> list[dict]:
    return [
        {
            "name": s.name,
            "proficiency": s.proficiency,
            "category": s.category,
            "first_used": s.first_used,
            "years_of_experience_override": s.years_of_experience_override,
            "certification": s.certification.name if s.certification else None,
            "domains": _names(s.domains),
            "related_skills": _names(s.related_skills),
            "builds_on": _names(s.builds_on),
            "description": s.description,
            "favourite": s.favourite,
        }
        for s in Skill.objects.filter(user=user).order_by("name")
    ]


def _jobs(user: User) -> list[dict]:
    return [
        {
            "title": j.title,
            "company": j.company,
            "location": j.location.city if j.location else None,
            "job_type": j.job_type,
            "started": j.started,
            "ended": j.ended,
            "url": j.url,
            "description": j.description,
            "favourite": j.favourite,
            "skills": _names(j.skills),
            "domains": _names(j.domains),
        }
        for j in Job.objects.filter(user=user).order_by("started")
    ]


def _projects(user: User) -> list[dict]:
    return [
        {
            "name": p.name,
            "location": p.location.city if p.location else None,
            "job": p.job.title if p.job else None,
            "started": p.started,
            "ended": p.ended,
            "url": p.url,
            "description": p.description,
            "favourite": p.favourite,
            "skills": _names(p.skills),
            "domains": _names(p.domains),
        }
        for p in Project.objects.filter(user=user).order_by("name")
    ]


def _educations(user: User) -> list[dict]:
    return [
        {
            "institution": e.institution,
            "location": e.location.city if e.location else None,
            "field_of_study": e.field_of_study,
            "degree": e.degree,
            "degree_level": e.degree_level,
            "grade": e.grade,
            "started": e.started,
            "ended": e.ended,
            "description": e.description,
            "favourite": e.favourite,
            "skills": _names(e.skills),
            "domains": _names(e.domains),
        }
        for e in Education.objects.filter(user=user).order_by("started")
    ]


def _languages(user: User) -> list[dict]:
    return [
        {
            "name": lang.name,
            "fluency": lang.fluency,
            "certification": lang.certification.name if lang.certification else None,
            "description": lang.description,
            "favourite": lang.favourite,
        }
        for lang in Language.objects.filter(user=user).order_by("name")
    ]
