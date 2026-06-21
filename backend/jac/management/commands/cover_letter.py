"""Smoke-test the cover-letter pipeline over one or more postings.

Per posting: runs the CV filter, extracts the recipient address from the posting text, builds
a cover letter, and writes a `<alias>__<slug>.cover.md` artifact. Transient by default (no DB
rows for JobPosting/JobPostAddress); pass --persist to save them for inspection in admin.

Grade & model selection mirror cv_eval: --llm picks the LLMConfig alias (default "default"),
--grade forces a grade (else auto-detected from the model's strength).

Usage:
    python manage.py cover_letter_test --user 1 --job-file data/test_job.md
    python manage.py cover_letter_test --user 1 --jobs-dir data/postings --grade standard
    python manage.py cover_letter_test --user 1 --job-file p.md --llm reasoning --persist
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from llm_connector.conf import get_alias_strength

from jac.cover_letter import CoverLetter
from jac.cv import CV
from jac.llm_prompts import AddressExtract
from jac.models import JobPostAddress, JobPosting

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ADDRESS_FIELDS = (
    "company",
    "contact_name",
    "street",
    "address_line2",
    "zip",
    "city",
    "country",
    "email",
    "phone",
)


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned or "posting"


class Command(BaseCommand):
    help = "Build cover letters for a postings corpus and write the artifacts."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="User pk")
        parser.add_argument(
            "--jobs-dir", type=str, help="Directory of *.txt / *.md postings"
        )
        parser.add_argument("--job-file", type=str, help="A single posting file")
        parser.add_argument(
            "--grade",
            type=str,
            default=None,
            choices=["light", "standard", "strong"],
            help="Force a weave grade. Omit to auto-detect from the model.",
        )
        parser.add_argument(
            "--llm",
            type=str,
            default="default",
            help="LLMConfig alias to use (default 'default').",
        )
        parser.add_argument(
            "--out-dir",
            type=str,
            default=None,
            help="Output dir (default: data/cover_letters/<UTC-timestamp>)",
        )
        parser.add_argument(
            "--persist",
            action="store_true",
            help="Save JobPosting + JobPostAddress rows instead of transient.",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Run the faithfulness/grounding check on each generated body.",
        )
        parser.add_argument(
            "--verifier-llm",
            type=str,
            default=None,
            help="LLMConfig alias for the grounding check (default: same as --llm). "
            "Point at a STRONG model — a weak writer cannot fact-check itself.",
        )

    def handle(self, *args, **opts):
        write = self.stdout.write
        user = User.objects.filter(pk=opts["user"]).first()
        if not user:
            raise CommandError(f"No user with pk={opts['user']}")

        postings: list[tuple[str, str]] = []
        if opts["job_file"]:
            p = Path(opts["job_file"])
            if not p.exists():
                raise CommandError(f"Not found: {p}")
            postings.append((_safe(p.stem), p.read_text()))
        if opts["jobs_dir"]:
            d = Path(opts["jobs_dir"])
            if not d.is_dir():
                raise CommandError(f"Not a directory: {d}")
            files = sorted([*d.glob("*.txt"), *d.glob("*.md")])
            if not files:
                raise CommandError(f"No *.txt/*.md postings in {d}")
            postings.extend((_safe(f.stem), f.read_text()) for f in files)
        if not postings:
            raise CommandError("Provide --jobs-dir or --job-file.")

        alias = opts["llm"]
        grade = opts["grade"] or get_alias_strength(alias, user=user)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = (
            Path(opts["out_dir"])
            if opts["out_dir"]
            else _REPO_ROOT / "data" / "cover_letters" / stamp
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        write(
            f"cover_letter_test — {len(postings)} posting(s)  alias={alias} grade={grade}"
        )
        write(f"  user={user.pk}  → {out_dir}\n")

        for slug, text in postings:
            self._one(
                user,
                slug,
                text,
                alias,
                grade,
                opts["persist"],
                out_dir,
                write,
                opts["verify"],
                opts["verifier_llm"],
            )

    def _one(
        self,
        user,
        slug,
        text,
        alias,
        grade,
        persist,
        out_dir,
        write,
        verify,
        verifier_alias,
    ):
        cv = CV(user_pk=user.pk)
        cv.apply_selection(cv.filter_cv(text, grade=grade, alias=alias))

        extracted = AddressExtract(text, alias=alias, user=user).extract()
        jp = JobPosting(
            user=user,
            title=extracted.get("title", ""),
            posting_text=text,
            language=extracted.get("language", "en"),
        )
        addr = JobPostAddress(**{f: extracted.get(f, "") for f in _ADDRESS_FIELDS})
        if persist:
            jp.save()
            addr.job_posting = jp
            addr.save()

        result = CoverLetter(
            user,
            jp,
            cv,
            address=addr,
            grade=grade,
            alias=alias,
            verify_grounding=verify,
            verifier_alias=verifier_alias,
        ).build()

        header_lines = [f"> AI share: {result['ai_share']:.0%}"]
        header_lines.append(self._grounding_line(result["grounding"]))
        for claim in result["grounding"]["claims"]:
            header_lines.append(f">   - {claim}")
        header = "\n".join(header_lines) + "\n\n"

        stem = f"{_safe(alias)}__{slug}"
        (out_dir / f"{stem}.cover.md").write_text(
            header + result["text"], encoding="utf-8"
        )
        write(
            f"  {slug:<28} {len(result['snippets_used'])} snippet(s), "
            f"recipient={result['recipient']['company'] or '—'}"
            + ("  [persisted]" if persist else "")
            + f"  AI share: {result['ai_share']:.0%}"
            + f"  |  {self._grounding_line(result['grounding']).lstrip('> ')}"
        )

    @staticmethod
    def _grounding_line(grounding: dict) -> str:
        count = grounding["count"]
        if count is None:
            return "> Grounding: not checked"
        if count == 0:
            return "> Grounding: ✓ all claims supported"
        return f"> Grounding: ⚠ {count} unsupported claim(s)"
