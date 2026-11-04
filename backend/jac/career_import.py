"""Load a career (the JSON shape `cv_export` writes) into a user's CV entries.

Shared by the `cv_import` command and the sample-career endpoint. The shape and the name-based
reference rules are documented on the command. Callers own the transaction.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction

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

# Cleared by --replace, and what "the user has a career" means. Domain/Location are intentionally
# excluded: they are shared-ish references (system defaults, reusable locations) that other rows
# may still point at, so wiping them on a re-import would be surprising.
ENTRY_MODELS = (
    Education,
    Certification,
    Skill,
    Job,
    Project,
    Language,
)

SAMPLE_CAREER = Path(__file__).resolve().parent / "resources" / "sample_career.json"


class CareerImportError(ValueError):
    """The data names something it never declared (an unknown skill, job, …)."""


class CareerNotEmpty(Exception):
    """The sample only ever loads into an empty career, so it can't clobber anything."""


def has_career_entries(user: User) -> bool:
    return any(model.objects.filter(user=user).exists() for model in ENTRY_MODELS)


def load_sample_career(user: User) -> dict[str, int]:
    """Load the fictional sample into `user`'s EMPTY career. Raises CareerNotEmpty otherwise.

    The check runs twice: the first turns a refused request away without taking the row lock, the
    second is what makes a double click safe.
    """
    if has_career_entries(user):
        raise CareerNotEmpty
    data = json.loads(SAMPLE_CAREER.read_text(encoding="utf-8"))
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        if has_career_entries(user):
            raise CareerNotEmpty
        return import_career(user, data)


def wipe_career(user: User) -> None:
    for model in ENTRY_MODELS:
        model.objects.filter(user=user).delete()


def import_career(user: User, data: dict) -> dict[str, int]:
    """Create `data`'s entries under `user`. Returns {section: rows created}."""
    counts: dict[str, int] = {}
    counts["domains"] = _import_domains(user, data.get("domains", []))
    counts["locations"] = _import_locations(user, data.get("locations", []))
    counts["certifications"] = _import_certifications(
        user, data.get("certifications", [])
    )
    counts["skills"] = _import_skills(user, data.get("skills", []))
    # cert.skills can only resolve now that skills exist.
    _wire_certification_skills(user, data.get("certifications", []))
    counts["jobs"] = _import_jobs(user, data.get("jobs", []))
    counts["projects"] = _import_projects(user, data.get("projects", []))
    counts["educations"] = _import_educations(user, data.get("educations", []))
    counts["languages"] = _import_languages(user, data.get("languages", []))
    return counts


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _import_domains(user: User, items: list[dict]) -> int:
    n = 0
    for item in items:
        _, created = Domain.objects.get_or_create(
            user=user,
            name=item["name"],
            defaults={"description": item.get("description", "")},
        )
        n += int(created)
    return n


def _import_locations(user: User, items: list[dict]) -> int:
    n = 0
    for item in items:
        _, created = Location.objects.get_or_create(
            user=user,
            city=item["city"],
            country=item.get("country"),
            defaults={
                "street": item.get("street"),
                "zip": item.get("zip"),
                "longitude": item.get("longitude"),
                "latitude": item.get("latitude"),
            },
        )
        n += int(created)
    return n


def _resolve_location(user: User, name: str | None) -> Location | None:
    if not name:
        return None
    # Scoped to the user; auto-create on a miss so a fresh server need not
    # be pre-seeded with the same locations. Nothing makes a city unique, so first match wins.
    return Location.objects.filter(
        user=user, city=name
    ).first() or Location.objects.create(user=user, city=name)


def _resolve_domains(user: User, names: list[str]) -> list[Domain]:
    out = []
    for n in names or []:
        # Own domains + system defaults; auto-create under the user on miss.
        domain = Domain.objects.for_user(user).filter(name=n).first()
        if not domain:
            domain, _ = Domain.objects.get_or_create(user=user, name=n)
        out.append(domain)
    return out


def _resolve_skills(user: User, names: list[str]) -> list[Skill]:
    out = []
    for n in names or []:
        skill = Skill.objects.filter(user=user, name=n).first()
        if not skill:
            raise CareerImportError(f"Unknown skill for user: {n!r}")
        out.append(skill)
    return out


def _resolve_certification(user: User, name: str | None) -> Certification | None:
    if not name:
        return None
    cert = Certification.objects.filter(user=user, name=name).first()
    if not cert:
        raise CareerImportError(f"Unknown certification for user: {name!r}")
    return cert


