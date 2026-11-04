"""Import CV entries for a user from a JSON file.

Usage:
    python manage.py cv_import --user 1 --file backend/jac/data/lukas_cv.json
    python manage.py cv_import --username lukas --file path/to/cv.json --replace

JSON schema (all sections optional):

    {
      "domains":        [{"name": "...", "description": "..."}],
      "locations":      [{"city": "...", "country": "...", "street": "...", "zip": "..."}],
      "certifications": [{"name", "issuer", "issued_on", "expires_on", "credential_id", "url",
                          "description", "skills": [...], "domains": [...]}],
      "skills":         [{"name", "proficiency", "category", "first_used", "domains": [...],
                          "certification": "<cert name>", "description",
                          "years_of_experience_override": <int|null>,
                          "related_skills": ["<skill name>", ...],
                          "builds_on": ["<skill name>", ...]}],
      "jobs":           [{"title", "company", "location": "<city>", "job_type", "started", "ended",
                          "url", "description", "skills": [...], "domains": [...]}],
      "projects":       [{"name", "location", "job": "<job title>", "started", "ended", "url",
                          "description", "skills": [...], "domains": [...]}],
      "educations":     [{"institution", "location", "field_of_study", "degree",
                          "degree_level": 0-5, "grade", "started", "ended", "description",
                          "skills": [...], "domains": [...]}],
      "languages":      [{"name", "fluency", "certification": "<cert name>", "description"}]
    }

References (`location`, `domains`, `skills`, `related_skills`, `builds_on`, `certification`,
a project's source `job` by title) are resolved by name against items declared earlier in the
same file or already present in the DB. Domains and locations are scoped to the target user (own
+ system-default domains); an unresolved name is created under the user, so a fresh server need
not pre-share the taxonomy. `cv_export` writes exactly this shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from jac.career_import import CareerImportError, import_career, wipe_career


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

        try:
            with transaction.atomic():
                if options["replace"]:
                    wipe_career(user)

                counts = import_career(user, data)

                if options["dry_run"]:
                    transaction.set_rollback(True)
                    self.stdout.write(self.style.WARNING("DRY RUN — rolling back."))
        except CareerImportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Imported into user {user.username!r}:"))
        for section, n in counts.items():
            self.stdout.write(f"  {section}: {n}")
