"""Load a career (the JSON shape `cv_export` writes) into a user's CV entries.

Shared by the `cv_import` command, the sample-career endpoint and the CV page's import. The shape
and the name-based reference rules are documented on the command. Callers own the transaction.

The file may come from a browser, so every row is checked with the model's own `full_clean()`
before it's saved: choices, lengths and URL schemes hold as in the editor, and a file that breaks
them is a `CareerImportError` with a sentence for the user, never a 500.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import MultipleObjectsReturned, ValidationError
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

# What `cv_export` writes; a file with none of them isn't a career.
SECTIONS = (
    "domains",
    "locations",
    "certifications",
    "skills",
    "jobs",
    "projects",
    "educations",
    "languages",
)
# An upload's ceiling: a real career is a few hundred rows, and each row costs a few queries.
MAX_IMPORT_ROWS = 2000


class CareerImportError(ValueError):
    """The data can't be imported: it names something it never declared (an unknown skill, job,
    …), misses a field, or breaks a field's rules. The message is meant for the user."""


class CareerNotEmpty(Exception):
    """The sample and an upload only ever load into an empty career, so they can't clobber
    anything."""


def has_career_entries(user: User) -> bool:
    return any(model.objects.filter(user=user).exists() for model in ENTRY_MODELS)


def load_into_empty_career(user: User, data: Any) -> dict[str, int]:
    """Load `data` into `user`'s EMPTY career. Raises CareerNotEmpty otherwise, CareerImportError
    for a file it can't take; either way nothing is written.

    The check runs twice: the first turns a refused request away without taking the row lock, the
    second is what makes a double click safe.
    """
    if has_career_entries(user):
        raise CareerNotEmpty
    if not isinstance(data, dict) or not any(key in data for key in SECTIONS):
        raise CareerImportError(
            "That isn't a career file: pick the one Download career wrote."
        )
    rows = sum(len(v) for v in data.values() if isinstance(v, list))
    if rows > MAX_IMPORT_ROWS:
        raise CareerImportError(
            f"The file has {rows} entries; an import takes at most {MAX_IMPORT_ROWS}."
        )
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        if has_career_entries(user):
            raise CareerNotEmpty
        return import_career(user, data)


def load_sample_career(user: User) -> dict[str, int]:
    """Load the fictional sample into `user`'s EMPTY career. Raises CareerNotEmpty otherwise."""
    data = json.loads(SAMPLE_CAREER.read_text(encoding="utf-8"))
    return load_into_empty_career(user, data)


def wipe_career(user: User) -> None:
    for model in ENTRY_MODELS:
        model.objects.filter(user=user).delete()


def import_career(user: User, data: dict) -> dict[str, int]:
    """Create `data`'s entries under `user`. Returns {section: rows created}.

    A file of the wrong shape (a section that isn't a list, an entry that isn't an object, a
    required field missing) is a CareerImportError, not a crash.
    """
    try:
        return _import_all(user, data)
    except CareerImportError:
        raise
    except KeyError as exc:
        raise CareerImportError(f"An entry is missing {exc.args[0]!r}.") from exc
    except MultipleObjectsReturned as exc:
        raise CareerImportError(
            "Two entries share a name that has to be unique (a skill or a certification)."
        ) from exc
    except (TypeError, AttributeError) as exc:
        raise CareerImportError(
            "The file doesn't have the shape Download career writes."
        ) from exc


def _import_all(user: User, data: dict) -> dict[str, int]:
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


def _create(model, **fields):
    """Build, check and save one row: `full_clean()` is the editor's rules (choices, lengths, URL
    schemes, the favourite cap), which `objects.create()` would skip."""
    obj = model(**fields)
    try:
        obj.full_clean()
    except ValidationError as exc:
        what = next(
            (
                fields[k]
                for k in ("name", "title", "institution", "city")
                if k in fields
            ),
            "",
        )
        problems = "; ".join(
            f"{field}: {' '.join(messages)}"
            for field, messages in getattr(
                exc, "message_dict", {"": exc.messages}
            ).items()
        )
        raise CareerImportError(
            f"{model._meta.verbose_name.capitalize()} {what!r}: {problems}"
        ) from exc
    obj.save()
    return obj


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise CareerImportError(f"Not a date (YYYY-MM-DD): {value!r}") from exc


