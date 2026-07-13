"""Seed the shared system defaults: the Domain taxonomy, the personality-question pool, and
the default ApplicationLayouts.

Rows owned by the ``settings.SYSTEM_USER_USERNAME`` user are read-only defaults visible to
every user (see ``SystemScopedManager.for_user``). There is no fixture or data migration for
them — this command is the single, idempotent source of truth, so a freshly deployed box gets
the same picker/questionnaire defaults as dev.

The default layouts also carry their template file (``jac/resources/*.json``, a declarative
spec the frontend react-pdf renderer consumes); they back the ``JobApplication.layout`` field
default / SET_DEFAULT target.

Usage:
    python manage.py seed_system_defaults          # create anything that is missing
    python manage.py seed_system_defaults --prune   # also delete system rows not in these lists

Re-runnable: existing rows are left untouched (question wording/order is re-synced from
PERSONALITY_QUESTIONS); only missing ones are created. Domains are kept deliberately *broad*
(industries / sectors) — a user adds their own narrower tags on top.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from spa.models import PersonalityQuestion
from spa.personality_questions import PERSONALITY_QUESTIONS

from jac.models import ApplicationLayout, Domain

RESOURCES = Path(__file__).resolve().parents[2] / "resources"
DEFAULT_LAYOUTS = [
    ("default", RESOURCES / "default_layout.json"),
    ("two-page", RESOURCES / "two_page_layout.json"),
]
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

        q_created = []
        for i, q in enumerate(PERSONALITY_QUESTIONS):
            obj, was_created = PersonalityQuestion.objects.get_or_create(
                user=system,
                slug=q["slug"],
                defaults={"prompt": q["prompt"], "order": i},
            )
            if was_created:
                q_created.append(q["slug"])
            elif obj.prompt != q["prompt"] or obj.order != i:
                # let the wording/order be re-tuned in PERSONALITY_QUESTIONS and re-seeded
                obj.prompt = q["prompt"]
                obj.order = i
                obj.save(update_fields=["prompt", "order"])

        q_pruned = []
        if options["prune"]:
            wanted_slugs = {q["slug"] for q in PERSONALITY_QUESTIONS}
            for q in PersonalityQuestion.objects.filter(user=system).exclude(
                slug__in=wanted_slugs
            ):
                q_pruned.append(q.slug)
                q.delete()

        for name, resource in DEFAULT_LAYOUTS:
            layout, layout_created = ApplicationLayout.objects.get_or_create(
                user=system, name=name
            )
            data = resource.read_bytes()
            if layout.template:
                with layout.template.open("rb") as fh:
                    current = fh.read()
            else:
                current = None
            if current != data:
                if layout.template:
                    layout.template.delete(save=False)
                layout.template.save(resource.name, ContentFile(data))
                layout_action = "created" if layout_created else "template refreshed"
            else:
                layout_action = "unchanged"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Layout {layout.name!r}: {layout_action} ({layout.template.name})"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"System defaults: {Domain.objects.filter(user=system).count()} total, "
                f"{len(created)} created."
            )
        )
        if created:
            self.stdout.write("  created: " + ", ".join(created))
        if pruned:
            self.stdout.write(self.style.WARNING("  pruned: " + ", ".join(pruned)))
        self.stdout.write(
            self.style.SUCCESS(
                f"Personality questions: "
                f"{PersonalityQuestion.objects.filter(user=system).count()} total, "
                f"{len(q_created)} created."
            )
        )
        if q_created:
            self.stdout.write("  created: " + ", ".join(q_created))
        if q_pruned:
            self.stdout.write(self.style.WARNING("  pruned: " + ", ".join(q_pruned)))
        self.stdout.write(
            self.style.SUCCESS(
                f"Default layout {layout.name!r}: {layout_action} ({layout.template.name})"
            )
        )
