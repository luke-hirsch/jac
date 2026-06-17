"""Smoke-test the CV filtering pipeline against a single job posting.

Usage:
    python manage.py cv_test --user 1 --job-file path/to/posting.txt
    python manage.py cv_test --user 1 --job "Senior Python engineer with Django..."

Runs one pass per filter grade and exports a Markdown CV for each, so the grades
can be diffed side-by-side:

    light    → cv_light.md      (embedding rank — the working floor)
    standard → cv_standard.md   (Instruct LLM rank; falls back to light until built)
    strong   → cv_strong.md     (Conversational LLM rank; falls back until built)
"""

import logging
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from jac.cv import CV
from jac.render import CvRender

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_OUT_DIR = _REPO_ROOT / "data"
_GRADES = ["light", "standard", "strong"]


def _format_entry(obj) -> str:
    score = getattr(obj, "relevance_score", None)
    label = _entry_label(obj)
    if score is not None:
        return f"    [{score:.3f}] {label}"
    return f"    - {label}"


def _entry_label(obj) -> str:
    cls = obj.__class__.__name__
    if cls == "Skill":
        return f"Skill: {obj.name} ({obj.proficiency})"
    if cls == "Job":
        return f"Job: {obj.title} @ {obj.company}"
    if cls == "Education":
        return f"Education: {obj.degree or ''} {obj.field_of_study or ''} @ {obj.institution}".strip()
    if cls == "Certification":
        return f"Cert: {obj.name} — {obj.issuer}"
    if cls == "Project":
        return f"Project: {obj.name}"
    if cls == "Language":
        return f"Language: {obj.name} ({obj.fluency})"
    return f"{cls}: {obj!r}"


def _print_pass(stdout, label: str, cv: CV) -> None:
    stdout.write("\n" + "=" * 72)
    stdout.write(label)
    stdout.write("=" * 72)
    total = 0
    for section, items in cv.entries.items():
        stdout.write(f"\n  {section}: {len(items)}")
        for obj in items:
            stdout.write(_format_entry(obj))
            total += 1
    stdout.write(f"\n  TOTAL: {total}")


class Command(BaseCommand):
    help = "Compare CV filter grades (light/standard/strong) against a job posting."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="User pk")
        parser.add_argument("--job", type=str, help="Job posting text (inline)")
        parser.add_argument(
            "--job-file", type=str, help="Path to a file containing the job posting"
        )
        parser.add_argument(
            "--grades",
            nargs="*",
            choices=_GRADES,
            help="Restrict to these grades (default: all three).",
        )
        parser.add_argument(
            "--out-dir",
            type=str,
            default=str(_DEFAULT_OUT_DIR),
            help=f"Directory for exported MDs (default: {_DEFAULT_OUT_DIR})",
        )

    def handle(self, *args, **options):
        job_text = options.get("job")
        job_file = options.get("job_file")
        if job_file:
            path = Path(job_file)
            if not path.exists():
                raise CommandError(f"Job file not found: {path}")
            job_text = path.read_text()
        if not job_text:
            raise CommandError("Provide --job or --job-file.")

        user_pk = options["user"]
        out_dir = Path(options["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        grades = options.get("grades") or _GRADES

        write = lambda msg="": self.stdout.write(msg)

        # Route jac.cv INFO logs to this command's stdout.
        handler = logging.StreamHandler(self.stdout)
        handler.setFormatter(logging.Formatter("  [cv] %(message)s"))
        jac_logger = logging.getLogger("jac.cv")
        jac_logger.setLevel(logging.INFO)
        jac_logger.addHandler(handler)
        jac_logger.propagate = False

        write(f"Job posting ({len(job_text)} chars):")
        write("-" * 72)
        write(job_text[:500] + ("..." if len(job_text) > 500 else ""))
        write(f"\nExporting to: {out_dir}")

        exported: list[Path] = []
        for step, grade in enumerate(grades, start=1):
            path = self._run_grade(user_pk, job_text, grade, out_dir, write, step)
            if path:
                exported.append(path)

        write("\n" + "=" * 72)
        write("EXPORT SUMMARY")
        write("=" * 72)
        for p in exported:
            write(f"  {p}")
        write("")

    def _run_grade(
        self, user_pk: int, job_text: str, grade: str, out_dir: Path, write, step: int
    ) -> Path | None:
        cv = CV(user_pk=user_pk)
        write(f"\n→ pass {step}: filter_cv(grade={grade!r}) ...")
        t0 = time.monotonic()
        try:
            selection = cv.filter_cv(job_text, grade=grade)
            cv.apply_selection(selection)
        except Exception as exc:
            write(f"  FAILED in {time.monotonic() - t0:.1f}s: {exc}")
            return None
        elapsed = time.monotonic() - t0
        _print_pass(self.stdout, f"{step}. GRADE={grade} ({elapsed:.1f}s)", cv)
        return self._export(cv, out_dir, f"cv_{grade}.md", write)

    def _export(self, cv: CV, out_dir: Path, filename: str, write) -> Path:
        path = out_dir / filename
        path.write_text(CvRender(cv).export_md(), encoding="utf-8")
        write(f"  MD → {path}")
        return path
