"""Export a user's CV entries to JSON (the exact shape `cv_import` consumes).

Usage:
    python manage.py cv_export --user 1 --file /tmp/cv.json
    python manage.py cv_export --username lukas            # → stdout

A thin wrapper over `jac.career_export`, which documents the shape. The CV overview's download
serves the same file. Round-trippable with `cv_import`.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from jac.career_export import export_career


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

        # default=str renders date objects as ISO "YYYY-MM-DD".
        payload = json.dumps(
            export_career(user), indent=2, ensure_ascii=False, default=str
        )

        if options["file"]:
            Path(options["file"]).write_text(payload + "\n")
            self.stderr.write(
                self.style.SUCCESS(f"Exported {user.username!r} → {options['file']}")
            )
        else:
            self.stdout.write(payload)