def _unique_names(kind: str, items: list[dict]) -> None:
    seen: set[str] = set()
    for item in items:
        if item["name"] in seen:
            raise CareerImportError(f"Two {kind}s are named {item['name']!r}.")
        seen.add(item["name"])


def _import_domains(user: User, items: list[dict]) -> int:
    n = 0
    for item in items:
        if not Domain.objects.filter(user=user, name=item["name"]).exists():
            _create(
                Domain,
                user=user,
                name=item["name"],
                description=item.get("description", ""),
            )
            n += 1
    return n


def _import_locations(user: User, items: list[dict]) -> int:
    n = 0
    for item in items:
        if not Location.objects.filter(
            user=user, city=item["city"], country=item.get("country")
        ).exists():
            _create(
                Location,
                user=user,
                city=item["city"],
                country=item.get("country"),
                street=item.get("street"),
                zip=item.get("zip"),
                longitude=item.get("longitude"),
                latitude=item.get("latitude"),
            )
            n += 1
    return n


def _resolve_location(user: User, name: str | None) -> Location | None:
    if not name:
        return None
    # Scoped to the user; auto-create on a miss so a fresh server need not
    # be pre-seeded with the same locations. Nothing makes a city unique, so first match wins.
    return Location.objects.filter(user=user, city=name).first() or _create(
        Location, user=user, city=name
    )


def _resolve_domains(user: User, names: list[str]) -> list[Domain]:
    out = []
    for n in names or []:
        # Own domains + system defaults; auto-create under the user on miss.
        domain = Domain.objects.for_user(user).filter(name=n).first()
        if not domain:
            domain = Domain.objects.filter(user=user, name=n).first() or _create(
                Domain, user=user, name=n
            )
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
    _unique_names("certification", items)
    for item in items:
        cert = _create(
            Certification,
            user=user,
            name=item["name"],
            issuer=item["issuer"],
            issued_on=_parse_date(item.get("issued_on")),
            expires_on=_parse_date(item.get("expires_on")),
            credential_id=item.get("credential_id", ""),
            url=item.get("url", ""),
            description=item.get("description", ""),
            favourite=item.get("favourite", False),
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
    _unique_names("skill", items)
    # Two passes: create every skill (so related_skills can reference one
    # defined later in the file), then wire the symmetric relations.
    for item in items:
        skill = _create(
            Skill,
            user=user,
            name=item["name"],
            proficiency=item.get("proficiency", Skill.Proficiency_Choices.intermediate),
            category=item.get("category", Skill.Category.technical),
            first_used=_parse_date(item.get("first_used")),
            years_of_experience_override=item.get("years_of_experience_override"),
            description=item.get("description", ""),
            favourite=item.get("favourite", False),
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
        job = _create(
            Job,
            user=user,
            title=item["title"],
            company=item["company"],
            location=_resolve_location(user, item.get("location")),
            job_type=item.get("job_type", Job.job_type_choices.full_time),
            started=_parse_date(item["started"]),
            ended=_parse_date(item.get("ended")),
            url=item.get("url", ""),
            description=item.get("description", ""),
            favourite=item.get("favourite", False),
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
        project = _create(
            Project,
            user=user,
            name=item["name"],
            location=_resolve_location(user, item.get("location")),
            job=_resolve_job(user, item.get("job")),
            started=_parse_date(item.get("started")),
            ended=_parse_date(item.get("ended")),
            url=item.get("url", ""),
            description=item.get("description", ""),
            favourite=item.get("favourite", False),
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
        education = _create(
            Education,
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
            favourite=item.get("favourite", False),
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
        _create(
            Language,
            user=user,
            name=item["name"],
            fluency=item.get("fluency", Language.Fluency.basic),
            certification=_resolve_certification(user, item.get("certification")),
            description=item.get("description", ""),
            favourite=item.get("favourite", False),
        )
    return len(items)
