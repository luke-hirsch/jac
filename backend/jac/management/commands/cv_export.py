"""Export a user's CV entries to JSON (the exact shape `cv_import` consumes).

Usage:
    python manage.py cv_export --user 1 --file /tmp/cv.json
    python manage.py cv_export --username lukas            # → stdout

References (locations, domains, skills, related_skills, certifications) are emitted
**by name**, not by database id, so the dump is portable across servers: export here,
copy the file, `cv_import` it on the deployed box. Round-trippable with `cv_import`.

The computed `Skill.years_of_experience` is deliberately *not* exported — only the manual
`years_of_experience_override`, since the computed value is derived on read.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    ResumeSnippet,
    Skill,
)


def _names(manager) -> list[str]:
    """Sorted list of related rows' `name` values (for M2M references)."""
    return sorted(manager.values_list("name", flat=True))


class Command(BaseCommand):
    help = "Export a user's CV entries (skills, jobs, education, etc.) to JSON."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--user", type=int, help="User pk to export.")
        group.add_argument("--username", type=str, help="Username to export.")
        parser.add_argument(
            "--file",
            help="Path to write JSON to (defaults to stdout).",
        )

    def handle(self, *args, **options):
        if options["user"] is not None:
            user = User.objects.filter(pk=options["user"]).first()
            if not user:
                raise CommandError(f"User pk={options['user']} not found.")
        else:
            user = User.objects.filter(username=options["username"]).first()
            if not user:
                raise CommandError(f"Username {options['username']!r} not found.")

        data = {
            "domains": self._domains(user),
            "locations": self._locations(user),
            "certifications": self._certifications(user),
            "skills": self._skills(user),
            "jobs": self._jobs(user),
            "projects": self._projects(user),
            "educations": self._educations(user),
            "languages": self._languages(user),
            "resume_snippets": self._snippets(user),
        }

        # default=str renders date objects as ISO "YYYY-MM-DD".
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)

        if options["file"]:
            Path(options["file"]).write_text(payload + "\n")
            self.stderr.write(
                self.style.SUCCESS(
                    f"Exported {user.username!r} → {options['file']}"
                )
            )
        else:
            self.stdout.write(payload)

    # ---- sections ---------------------------------------------------------

    def _domains(self, user: User) -> list[dict]:
        # Only the user's *own* domains. System-default domains the user has
        # tagged onto entries are deliberately excluded: the entry sections
        # still reference them by name, and on import `_resolve_domains`
        # resolves those names against the target box's own system defaults
        # (reusing them) or auto-creates a user-owned fallback when absent —
        # so exporting them here would just create user-owned duplicates of
        # names that already exist as shared defaults.
        return [
            {"name": d.name, "description": d.description}
            for d in Domain.objects.filter(user=user).order_by("name")
        ]

    def _locations(self, user: User) -> list[dict]:
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

    def _certifications(self, user: User) -> list[dict]:
        return [
            {
                "name": c.name,
                "issuer": c.issuer,
                "issued_on": c.issued_on,
                "expires_on": c.expires_on,
                "credential_id": c.credential_id,
                "url": c.url,
                "description": c.description,
            }
            for c in Certification.objects.filter(user=user).order_by("name")
        ]

    def _skills(self, user: User) -> list[dict]:
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
                "description": s.description,
            }
            for s in Skill.objects.filter(user=user).order_by("name")
        ]

    def _jobs(self, user: User) -> list[dict]:
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
                "skills": _names(j.skills),
                "domains": _names(j.domains),
            }
            for j in Job.objects.filter(user=user).order_by("started")
        ]

    def _projects(self, user: User) -> list[dict]:
        return [
            {
                "name": p.name,
                "location": p.location.city if p.location else None,
                "started": p.started,
                "ended": p.ended,
                "url": p.url,
                "description": p.description,
                "skills": _names(p.skills),
                "domains": _names(p.domains),
            }
            for p in Project.objects.filter(user=user).order_by("name")
        ]

    def _educations(self, user: User) -> list[dict]:
        return [
            {
                "institution": e.institution,
                "location": e.location.city if e.location else None,
                "field_of_study": e.field_of_study,
                "degree": e.degree,
                "grade": e.grade,
                "started": e.started,
                "ended": e.ended,
                "description": e.description,
            }
            for e in Education.objects.filter(user=user).order_by("started")
        ]

    def _languages(self, user: User) -> list[dict]:
        return [
            {
                "name": lang.name,
                "fluency": lang.fluency,
                "certification": (
                    lang.certification.name if lang.certification else None
                ),
                "description": lang.description,
            }
            for lang in Language.objects.filter(user=user).order_by("name")
        ]

    def _snippets(self, user: User) -> list[dict]:
        return [
            {
                "title": s.title,
                "content": s.content,
                "kind": s.kind,
                "is_active": s.is_active,
                "domains": _names(s.domains),
                "skills": _names(s.skills),
            }
            for s in ResumeSnippet.objects.filter(user=user).order_by("title")
        ]
