# [backend] cv_eval — per-entry rank feedback + one-page target colour grading

## Context / goal

Roadmap item **1 (CV ladder)** support tooling. The new `CVFilter` selection layer in
`backend/jac/cv.py` works — a first `cv_eval` run picks entries that align with each posting's
fit. The next thing that helps tune the pipeline is seeing **how entries rank**, not just _which_
were kept, and a quick read on whether each section is the right _length_ for a one-page CV.

This change extends the `cv_eval` management command to:

1. Report, per posting, the **rank + relevance score** of every kept entry (terminal + a durable
   `<slug>.ranks.md` artifact alongside the existing `<slug>.cv.md`).
2. Define an **assumed one-page target count per section** (`x` jobs, `y` certs, `z` skills, …).
3. **Colour-grade** each section's kept-count against its target in the terminal: green on target,
   fading to **red** as it undershoots and **blue** as it overshoots. A well-tuned run shows only
   greenish cells — red/blue flag sections that are too short or too long.

Scope is **`cv_eval` only**. `cv.py`, `render.py`, `llm_prompts.py` are untouched — the rank data
comes from the `relevance_score` attribute `CV.apply_selection` already sets on each kept instance
(`backend/jac/cv.py:285`), and entry labels are reused from `CvRender._format_entry`
(`backend/jac/render.py:67`).

## Affected files

