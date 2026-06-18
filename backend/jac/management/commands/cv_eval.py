"""Batch-evaluate the CV pipeline over a corpus of job postings.

Runs CV.filter_cv(grade) + apply_selection over a directory of postings and writes a comparable
findings artifact, so a pipeline change can be measured before/after. Per posting it also reports
the rank + relevance score of every kept entry, and colour-grades each section's kept-count against
an assumed one-page target (green = on target, red = undershoot, blue = overshoot).

Model & grade selection (the grade×llm matrix, see _resolve_runs):
    - no flag (in a terminal): an interactive questionnaire (choose models / grade / analysis).
    - no flag (scripted):      the 'default' alias at its auto-detected grade.
    - --pick:           force the questionnaire even alongside other flags.
    - --llm <alias>:    that model, grade auto-detected from it.
    - --grade <g>:      every configured model, each forced to grade <g> (compare models).
    - --all-models:     every configured model + the 'default' embedding baseline, own grades.
    - both:             that model at that grade.

Usage:
    python manage.py cv_eval --user 1 --jobs-dir data/postings
    python manage.py cv_eval --user 1 --job-file data/test_job.md
    python manage.py cv_eval --user 1 --job-file data/test_job.md --llm reasoning
    python manage.py cv_eval --user 1 --jobs-dir data/postings --grade standard
    python manage.py cv_eval --user 1 --job-file data/test_job.md --show-ranks
    python manage.py cv_eval --user 1 --jobs-dir data/postings \
        --compare data/eval/<earlier-run>/findings.json
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from llm_connector.conf import get_alias_strength
from llm_connector.models import LLMConfig

from jac.cv import CV
from jac.llm_prompts import TheAnalyst, TheJudge
from jac.render import CvRender

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SECTIONS = ["skills", "jobs", "educations", "certifications", "projects", "languages"]
_GRADES = ["light", "standard", "strong"]

# Assumed number of entries per section that fills a tidy one-page CV. Hitting the number is the
# goal (green); fewer trends red, more trends blue. Tune as the render/format phase firms up.
_ONE_PAGE_TARGET = {
    "skills": 10,
    "jobs": 4,
    "educations": 2,
    "certifications": 3,
    "projects": 3,
    "languages": 3,
}

# Truecolor anchors for the count gradient (R, G, B).
_C_GREEN = (60, 200, 60)
_C_RED = (210, 70, 70)
_C_BLUE = (80, 130, 230)


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned or "posting"


def _resolve_runs(grade, llm, aliases, strength_of, all_models=False):
    """Return [(alias, grade)] per the grade×llm matrix.

    - all_models:           run EVERY configured alias at its own autodetected grade.
    - llm given:            run that alias (grade as given, else autodetected).
    - grade only:           run every configured alias forced to that grade (compare models).
    - neither:              run the 'default' alias at its autodetected grade.

    `aliases` is the user's configured alias list (consulted in the all_models / grade-only cases);
    `strength_of` is a callable(alias) -> autodetected grade.
    """
    if all_models:
        return [(a, strength_of(a)) for a in aliases]
    if llm:
        return [(llm, grade or strength_of(llm))]
    if grade:
        return [(a, grade) for a in aliases]
    return [("default", strength_of("default"))]


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    """Component-wise linear interpolation between two RGB tuples."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _grade_rgb(count: int, target: int) -> tuple:
    """Green on target; fades toward red when undershooting, blue when overshooting.

    Deviation is normalised against the target (floored at 3) so a small target doesn't snap to
    full saturation on a single-entry miss.
    """
    if not target or count == target:
        return _C_GREEN
    frac = min(1.0, abs(count - target) / max(target, 3))
    return _lerp(_C_GREEN, _C_RED if count < target else _C_BLUE, frac)


