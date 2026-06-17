"""Batch-evaluate the CV pipeline over a corpus of job postings.

Runs CV.filter_cv(grade) + apply_selection over a directory of postings and writes a comparable
findings artifact, so a pipeline change can be measured before/after.

Usage:
    python manage.py cv_eval --user 1 --jobs-dir data/postings
    python manage.py cv_eval --user 1 --job-file data/test_job.md
    python manage.py cv_eval --user 1 --jobs-dir data/postings --grade standard
    python manage.py cv_eval --user 1 --jobs-dir data/postings \
        --compare data/eval/<earlier-run>/findings.json
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from jac.cv import CV
from jac.render import CvRender

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SECTIONS = ["skills", "jobs", "educations", "certifications", "projects", "languages"]
_GRADES = ["light", "standard", "strong"]


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned or "posting"


class Command(BaseCommand):
    help = "Run the production CV fallback pipeline over a postings corpus and write findings."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="User pk")
        parser.add_argument(
            "--jobs-dir", type=str, help="Directory of *.txt / *.md postings"
        )
        parser.add_argument(
            "--job-file", type=str, help="A single posting file (quick check)"
        )
        parser.add_argument(
            "--grade", type=str, default="light", choices=_GRADES, help="Filter grade"
        )
        parser.add_argument(
            "--out-dir",
            type=str,
            default=None,
            help="Output dir (default: data/eval/<UTC-timestamp>)",
        )
        parser.add_argument(
            "--compare",
            type=str,
            default=None,
            help="Path to a prior findings.json to diff against",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show jac.cv DEBUG logs",
        )

    def handle(self, *args, **opts):
        write = lambda m="": self.stdout.write(m)

        # Resolve the posting set.
        postings: list[tuple[str, str]] = []  # (slug, text)
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

        # Output dir.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = (
            Path(opts["out_dir"])
            if opts["out_dir"]
            else _REPO_ROOT / "data" / "eval" / stamp
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        # Route jac.cv logs to stdout so you can watch tier/round behaviour.
        handler = logging.StreamHandler(self.stdout)
        handler.setFormatter(logging.Formatter("    [cv] %(message)s"))
        log = logging.getLogger("jac.cv")
        log.setLevel(logging.DEBUG if opts["verbose"] else logging.INFO)
        log.addHandler(handler)
        log.propagate = False

        meta = {
            "timestamp": stamp,
            "user": opts["user"],
            "grade": opts["grade"],
        }
        write(f"cv_eval — {len(postings)} posting(s) → {out_dir}")
        write(f"  user={meta['user']} grade={meta['grade']}\n")

        rows = [
            self._evaluate(opts["user"], text, slug, opts["grade"], out_dir, write)
            for slug, text in postings
        ]

        self._write_findings(rows, out_dir, meta)
        write(f"\n  findings → {out_dir / 'findings.md'}")

        if opts["compare"]:
            self._compare(opts["compare"], rows, write)

    def _evaluate(self, user_pk, job_text, slug, grade, out_dir, write):
        cv = CV(user_pk=user_pk)
        t0 = time.monotonic()
        try:
            selection = cv.filter_cv(job_text, grade=grade)
            cv.apply_selection(selection)
        except Exception as exc:
            write(f"  {slug:<28} ERROR: {exc}")
            return {
                "posting": slug,
                "grade": "error",
                "error": str(exc),
                "elapsed_s": round(time.monotonic() - t0, 1),
                "total": 0,
                "counts": {s: 0 for s in _SECTIONS},
            }
        elapsed = time.monotonic() - t0

        (out_dir / f"{slug}.cv.md").write_text(
            CvRender(cv).export_md(), encoding="utf-8"
        )

        counts = {s: len(cv.entries.get(s, [])) for s in _SECTIONS}
        row = {
            "posting": slug,
            "grade": grade,
            "elapsed_s": round(elapsed, 1),
            "total": sum(counts.values()),
            "counts": counts,
        }
        write(
            f"  {slug:<28} grade={row['grade']:<10}{elapsed:>6.1f}s  total={row['total']}"
        )
        return row

    def _write_findings(self, rows, out_dir, meta):
        (out_dir / "findings.json").write_text(
            json.dumps(rows, indent=2), encoding="utf-8"
        )
        header = f"user={meta['user']}  grade={meta['grade']}  postings={len(rows)}"
        lines = [
            f"# CV eval — {meta['timestamp']}",
            "",
            header,
            "",
            "| posting | grade | total | skills | jobs | edu | certs | proj | lang | elapsed |",
            "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
        ]
        for r in rows:
            c = r["counts"]
            lines.append(
                f"| {r['posting']} | {r['grade']} | {r['total']} | "
                f"{c['skills']} | {c['jobs']} | {c['educations']} | {c['certifications']} | "
                f"{c['projects']} | {c['languages']} | {r['elapsed_s']}s |"
            )
        (out_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _compare(self, prev_path, rows, write):
        prev = {r["posting"]: r for r in json.loads(Path(prev_path).read_text())}
        write("\n" + "=" * 72)
        write(f"COMPARE vs {prev_path}")
        write("=" * 72)
        for r in rows:
            p = prev.get(r["posting"])
            if not p:
                write(f"  {r['posting']:<28} (new — no baseline)")
                continue
            d_total = r["total"] - p["total"]
            d_time = r["elapsed_s"] - p["elapsed_s"]
            prev_grade = p.get("grade", p.get("tier"))
            cur_grade = r.get("grade")
            grade = (
                ""
                if prev_grade == cur_grade
                else f"   grade {prev_grade} → {cur_grade}"
            )
            write(
                f"  {r['posting']:<28} total {p['total']}→{r['total']} ({d_total:+d})  "
                f"time {d_time:+.1f}s{grade}"
            )