| path                                         | change                                                                                                                                                              |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/jac/management/commands/cv_eval.py` | rewrite: target constants, colour helpers, rank capture, new per-posting terminal block, `<slug>.ranks.md` writer, `--show-ranks` flag, target row in `findings.md` |
| `backend/jac/tests.py`                       | add a `CvEvalGradingTests` class (pure helper tests) + extend the existing `cv_eval` smoke test                                                                     |

## Design notes

- **Targets** live in a module constant `_ONE_PAGE_TARGET`. These are _assumptions_ to tune later
  when the render/format phase firms up — keep them in one obvious place.
- **Colour** uses 24-bit truecolor ANSI (`\033[38;2;R;G;Bm`). It is only emitted when stdout is a
  TTY **and** `--no-color` was not passed (Django's `BaseCommand` already provides `--no-color`,
  so we reuse `opts["no_color"]` rather than add a flag). Under tests (`stdout=StringIO`) `isatty`
  is False, so output stays plain.
- The gradient normalises the deviation against the target (floored at 3) so a small target like
  `educations=2` doesn't snap to full red on a single-entry miss.
- `findings.md` is Markdown — it can't carry colour — so it gains a plain `_target_` reference row;
  the colour lives in the live terminal. The durable per-entry ranks go to `<slug>.ranks.md`.

## The code

### `backend/jac/management/commands/cv_eval.py` (full replacement)

```python
"""Batch-evaluate the CV pipeline over a corpus of job postings.

Runs CV.filter_cv(grade) + apply_selection over a directory of postings and writes a comparable
findings artifact, so a pipeline change can be measured before/after. Per posting it also reports
the rank + relevance score of every kept entry, and colour-grades each section's kept-count against
an assumed one-page target (green = on target, red = undershoot, blue = overshoot).

Usage:
    python manage.py cv_eval --user 1 --jobs-dir data/postings
    python manage.py cv_eval --user 1 --job-file data/test_job.md
    python manage.py cv_eval --user 1 --jobs-dir data/postings --grade standard
    python manage.py cv_eval --user 1 --job-file data/test_job.md --show-ranks
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
            "--show-ranks",
            action="store_true",
            help="Print every kept entry's rank+score to the terminal (always written to file)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show jac.cv DEBUG logs",
        )

    def handle(self, *args, **opts):
        write = lambda m="": self.stdout.write(m)
        isatty = getattr(self.stdout, "isatty", None)
        color = (not opts["no_color"]) and bool(isatty and isatty())

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
            "targets": _ONE_PAGE_TARGET,
        }
        targets_str = "  ".join(f"{s}={_ONE_PAGE_TARGET[s]}" for s in _SECTIONS)
        write(f"cv_eval — {len(postings)} posting(s) → {out_dir}")
        write(f"  user={meta['user']} grade={meta['grade']}")
        write(f"  one-page targets: {targets_str}\n")

        rows = [
            self._evaluate(
                opts["user"],
                text,
                slug,
                opts["grade"],
                out_dir,
                write,
                color,
                opts["show_ranks"],
            )
            for slug, text in postings
        ]

        self._write_findings(rows, out_dir, meta)
        write(f"\n  findings → {out_dir / 'findings.md'}")

        if opts["compare"]:
            self._compare(opts["compare"], rows, write)

    def _evaluate(self, user_pk, job_text, slug, grade, out_dir, write, color, show_ranks):
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
                "ranks": {s: [] for s in _SECTIONS},
            }
        elapsed = time.monotonic() - t0

        (out_dir / f"{slug}.cv.md").write_text(
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
        row = {
            "posting": slug,
            "grade": grade,
            "elapsed_s": round(elapsed, 1),
            "total": sum(counts.values()),
            "counts": counts,
            "ranks": ranks,
        }

        self._write_ranks(slug, grade, counts, ranks, out_dir)
        self._print_posting(slug, elapsed, row, color, show_ranks, write)
        return row

    def _print_posting(self, slug, elapsed, row, color, show_ranks, write):
        write(f"\n  {slug}  ({elapsed:.1f}s, total={row['total']})")
        for section in _SECTIONS:
            count = row["counts"][section]
            target = _ONE_PAGE_TARGET.get(section, 0)
            entries = row["ranks"][section]
            cell = _colorize(f"{count:>2}/{target:<2}", _grade_rgb(count, target), color)
            scores = [e["score"] for e in entries if e["score"] is not None]
            rng = (
                f"scores {max(scores):.3f}→{min(scores):.3f}" if scores else "—"
            )
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

    def _write_findings(self, rows, out_dir, meta):
        (out_dir / "findings.json").write_text(
            json.dumps(rows, indent=2), encoding="utf-8"
        )
        header = f"user={meta['user']}  grade={meta['grade']}  postings={len(rows)}"
        t = meta["targets"]
        target_row = (
            f"| _target_ |  |  | {t['skills']} | {t['jobs']} | {t['educations']} | "
            f"{t['certifications']} | {t['projects']} | {t['languages']} |  |"
        )
        lines = [
    f"# CV eval — {meta['timestamp']}",
            "",
            header,
            "",
            "| posting | grade | total | skills | jobs | edu | certs | proj | lang | elapsed |",
            "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
            target_row,
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
```

### What changed vs the current file

- New module constants `_ONE_PAGE_TARGET`, `_C_GREEN/_RED/_BLUE`; new pure helpers `_lerp`,
  `_grade_rgb`, `_colorize`.
- `handle`: compute `color`; print the targets line; add `--show-ranks`; pass `color` +
  `show_ranks` into `_evaluate`; store `targets` in `meta`.
- `_evaluate`: capture `ranks` per section from the kept instances; write `<slug>.ranks.md`; call
  the new `_print_posting` instead of the single summary line; the error path now also returns an
  empty `ranks` map.
- New `_print_posting` (colour-graded count/target + score range, optional per-entry list) and
  `_write_ranks` (durable per-posting rank file).
- `_write_findings`: add the `_target_` reference row.
- `_compare` is unchanged (still reads `total`/`elapsed_s`/`grade` — the new `ranks`/`counts`
  fields don't affect it).

## Tests

AI writes these; you run them. Add to `backend/jac/tests.py`. The first class is pure-function and
needs no DB; the smoke-test additions reuse the existing `_keep_all` patch + `CVCommandSmokeTests`
fixtures.

Add the imports near the top of `tests.py` (extend the existing `from jac...` imports — these are
new symbols):

```python
from jac.management.commands.cv_eval import (
    _C_BLUE,
    _C_GREEN,
    _C_RED,
    _ONE_PAGE_TARGET,
    _colorize,
    _grade_rgb,
)
```

New test class (place it next to `CVCommandSmokeTests`):

```python
class CvEvalGradingTests(TestCase):
    """Pure colour-grading + label helpers — no DB, no embedding."""

    def test_on_target_is_green(self):
        self.assertEqual(_grade_rgb(4, 4), _C_GREEN)

    def test_zero_target_is_green(self):
        # A section with no defined target should not be flagged.
        self.assertEqual(_grade_rgb(3, 0), _C_GREEN)

    def test_full_undershoot_is_red(self):
        # count 0, target 4 -> deviation == max(target, 3) -> frac 1.0 -> full red.
        self.assertEqual(_grade_rgb(0, 4), _C_RED)

    def test_full_overshoot_is_blue(self):
        self.assertEqual(_grade_rgb(8, 4), _C_BLUE)

    def test_mild_miss_is_between_green_and_endpoint(self):
        rgb = _grade_rgb(3, 4)  # undershoot by 1 on target 4 -> frac 0.25 toward red
        self.assertNotIn(rgb, (_C_GREEN, _C_RED))
        # Greener than full red: more green channel than the red anchor.
        self.assertGreater(rgb[1], _C_RED[1])

    def test_colorize_disabled_is_plain(self):
        self.assertEqual(_colorize("12/10", _C_RED, enabled=False), "12/10")

    def test_colorize_enabled_wraps_ansi(self):
        out = _colorize("x", _C_GREEN, enabled=True)
        self.assertTrue(out.startswith("\033[38;2;"))
        self.assertTrue(out.endswith("\033[0m"))
        self.assertIn("x", out)

    def test_targets_cover_all_sections(self):
        for section in ("skills", "jobs", "educations", "certifications", "projects", "languages"):
            self.assertIn(section, _ONE_PAGE_TARGET)
```

Extend the existing `cv_eval` smoke test (replace `test_cv_eval_writes_findings` in
`CVCommandSmokeTests` with this — it adds the new-artifact assertions):

```python
    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_eval_writes_findings_and_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "posting.md"
            job.write_text("Senior Python engineer")
            call_command(
                "cv_eval",
                "--user",
                str(self.user.pk),
                "--job-file",
                str(job),
                "--out-dir",
                tmp,
                stdout=io.StringIO(),
            )
            self.assertTrue((Path(tmp) / "findings.json").exists())
            self.assertTrue((Path(tmp) / "findings.md").exists())
            # New: per-posting ranks artifact + target reference row in findings.
            self.assertTrue((Path(tmp) / "posting.ranks.md").exists())
            self.assertIn("_target_", (Path(tmp) / "findings.md").read_text())
            ranks_md = (Path(tmp) / "posting.ranks.md").read_text()
            self.assertIn("## skills", ranks_md)
            # _keep_all scores every entry 1.0, so the kept skill shows its score.
            self.assertIn("1.0000", ranks_md)
```

## Verification

1. **Run the unit tests** (these don't need Ollama):

   ```bash
   cd backend && python manage.py test jac.tests.CvEvalGradingTests jac.tests.CVCommandSmokeTests -v 2
   ```

   Expect all green. The grading tests assert the green/red/blue endpoints; the smoke test asserts
   `posting.ranks.md`, the `_target_` row, and a `1.0000` score line.

2. **Real run against a posting** (needs the Ollama embedding model up, as before):

   ```bash
   cd backend && python manage.py cv_eval --user 1 --job-file ../data/test_job.md --show-ranks
   ```

   What "done" looks like in the terminal:
   - A `one-page targets: skills=10 jobs=4 …` line under the header.
   - Per posting, a block with one line per section like `jobs        3/4   scores 0.581→0.224`,
     where the `3/4` is **colour-graded**: green when it matches the target, reddish when short,
     bluish when over. With `--show-ranks`, each kept entry is listed beneath as
     `1. 0.5810  Senior Engineer — Acme`.
   - Tune `_ONE_PAGE_TARGET` until a good posting shows mostly green cells.

3. **Check the artifacts** in the new `data/eval/<timestamp>/` dir:
   - `<slug>.ranks.md` — ranked entries with scores per section (review like you did `<slug>.cv.md`).
   - `findings.md` — the count table now has a `_target_` row to eyeball each posting against.

4. **Colour off** (piping/redirecting, or `--no-color`) must emit plain text — confirm no `\033[`
   escapes land in a redirected file:

   ```bash
   cd backend && python manage.py cv_eval --user 1 --job-file ../data/test_job.md --no-color | cat
   ```

## After

When this lands, run `/update-claude` to move this guide to `.claude/plans/done/` and note the
eval-tooling state. No CLAUDE.md roadmap item is completed by this (it's tooling for item 1), so
the roadmap text shouldn't need changes.

```

```