def _colorize(text: str, rgb: tuple, enabled: bool) -> str:
    if not enabled:
        return text
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


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
            "--grade",
            type=str,
            default=None,
            choices=_GRADES,
            help="Force a filter grade. Omit to auto-detect from the model's strength. "
            "Given alone (no --llm), runs every configured model at this grade.",
        )
        parser.add_argument(
            "--llm",
            type=str,
            default=None,
            help="LLMConfig alias to run (e.g. 'reasoning'). Omit to use 'default'. "
            "Given alone (no --grade), the grade is auto-detected from the model.",
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
            "--show-ranks",
            action="store_true",
            help="Print every kept entry's rank+score to the terminal (always written to file)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show jac.cv DEBUG logs",
        )
        parser.add_argument(
            "--all-models",
            action="store_true",
            help="Run every configured model at its own auto-detected grade (ignores "
            "--grade/--llm). The natural sweep for --analyze.",
        )
        parser.add_argument(
            "--analyze",
            action="store_true",
            help="After the run, have a strong LLM judge each selection (kept vs dropped vs the "
            "posting) and write a cross-run analysis.md. Costs N postings × M models calls.",
        )
        parser.add_argument(
            "--analyst",
            type=str,
            default=None,
            help="LLMConfig alias used as the judge/analyst for --analyze. "
            "Omit to use the strongest configured model.",
        )
        parser.add_argument(
            "--pick",
            action="store_true",
            help="Interactive questionnaire: choose which models to evaluate, the grade, and "
            "whether to run the AI analysis. Also runs automatically when no selection flag "
            "(--llm/--grade/--all-models) is given in a terminal.",
        )

    def handle(self, *args, **opts):
        write = lambda m="": self.stdout.write(m)
        isatty = getattr(self.stdout, "isatty", None)
        out_tty = bool(isatty and isatty())
        color = (not opts["no_color"]) and out_tty
        # Only prompt when both ends are a real terminal — keeps scripted/test runs from blocking.
        interactive = out_tty and bool(sys.stdin and sys.stdin.isatty())

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

        # Resolve which (model, grade) pairs to run.
        configured = list(
            LLMConfig.objects.filter(user=opts["user"]).values_list("alias", flat=True)
        ) or ["default"]
        # 'default' is the embedding-only `light` baseline (the server embedder); offer it in the
        # picker and fold it into --all-models so it shows as its own row.
        menu = configured if "default" in configured else ["default"] + configured
        strength_of = lambda a: get_alias_strength(a, user=opts["user"])

        no_flags = not (opts["llm"] or opts["grade"] or opts["all_models"])
        if opts["pick"] or (no_flags and interactive):
            runs, q_analyze = self._interactive_setup(menu, strength_of, interactive, write)
            analyze = opts["analyze"] or q_analyze
        else:
            aliases = menu if opts["all_models"] else configured
            runs = _resolve_runs(
                opts["grade"], opts["llm"], aliases, strength_of,
                all_models=opts["all_models"],
            )
            analyze = opts["analyze"]

        meta = {
            "timestamp": stamp,
            "user": opts["user"],
            "runs": [{"model": a, "grade": g} for a, g in runs],
            "targets": _ONE_PAGE_TARGET,
        }
        runs_str = "  ".join(f"{a}:{g}" for a, g in runs)
        targets_str = "  ".join(f"{s}={_ONE_PAGE_TARGET[s]}" for s in _SECTIONS)
        write(
            f"cv_eval — {len(postings)} posting(s) × {len(runs)} model(s) → {out_dir}"
        )
        write(f"  user={meta['user']} runs: {runs_str}")
        write(f"  one-page targets: {targets_str}\n")

        rows = [
            self._evaluate(
                opts["user"],
                text,
                slug,
                grade,
                alias,
                out_dir,
                write,
                color,
                opts["show_ranks"],
            )
            for alias, grade in runs
            for slug, text in postings
        ]

        # Run the judge + cross-model summary BEFORE writing findings, so the judge grade lands in
        # the findings table and the summary at the bottom of findings.md.
        analyst_summary = ""
        if analyze:
            analyst = opts["analyst"] or next(
                (a for a in menu if strength_of(a) == "strong"),
                "default",
            )
            slug_text = {slug: text for slug, text in postings}
            analyst_summary = self._analyze(
                rows, slug_text, analyst, opts["user"], out_dir, write
            )

        self._write_findings(rows, out_dir, meta, analyst_summary)
        write(f"\n  findings → {out_dir / 'findings.md'}")

        if opts["compare"]:
            self._compare(opts["compare"], rows, write)

    def _interactive_setup(self, menu, strength_of, interactive, write):
        """Questionnaire run when no selection flag is given (or --pick): choose models, grade, and
        whether to run the AI analysis. Returns (runs, analyze).

        Non-interactive (no TTY) falls back to the plain 'default' run so scripts/CI never block.
        """
        if not interactive:
            return [("default", strength_of("default"))], False

        write("\nWhich LLMs do you want to evaluate?")
        for i, a in enumerate(menu, 1):
            write(f"  {i}) {a:<14} auto-grade: {strength_of(a)}")
        chosen = self._parse_pick(
            input("  numbers (comma/space separated), or 'all' [all]: "), menu
        )

        forced = {"l": "light", "s": "standard", "g": "strong"}.get(
            input(
                "  grade — [a]uto per model / [l]ight / [s]tandard / stron[g] [a]: "
            ).strip().lower()
        )
        analyze = input(
            "  run AI judge + summary analysis? costs N×M LLM calls [y/N]: "
        ).strip().lower() in ("y", "yes")

        write("")
        return [(a, forced or strength_of(a)) for a in chosen], analyze

    @staticmethod
    def _parse_pick(raw, menu):
        """Parse a '1,3' / '1 3' / 'all' selection into an ordered, de-duped alias list.

        Empty or 'all' -> the whole menu; out-of-range/garbage tokens are ignored; nothing
        valid -> the whole menu (safer than an empty run).
        """
        raw = (raw or "").strip()
        if not raw or raw.lower() == "all":
            return list(menu)
        picked = []
        for tok in re.split(r"[,\s]+", raw):
            if tok.isdigit() and 1 <= int(tok) <= len(menu):
                a = menu[int(tok) - 1]
                if a not in picked:
                    picked.append(a)
        return picked or list(menu)

    def _evaluate(
        self, user_pk, job_text, slug, grade, alias, out_dir, write, color, show_ranks
    ):
        # Namespace artifacts by model so a multi-model run doesn't overwrite itself.
        stem = f"{_safe(alias)}__{slug}"
        cv = CV(user_pk=user_pk)
        t0 = time.monotonic()
        try:
            selection = cv.filter_cv(job_text, grade=grade, alias=alias)
            candidates = cv._flatten_entries()  # full set + text, before pruning
            cv.apply_selection(selection)
        except Exception as exc:
            write(f"  {alias}/{slug:<28} ERROR: {exc}")
            return {
                "posting": slug,
                "model": alias,
                "grade": "error",
                "error": str(exc),
                "elapsed_s": round(time.monotonic() - t0, 1),
                "total": 0,
                "counts": {s: 0 for s in _SECTIONS},
                "ranks": {s: [] for s in _SECTIONS},
            }
        elapsed = time.monotonic() - t0

        (out_dir / f"{stem}.cv.md").write_text(
            CvRender(cv).export_md(), encoding="utf-8"
        )

        # Capture rank + score per kept entry. apply_selection has set `relevance_score` on each
        # surviving instance and left them in ranked order; CvRender._format_entry gives the label.
        renderer = CvRender(cv)
        ranks: dict[str, list[dict]] = {}
        for section in _SECTIONS:
            objs = cv.entries.get(section, [])
            ranks[section] = [
                {
                    "rank": i + 1,
                    "score": getattr(o, "relevance_score", None),
                    "label": renderer._format_entry(section, o)[0],
                }
                for i, o in enumerate(objs)
            ]

        counts = {s: len(cv.entries.get(s, [])) for s in _SECTIONS}

        # Kept (ranked, per selection order) + dropped, for the --analyze judge. Text is trimmed;
        # these are stripped from findings.json (see _write_findings) to keep it lean/comparable.
        text_by_id = {c["id"]: (c.get("text") or "") for c in candidates}
        kept = [
            {"id": it["id"], "text": text_by_id[it["id"]][:300]}
            for items in selection.values()
            for it in items
            if it.get("id") in text_by_id
        ]
        kept_ids = {e["id"] for e in kept}
        dropped = [
            {"id": c["id"], "text": (c.get("text") or "")[:300]}
            for c in candidates
            if c["id"] not in kept_ids
        ]

        row = {
            "posting": slug,
            "model": alias,
            "grade": grade,
            "elapsed_s": round(elapsed, 1),
            "total": sum(counts.values()),
            "counts": counts,
            "ranks": ranks,
            "kept": kept,
            "dropped": dropped,
        }

        self._write_ranks(stem, grade, counts, ranks, out_dir)
        self._print_posting(slug, alias, elapsed, row, color, show_ranks, write)

        return row

    def _print_posting(self, slug, alias, elapsed, row, color, show_ranks, write):
        write(
            f"\n  {alias}/{slug}  ({row['grade']}, {elapsed:.1f}s, total={row['total']})"
        )
        for section in _SECTIONS:
            count = row["counts"][section]
            target = _ONE_PAGE_TARGET.get(section, 0)
            entries = row["ranks"][section]
            cell = _colorize(
                f"{count:>2}/{target:<2}", _grade_rgb(count, target), color
            )
            scores = [e["score"] for e in entries if e["score"] is not None]
            rng = f"scores {max(scores):.3f}→{min(scores):.3f}" if scores else "—"
            write(f"    {section:<14}{cell}  {rng}")
            if show_ranks:
                for e in entries:
                    s = e["score"]
                    s_txt = f"{s:.4f}" if s is not None else " n/a  "
                    write(f"        {e['rank']:>2}. {s_txt}  {e['label']}")

    def _write_ranks(self, slug, grade, counts, ranks, out_dir):
        lines = [f"# Ranks — {slug}   grade={grade}", ""]
        for section in _SECTIONS:
            target = _ONE_PAGE_TARGET.get(section, 0)
            lines.append(f"## {section}   {counts[section]}/{target}")
            entries = ranks[section]
            if not entries:
                lines.append("_(none kept)_")
                lines.append("")
                continue
            for e in entries:
                s = e["score"]
                s_txt = f"{s:.4f}" if s is not None else " n/a  "
                lines.append(f"{e['rank']:>2}. {s_txt}  {e['label']}")
            lines.append("")
        (out_dir / f"{slug}.ranks.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_findings(self, rows, out_dir, meta, summary=""):
        # Drop the heavy per-run payloads from the comparable artifact: kept/dropped (only needed
        # in-memory for --analyze) and ranks (already in *.ranks.md). Keeps findings.json small.
        slim = [
            {k: v for k, v in r.items() if k not in ("kept", "dropped", "ranks")}
            for r in rows
        ]
        (out_dir / "findings.json").write_text(
            json.dumps(slim, indent=2), encoding="utf-8"
        )

        runs_str = "  ".join(f"{r['model']}:{r['grade']}" for r in meta["runs"])
        t = meta["targets"]
        target_line = "one-page target → " + " · ".join(
            f"{s} {t[s]}" for s in _SECTIONS
        )
        lines = [
            f"# CV eval — {meta['timestamp']}",
            "",
            f"user={meta['user']}  runs={runs_str}  rows={len(rows)}",
            target_line,
            "",
        ]

        # One table per run (model+grade), grouped in first-seen order — so each model reads as its
        # own block, then the cross-model summary lands at the bottom.
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r.get("model", "?"), r["grade"]), []).append(r)
        for (model, grade), grp in groups.items():
            lines += [
                f"## {model} ({grade})",
                "",
                "| posting | total | skills | jobs | edu | certs | proj | lang | judge | elapsed |",
                "|---|--:|--:|--:|--:|--:|--:|--:|:--:|--:|",
            ]
            for r in grp:
                c = r["counts"]
                lines.append(
                    f"| {r['posting']} | {r['total']} | {c['skills']} | {c['jobs']} | "
                    f"{c['educations']} | {c['certifications']} | {c['projects']} | "
                    f"{c['languages']} | {r.get('judge') or '—'} | {r['elapsed_s']}s |"
                )
            lines.append("")

        if summary:
            lines += ["## AI summary", "", summary.strip(), ""]
        else:
            lines += [
                "_AI summary: re-run with `--analyze` for a cross-model judgment._",
                "",
            ]
        (out_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _compare(self, prev_path, rows, write):
        prev = {
            (r.get("model", "?"), r["posting"]): r
            for r in json.loads(Path(prev_path).read_text())
        }
        write("\n" + "=" * 72)
        write(f"COMPARE vs {prev_path}")
        write("=" * 72)
        for r in rows:
            label = f"{r.get('model', '?')}/{r['posting']}"
            p = prev.get((r.get("model", "?"), r["posting"]))
            if not p:
                write(f"  {label:<28} (new — no baseline)")
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
                f"  {label:<28} total {p['total']}→{r['total']} ({d_total:+d})  "
                f"time {d_time:+.1f}s{grade}"
            )

    def _analyze(self, rows, slug_text, analyst_alias, user, out_dir, write):
        """Judge each run's selection (kept vs dropped vs posting), then summarise the whole sweep.

        Attaches each run's letter grade to its row (`row['judge']`, surfaced in findings.md),
        writes one `*.judge.md` per run and a single `analysis.md`, and RETURNS the cross-model
        summary text (so findings.md can carry it). The judge + analyst both run under
        `analyst_alias` (a fixed strong grader). Error rows are skipped. '' when nothing to judge.
        """
        write("\n" + "=" * 72)
        write(f"AI ANALYSIS — judge + summary via '{analyst_alias}'")
        write("=" * 72)
        blocks = []
        for r in rows:
            label = f"{r.get('model', '?')}/{r['posting']}"
            if r.get("grade") == "error":
                write(f"  {label:<28} skipped (run errored)")
                continue
            verdict = TheJudge(
                slug_text.get(r["posting"], ""),
                r.get("kept", []),
                r.get("dropped", []),
                user=user,
                alias=analyst_alias,
            ).critique()
            r["judge"] = verdict["grade"]  # surfaced in the findings.md judge column
            self._write_judge(r, verdict, out_dir)
            write(
                f"  {label:<28} grade {verdict['grade'] or '?'}  "
                f"({len(verdict['notes'])} note(s))"
            )
            blocks.append(self._run_block(r, verdict))

        if not blocks:
            write("\n  analysis skipped (no successful runs to judge)")
            return ""

        summary = TheAnalyst(
            "\n\n".join(blocks), user=user, alias=analyst_alias
        ).analyse()
        if summary:
            (out_dir / "analysis.md").write_text(
                f"# CV eval — AI analysis (judge: {analyst_alias})\n\n{summary}\n",
                encoding="utf-8",
            )
            write(f"\n  analysis → {out_dir / 'analysis.md'}")
        else:
            write("\n  analysis skipped (summary call returned nothing)")
        return summary

    def _run_block(self, r, verdict):
        """One run's block for the Analyst's input: counts vs target + judge grade/notes."""
        c = r["counts"]
        counts = "  ".join(f"{s}={c[s]}/{_ONE_PAGE_TARGET[s]}" for s in _SECTIONS)
        lines = [
            f"## {r.get('model', '?')} / {r['posting']}  "
            f"(grade={r['grade']}, {r['elapsed_s']}s)",
            f"counts: {counts}  total={r['total']}",
            f"selection grade: {verdict['grade'] or '?'}",
        ]
        if verdict["notes"]:
            lines.append("issues:")
            lines += [f"  {n['id']} — {n['note']}" for n in verdict["notes"]]
        else:
            lines.append("issues: none flagged")
        return "\n".join(lines)

    def _write_judge(self, r, verdict, out_dir):
        stem = f"{_safe(r.get('model', '?'))}__{r['posting']}"
        lines = [
            f"# Judge — {r.get('model', '?')} / {r['posting']}",
            f"selection grade: {verdict['grade'] or '?'}",
            "",
            "## kept",
            *[f"- {e['id']} — {e.get('text', '')}" for e in r.get("kept", [])],
            "",
            "## dropped",
            *[f"- {e['id']} — {e.get('text', '')}" for e in r.get("dropped", [])],
            "",
            "## flagged",
        ]
        lines += [f"- {n['id']} — {n['note']}" for n in verdict["notes"]] or [
            "_(none)_"
        ]
        (out_dir / f"{stem}.judge.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