def _resolve_job(user: User, title: str | None) -> Job | None:
    # Jobs have no unique name; reference by title (first match wins). Imported
    # before projects, so the job exists by the time we resolve it.
    if not title:
        return None
    job = Job.objects.filter(user=user, title=title).first()
    if not job:
        raise CareerImportError(f"Unknown job for user: {title!r}")
    return job


def _import_certifications(user: User, items: list[dict]) -> int:
    for item in items:
        cert = Certification.objects.create(
            user=user,
            name=item["name"],
            issuer=item["issuer"],
            issued_on=_parse_date(item.get("issued_on")),
            expires_on=_parse_date(item.get("expires_on")),
            credential_id=item.get("credential_id", ""),
            url=item.get("url", ""),
            description=item.get("description", ""),
        )
        domains = _resolve_domains(user, item.get("domains", []))
        if domains:
            cert.domains.set(domains)
    # cert.skills is wired in a later pass (see _wire_certification_skills):
    # certifications import before skills exist, so the skill names can't
    # resolve yet here.
    return len(items)


def _wire_certification_skills(user: User, items: list[dict]) -> None:
    for item in items:
        skills = _resolve_skills(user, item.get("skills", []))
        if skills:
            Certification.objects.get(user=user, name=item["name"]).skills.set(skills)


def _import_skills(user: User, items: list[dict]) -> int:
    # Two passes: create every skill (so related_skills can reference one
    # defined later in the file), then wire the symmetric relations.
    for item in items:
        skill = Skill.objects.create(
            user=user,
            name=item["name"],
            proficiency=item.get("proficiency", Skill.Proficiency_Choices.intermediate),
            category=item.get("category", Skill.Category.technical),
            first_used=_parse_date(item.get("first_used")),
            years_of_experience_override=item.get("years_of_experience_override"),
            certification=_resolve_certification(user, item.get("certification")),
            description=item.get("description", ""),
        )
        domains = _resolve_domains(user, item.get("domains", []))
        if domains:
            skill.domains.set(domains)
    for item in items:
        related = _resolve_skills(user, item.get("related_skills", []))
        builds_on = _resolve_skills(user, item.get("builds_on", []))
        if related or builds_on:
            skill = Skill.objects.get(user=user, name=item["name"])
            if related:
                skill.related_skills.set(related)
            if builds_on:
                skill.builds_on.set(builds_on)
    return len(items)


def _import_jobs(user: User, items: list[dict]) -> int:
    for item in items:
        job = Job.objects.create(
            user=user,
            title=item["title"],
            company=item["company"],
            location=_resolve_location(user, item.get("location")),
            job_type=item.get("job_type", Job.job_type_choices.full_time),
            started=_parse_date(item["started"]),
            ended=_parse_date(item.get("ended")),
            url=item.get("url", ""),
            description=item.get("description", ""),
        )
        skills = _resolve_skills(user, item.get("skills", []))
        if skills:
            job.skills.set(skills)
        domains = _resolve_domains(user, item.get("domains", []))
        if domains:
            job.domains.set(domains)
    return len(items)


def _import_projects(user: User, items: list[dict]) -> int:
    for item in items:
        project = Project.objects.create(
            user=user,
            name=item["name"],
            location=_resolve_location(user, item.get("location")),
            job=_resolve_job(user, item.get("job")),
            started=_parse_date(item.get("started")),
            ended=_parse_date(item.get("ended")),
            url=item.get("url", ""),
            description=item.get("description", ""),
        )
        skills = _resolve_skills(user, item.get("skills", []))
        if skills:
            project.skills.set(skills)
        domains = _resolve_domains(user, item.get("domains", []))
        if domains:
            project.domains.set(domains)
    return len(items)


def _import_educations(user: User, items: list[dict]) -> int:
    for item in items:
        education = Education.objects.create(
            user=user,
            institution=item["institution"],
            location=_resolve_location(user, item.get("location")),
            field_of_study=item.get("field_of_study", ""),
            degree=item.get("degree"),
            degree_level=item.get("degree_level", 0),
            grade=item.get("grade"),
            started=_parse_date(item["started"]),
            ended=_parse_date(item.get("ended")),
            description=item.get("description", ""),
        )
        skills = _resolve_skills(user, item.get("skills", []))
        if skills:
            education.skills.set(skills)
        domains = _resolve_domains(user, item.get("domains", []))
        if domains:
            education.domains.set(domains)
    return len(items)


def _import_languages(user: User, items: list[dict]) -> int:
    for item in items:
        Language.objects.create(
            user=user,
            name=item["name"],
            fluency=item.get("fluency", Language.Fluency.basic),
            certification=_resolve_certification(user, item.get("certification")),
            description=item.get("description", ""),
        )
    return len(items)
