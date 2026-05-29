"""Smoke-test the CV filtering pipeline against a job posting.

Usage:
    python manage.py cv_test --user 1 --job-file path/to/posting.txt
    python manage.py cv_test --user 1 --job "Senior Python engineer with Django..."

Runs one pass per filtering mode and exports a Markdown CV for each, so the
results can be diffed side-by-side:

    1. deterministic_filter        → cv_deterministic.md
    2. agentic_tailor (llm=default) → cv_default.md
    3..N. agentic_tailor for every LLMConfig alias the user has registered
          (excluding "default", since that's covered above) → cv_<alias>.md
"""

import logging
import re
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from jac.cv import CV
from jac.render import CvRender
from llm_connector.models import LLMConfig

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_OUT_DIR = _REPO_ROOT / "data"


def _format_entry(obj) -> str:
    score = getattr(obj, "relevance_score", None)
    reason = getattr(obj, "relevance_reason", None)
    label = _entry_label(obj)
    if score is not None:
        return f"    [{score:.2f}] {label}" + (f"  — {reason}" if reason else "")
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


def _safe_filename(alias: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", alias).strip("_")
    return cleaned or "alias"


class Command(BaseCommand):
    help = (
        "Compare CV filtering modes against a job posting: deterministic, "
        "the default LLM alias, and every other LLMConfig alias the user has."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="User pk")
        parser.add_argument("--job", type=str, help="Job posting text (inline)")
        parser.add_argument(
            "--job-file", type=str, help="Path to a file containing the job posting"
        )
        parser.add_argument(
            "--threshold",
            type=float,
            default=0.25,
            help="Relevance score threshold for AI filtering (default 0.25)",
        )
        parser.add_argument(
            "--out-dir",
            type=str,
            default=str(_DEFAULT_OUT_DIR),
            help=f"Directory for exported MDs (default: {_DEFAULT_OUT_DIR})",
        )
        parser.add_argument(
            "--aliases",
            nargs="*",
            help=(
                "Restrict the per-alias loop to these LLMConfig aliases "
                "(default: every alias on the user's account except 'default')."
            ),
        )
        parser.add_argument(
            "--skip-deterministic",
            action="store_true",
            help="Skip the deterministic pass.",
        )
        parser.add_argument(
            "--skip-default",
            action="store_true",
            help="Skip the 'default' alias pass.",
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
        threshold = options["threshold"]
        out_dir = Path(options["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        write = lambda msg="": self.stdout.write(msg)

        # Route jac.cv INFO logs to this command's stdout
        _handler = logging.StreamHandler(self.stdout)
        _handler.setFormatter(logging.Formatter("  [cv] %(message)s"))
        _jac_logger = logging.getLogger("jac.cv")
        _jac_logger.setLevel(logging.INFO)
        _jac_logger.addHandler(_handler)
        _jac_logger.propagate = False

        write(f"Job posting ({len(job_text)} chars):")
        write("-" * 72)
        write(job_text[:500] + ("..." if len(job_text) > 500 else ""))
        write(f"\nExporting to: {out_dir}")

        step = 0
        exported: list[Path] = []

        # 1. Deterministic
        if not options["skip_deterministic"]:
            step += 1
            cv = CV(user_pk=user_pk)
            t0 = time.monotonic()
            keywords = cv.deterministic_filter(job_text)
            elapsed = time.monotonic() - t0
            write(f"\nKeywords used ({len(keywords)}): {keywords}")
            _print_pass(self.stdout, f"{step}. DETERMINISTIC ({elapsed:.1f}s)", cv)
            exported.append(self._export(cv, out_dir, "cv_deterministic.md", write))

        # 2. Default alias — note this transparently falls back to
        # settings.LLM["default"] if the user has no LLMConfig(alias="default")
        if not options["skip_default"]:
            step += 1
            path = self._run_ai_pass(
                user_pk, job_text, threshold, "default",
                out_dir, "cv_default.md", write, step,
            )
            if path:
                exported.append(path)

        # 3..N. Every other alias the user has registered.
        requested = options.get("aliases")
        if requested:
            aliases = list(requested)
        else:
            aliases = list(
                LLMConfig.objects.filter(user_id=user_pk)
                .exclude(alias="default")
                .order_by("alias")
                .values_list("alias", flat=True)
            )

        if not aliases:
            write("\nNo additional LLMConfig aliases found for this user.")
        for alias in aliases:
            step += 1
            filename = f"cv_{_safe_filename(alias)}.md"
            path = self._run_ai_pass(
                user_pk, job_text, threshold, alias,
                out_dir, filename, write, step,
            )
            if path:
                exported.append(path)

        write("\n" + "=" * 72)
        write("EXPORT SUMMARY")
        write("=" * 72)
        for p in exported:
            write(f"  {p}")
        write("")

    def _run_ai_pass(
        self,
        user_pk: int,
        job_text: str,
        threshold: float,
        alias: str,
        out_dir: Path,
        filename: str,
        write,
        step: int,
    ) -> Path | None:
        cv = CV(user_pk=user_pk)
        write(f"\n→ pass {step}: agentic_tailor(llm={alias!r}) ...")
        t0 = time.monotonic()
        try:
            analysis = cv.agentic_tailor(job_text, llm=alias, threshold=threshold)
        except Exception as exc:
            write(f"  FAILED in {time.monotonic()-t0:.1f}s: {exc}")
            return None
        elapsed = time.monotonic() - t0
        write("\n--- Job analysis ---")
        for key, value in analysis.items():
            write(f"  {key}: {value}")
        _print_pass(
            self.stdout,
            f"{step}. AGENTIC TAILOR — {alias} ({elapsed:.1f}s, threshold={threshold})",
            cv,
        )
        return self._export(cv, out_dir, filename, write)

    def _export(self, cv: CV, out_dir: Path, filename: str, write) -> Path:
        renderer = CvRender(cv)
        path = out_dir / filename
        path.write_text(renderer.export_md(), encoding="utf-8")
        write(f"  MD → {path}")
        return path
