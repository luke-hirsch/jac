"""Batch-evaluate the SHIPPED CV pipeline over a corpus of job postings.

Unlike cv_test (which probes agentic_tailor per alias on ONE posting), this runs
CV.ai_tailor_with_fallback over a directory of postings and writes a comparable
findings artifact, so a pipeline change can be measured before/after.

Usage:
    python manage.py cv_eval --user 1 --jobs-dir data/postings
    python manage.py cv_eval --user 1 --job-file data/test_job.md
    python manage.py cv_eval --user 1 --jobs-dir data/postings --llm ollama
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
            "--llm", type=str, default="default", help="LLM alias (default: 'default')"
        )
        parser.add_argument("--threshold", type=float, default=0.25)
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
            help="Show jac.cv DEBUG logs (tier selection, distill rounds)",
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
            "llm": opts["llm"],
            "threshold": opts["threshold"],
        }
        write(f"cv_eval — {len(postings)} posting(s) → {out_dir}")
        write(
            f"  user={meta['user']} llm={meta['llm']} threshold={meta['threshold']}\n"
        )

        rows = [
            self._evaluate(
                opts["user"], text, slug, opts["llm"], opts["threshold"], out_dir, write
            )
            for slug, text in postings
        ]

        self._write_findings(rows, out_dir, meta)
        write(f"\n  findings → {out_dir / 'findings.md'}")

        if opts["compare"]:
            self._compare(opts["compare"], rows, write)

    def _evaluate(self, user_pk, job_text, slug, llm, threshold, out_dir, write):
        cv = CV(user_pk=user_pk)
        t0 = time.monotonic()
        try:
            result = cv.ai_tailor_with_fallback(job_text, llm=llm, threshold=threshold)
        except Exception as exc:
            write(f"  {slug:<28} ERROR: {exc}")
            return {
                "posting": slug,
                "tier": "error",
                "error": str(exc),
                "elapsed_s": round(time.monotonic() - t0, 1),
                "total": 0,
                "counts": {s: 0 for s in _SECTIONS},
                "n_keywords": None,
                "n_selected": None,
            }
        elapsed = time.monotonic() - t0

        (out_dir / f"{slug}.cv.md").write_text(
            CvRender(cv).export_md(), encoding="utf-8"
        )

        counts = {s: len(cv.entries.get(s, [])) for s in _SECTIONS}
        row = {
            "posting": slug,
            "tier": result["tier"],
            "elapsed_s": round(elapsed, 1),
            "total": sum(counts.values()),
            "counts": counts,
            "n_keywords": len(result["keywords"]) if result.get("keywords") else None,
            "n_selected": len(result["selection"]) if result.get("selection") else None,
        }
        write(
            f"  {slug:<28} tier={row['tier']:<14}{elapsed:>6.1f}s  total={row['total']}"
        )
        return row

    def _write_findings(self, rows, out_dir, meta):
        (out_dir / "findings.json").write_text(
            json.dumps(rows, indent=2), encoding="utf-8"
        )
        header = (
            f"user={meta['user']}  llm={meta['llm']}  "
            f"threshold={meta['threshold']}  postings={len(rows)}"
        )
        lines = [
            f"# CV eval — {meta['timestamp']}",
            "",
            header,
            "",
            "| posting | tier | total | skills | jobs | edu | certs | proj | lang | elapsed |",
            "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
        ]
        for r in rows:
            c = r["counts"]
            lines.append(
                f"| {r['posting']} | {r['tier']} | {r['total']} | "
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
            tier = (
                "" if p["tier"] == r["tier"] else f"   tier {p['tier']} → {r['tier']}"
            )
            write(
                f"  {r['posting']:<28} total {p['total']}→{r['total']} ({d_total:+d})  "
                f"time {d_time:+.1f}s{tier}"
            )
