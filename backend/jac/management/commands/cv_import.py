"""Import CV entries for a user from a JSON file.

Usage:
    python manage.py cv_import --user 1 --file backend/jac/data/lukas_cv.json
    python manage.py cv_import --username lukas --file path/to/cv.json --replace

JSON schema (all sections optional):

    {
      "domains":        [{"name": "...", "description": "..."}],
      "locations":      [{"city": "...", "country": "...", "street": "...", "zip": "..."}],
      "certifications": [{"name", "issuer", "issued_on", "expires_on", "credential_id", "url", "description"}],
      "skills":         [{"name", "proficiency", "category", "first_used", "domains": [...],
                          "certification": "<cert name>", "description"}],
      "jobs":           [{"title", "company", "location": "<city>", "job_type", "started", "ended",
                          "url", "description", "skills": [...], "domains": [...]}],
      "projects":       [{"name", "location", "started", "ended", "url", "description",
                          "skills": [...], "domains": [...]}],
      "educations":     [{"institution", "location", "field_of_study", "degree", "grade",
                          "started", "ended", "description"}],
      "languages":      [{"name", "fluency", "certification": "<cert name>", "description"}]
    }

References (`location`, `domains`, `skills`, `certification`) are resolved by name against
items declared earlier in the same file or already present in the DB.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
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


ENTRY_MODELS = (Education, Certification, Skill, Job, Project, Language)


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


class Command(BaseCommand):
    help = "Import CV entries (skills, jobs, education, etc.) for a user from JSON."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to the CV JSON file.")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--user", type=int, help="User pk to bind entries to.")
        group.add_argument("--username", type=str, help="Username to bind entries to.")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete the user's existing CV entries before importing.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate the file without writing to the DB.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise CommandError("Top-level JSON must be an object.")

        if options["user"] is not None:
            user = User.objects.filter(pk=options["user"]).first()
            if not user:
                raise CommandError(f"User pk={options['user']} not found.")
        else:
            user = User.objects.filter(username=options["username"]).first()
            if not user:
                raise CommandError(f"Username {options['username']!r} not found.")

        with transaction.atomic():
            if options["replace"]:
                self._wipe_user_entries(user)

            counts: dict[str, int] = {}
            counts["domains"] = self._import_domains(data.get("domains", []))
            counts["locations"] = self._import_locations(data.get("locations", []))
            counts["certifications"] = self._import_certifications(
                user, data.get("certifications", [])
            )
            counts["skills"] = self._import_skills(user, data.get("skills", []))
            counts["jobs"] = self._import_jobs(user, data.get("jobs", []))
            counts["projects"] = self._import_projects(user, data.get("projects", []))
            counts["educations"] = self._import_educations(
                user, data.get("educations", [])
            )
            counts["languages"] = self._import_languages(user, data.get("languages", []))

            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("DRY RUN — rolling back."))

        self.stdout.write(self.style.SUCCESS(f"Imported into user {user.username!r}:"))
        for section, n in counts.items():
            self.stdout.write(f"  {section}: {n}")

    # ---- helpers ----------------------------------------------------------

    def _wipe_user_entries(self, user: User) -> None:
        for model in ENTRY_MODELS:
            model.objects.filter(user=user).delete()

    def _import_domains(self, items: list[dict]) -> int:
        n = 0
        for item in items:
            _, created = Domain.objects.update_or_create(
                name=item["name"],
                defaults={"description": item.get("description", "")},
            )
            n += int(created)
        return n

    def _import_locations(self, items: list[dict]) -> int:
        n = 0
        for item in items:
            _, created = Location.objects.get_or_create(
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

    def _resolve_location(self, name: str | None) -> Location | None:
        if not name:
            return None
        loc = Location.objects.filter(city=name).first()
        if not loc:
            raise CommandError(f"Unknown location: {name!r}")
        return loc

    def _resolve_domains(self, names: list[str]) -> list[Domain]:
        out = []
        for n in names or []:
            domain = Domain.objects.filter(name=n).first()
            if not domain:
                raise CommandError(f"Unknown domain: {n!r}")
            out.append(domain)
        return out

    def _resolve_skills(self, user: User, names: list[str]) -> list[Skill]:
        out = []
        for n in names or []:
            skill = Skill.objects.filter(user=user, name=n).first()
            if not skill:
                raise CommandError(f"Unknown skill for user: {n!r}")
            out.append(skill)
        return out

    def _resolve_certification(self, user: User, name: str | None) -> Certification | None:
        if not name:
            return None
        cert = Certification.objects.filter(user=user, name=name).first()
        if not cert:
            raise CommandError(f"Unknown certification for user: {name!r}")
        return cert

    def _import_certifications(self, user: User, items: list[dict]) -> int:
        for item in items:
            Certification.objects.create(
                user=user,
                name=item["name"],
                issuer=item["issuer"],
                issued_on=_parse_date(item.get("issued_on")),
                expires_on=_parse_date(item.get("expires_on")),
                credential_id=item.get("credential_id", ""),
                url=item.get("url", ""),
                description=item.get("description", ""),
            )
        return len(items)

    def _import_skills(self, user: User, items: list[dict]) -> int:
        for item in items:
            skill = Skill.objects.create(
                user=user,
                name=item["name"],
                proficiency=item.get("proficiency", Skill.Proficiency_Choices.intermediate),
                category=item.get("category", Skill.Category.technical),
                first_used=_parse_date(item.get("first_used")),
                certification=self._resolve_certification(user, item.get("certification")),
                description=item.get("description", ""),
            )
            domains = self._resolve_domains(item.get("domains", []))
            if domains:
                skill.domains.set(domains)
        return len(items)

    def _import_jobs(self, user: User, items: list[dict]) -> int:
        for item in items:
            job = Job.objects.create(
                user=user,
                title=item["title"],
                company=item["company"],
                location=self._resolve_location(item.get("location")),
                job_type=item.get("job_type", Job.job_type_choices.full_time),
                started=_parse_date(item["started"]),
                ended=_parse_date(item.get("ended")),
                url=item.get("url", ""),
                description=item.get("description", ""),
            )
            skills = self._resolve_skills(user, item.get("skills", []))
            if skills:
                job.skills.set(skills)
            domains = self._resolve_domains(item.get("domains", []))
            if domains:
                job.domains.set(domains)
        return len(items)

    def _import_projects(self, user: User, items: list[dict]) -> int:
        for item in items:
            project = Project.objects.create(
                user=user,
                name=item["name"],
                location=self._resolve_location(item.get("location")),
                started=_parse_date(item.get("started")),
                ended=_parse_date(item.get("ended")),
                url=item.get("url", ""),
                description=item.get("description", ""),
            )
            skills = self._resolve_skills(user, item.get("skills", []))
            if skills:
                project.skills.set(skills)
            domains = self._resolve_domains(item.get("domains", []))
            if domains:
                project.domains.set(domains)
        return len(items)

    def _import_educations(self, user: User, items: list[dict]) -> int:
        for item in items:
            Education.objects.create(
                user=user,
                institution=item["institution"],
                location=self._resolve_location(item.get("location")),
                field_of_study=item.get("field_of_study", ""),
                degree=item.get("degree"),
                grade=item.get("grade"),
                started=_parse_date(item["started"]),
                ended=_parse_date(item.get("ended")),
                description=item.get("description", ""),
            )
        return len(items)

    def _import_languages(self, user: User, items: list[dict]) -> int:
        for item in items:
            Language.objects.create(
                user=user,
                name=item["name"],
                fluency=item.get("fluency", Language.Fluency.basic),
                certification=self._resolve_certification(user, item.get("certification")),
                description=item.get("description", ""),
            )
        return len(items)
