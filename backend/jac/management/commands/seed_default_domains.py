"""Seed the shared system defaults: the Domain taxonomy + the "default" ApplicationLayout.

Rows owned by the ``settings.SYSTEM_USER_USERNAME`` user are read-only defaults
visible to every user (see ``SystemScopedManager.for_user``). There is no fixture
or data migration for them — this command is the single, idempotent source of
truth, so a freshly deployed box gets the same picker defaults as dev.

The default layout also carries its template file (``jac/resources/default_layout.json``,
a declarative spec the frontend react-pdf renderer consumes); it backs the
``JobApplication.layout`` field default / SET_DEFAULT target.

Usage:
    python manage.py seed_default_domains          # create anything that is missing
    python manage.py seed_default_domains --prune   # also delete default domains not in this list

Re-runnable: existing rows are left untouched; only missing ones are created.
Domains are kept deliberately *broad* (industries / sectors) — a user adds their
own narrower tags on top.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from jac.models import ApplicationLayout, Domain

DEFAULT_LAYOUT_NAME = "default"
DEFAULT_LAYOUT_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "resources" / "default_layout.json"
)


# Broad industry / sector defaults. Lowercase to match the existing rows
# (except the established "IT"). Edit this list — it's the source of truth.
DEFAULT_DOMAINS = [
    # tech
    "IT",
    "web development",
    "data science",
    "security",
    "telecommunications",
    "engineering",
    # science / health
    "science",
    "health",
    "pharmaceuticals",
    "agriculture",
    # industry / trade
    "construction",
    "manufacturing",
    "automotive",
    "aerospace",
    "energy",
    "logistics",
    # commerce / services
    "retail",
    "e-commerce",
    "finance",
    "insurance",
    "real estate",
    "consulting",
    "marketing",
    "human resources",
    "legal",
    # public / social
    "education",
    "government",
    "nonprofit",
    # hospitality / culture
    "hospitality",
    "gastronomy",
    "tourism",
    "entertainment",
    "media",
    "arts & culture",
    "sports",
]


class Command(BaseCommand):
    help = "Create the shared system defaults: Domain rows + the default ApplicationLayout (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete existing system-default domains not in DEFAULT_DOMAINS.",
        )

    def handle(self, *args, **options):
        system, created_user = User.objects.get_or_create(
            username=settings.SYSTEM_USER_USERNAME,
            defaults={"is_active": False},
        )
        if created_user:
            system.set_unusable_password()
            system.save(update_fields=["password"])
            self.stdout.write(
                self.style.WARNING(
                    f"Created system user {settings.SYSTEM_USER_USERNAME!r}."
                )
            )

        created = []
        for name in DEFAULT_DOMAINS:
            _, was_created = Domain.objects.get_or_create(user=system, name=name)
            if was_created:
                created.append(name)

        pruned = []
        if options["prune"]:
            wanted = set(DEFAULT_DOMAINS)
            for d in Domain.objects.filter(user=system).exclude(name__in=wanted):
                pruned.append(d.name)
                d.delete()

        layout, layout_created = ApplicationLayout.objects.get_or_create(
            user=system, name=DEFAULT_LAYOUT_NAME
        )
        if not layout.template:
            layout.template.save(
                DEFAULT_LAYOUT_TEMPLATE.name,
                ContentFile(DEFAULT_LAYOUT_TEMPLATE.read_bytes()),
            )
            layout_action = "created" if layout_created else "template attached"
        else:
            layout_action = "created" if layout_created else "unchanged"

        self.stdout.write(
            self.style.SUCCESS(
                f"System defaults: {Domain.objects.filter(user=system).count()} total, "
                f"{len(created)} created."
            )
        )
        if created:
            self.stdout.write("  created: " + ", ".join(created))
        if pruned:
            self.stdout.write(
                self.style.WARNING("  pruned: " + ", ".join(pruned))
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Default layout {layout.name!r}: {layout_action} ({layout.template.name})"
            )
        )
