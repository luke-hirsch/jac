"""Load cover-letter snippets from a Markdown file into ResumeSnippet rows.

Usage:
    python manage.py load_snippets --user 1
    python manage.py load_snippets --user 1 --file data/cover-letter-snippets.md

The Markdown is the source of truth. Each `## N. Title` section yields two rows —
one per **DE** / **EN** block — so the 12 sections become 24 ResumeSnippets.
Idempotent: re-running update_or_create's on (user, title, language).
"""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from jac.models import ResumeSnippet

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_FILE = _REPO_ROOT / "data" / "cover-letter-snippets.md"

# Section number → snippet kind. Anything unlisted falls back to `other`.
_KIND_BY_NUM = {
    1: ResumeSnippet.Kind.achievement,
    2: ResumeSnippet.Kind.achievement,
    3: ResumeSnippet.Kind.achievement,
    4: ResumeSnippet.Kind.achievement,
    5: ResumeSnippet.Kind.achievement,
    6: ResumeSnippet.Kind.value_statement,
    7: ResumeSnippet.Kind.achievement,
    8: ResumeSnippet.Kind.achievement,
    9: ResumeSnippet.Kind.achievement,
    10: ResumeSnippet.Kind.value_statement,
    11: ResumeSnippet.Kind.value_statement,
    12: ResumeSnippet.Kind.intro,
}

# `## 1. Fullstack / health-sector software`
_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.*)$")
# `**DE**` / `**EN**` language markers.
_LANG_RE = re.compile(r"^\*\*(DE|EN)\*\*\s*$")


def _parse(text: str):
    """Yield (num, title, lang, content) tuples from the snippet Markdown."""
    num = title = None
    lang = None
    buf: list[str] = []

    def flush():
        if lang and buf:
            content = " ".join(line.strip() for line in buf if line.strip()).strip()
            if content:
                yield num, title, lang.lower(), content

    for line in text.splitlines():
        section = _SECTION_RE.match(line)
        if section:
            yield from flush()
            buf, lang = [], None
            num, title = int(section.group(1)), section.group(2).strip()
            continue
        lang_marker = _LANG_RE.match(line)
        if lang_marker:
            yield from flush()
            buf, lang = [], lang_marker.group(1)
            continue
        if line.startswith("---") or line.startswith("*Quelle"):
            yield from flush()
            buf, lang = [], None
            continue
        if lang:
            buf.append(line)
    yield from flush()


class Command(BaseCommand):
    help = "Load bilingual cover-letter snippets from Markdown into ResumeSnippet."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="User pk")
        parser.add_argument(
            "--file",
            type=str,
            default=str(_DEFAULT_FILE),
            help="Path to the snippets Markdown file",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(pk=options["user"])
        except User.DoesNotExist:
            raise CommandError(f"No user with pk={options['user']}")

        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        created = updated = 0
        for num, title, lang, content in _parse(path.read_text(encoding="utf-8")):
            _, was_created = ResumeSnippet.objects.update_or_create(
                user=user,
                title=title,
                language=lang,
                defaults={
                    "content": content,
                    "kind": _KIND_BY_NUM.get(num, ResumeSnippet.Kind.other),
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
            self.stdout.write(f"  [{lang}] {title}")

        self.stdout.write(
            self.style.SUCCESS(f"Done — {created} created, {updated} updated.")
        )
