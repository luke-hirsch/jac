# Phase 4a (revised) — `cv_eval`: a CV-pipeline regression harness

> **Why this is a rewrite.** The original 4a guide bundled two things: a *go/no-go
> decision* ("does the pipeline work? should we do 4b?") and a *batch evaluation
> command*. The go/no-go is **already answered** — the manual `cv_test` dogfooding run
> showed the pipeline works on a fast paid model and stalls on the local one, which is
> enough to greenlight Phase 4b. So that ceremony (and the over-built `--judge`
> LLM-critique) is dropped. What's left, and worth building, is a **small repeatable
> harness** so that *after* you change `cv.py` in 4b/4b-bis you can prove you improved
> things instead of eyeballing one posting. This guide is that, written as a
> follow-along tutorial.

---

## 1. What you're building, and the point of it

A management command, `cv_eval`, that runs the **shipped** entry point —
`CV.ai_tailor_with_fallback` — over a **folder of real postings**, and writes a
**comparable artifact** (one CV markdown per posting + a `findings.md` table + a
`findings.json`). Run it once now to capture a *baseline*; run it again after 4b and
`--compare` the two to see what moved.

How it differs from the `cv_test` you already have — this matters:

| | `cv_test` (have) | `cv_eval` (building) |
|---|---|---|
| Postings | one | a corpus (`data/postings/*`) |
| Calls | `agentic_tailor` — one tier, no fallback | **`ai_tailor_with_fallback`** — the real ladder |
| Output | eyeball MD per alias | `findings.{md,json}` you can diff + re-run |
| Use | "what does model X do here?" | "did my 4b change regress anything?" |

The single most important reason this isn't redundant with your manual run: **`cv_test`
calls `agentic_tailor`, which has no fallback** ([cv_test.py:220](../../backend/jac/management/commands/cv_test.py#L220)).
That's why your local passes hard-failed. `cv_eval` calls the production path that
*degrades* (keyword → deterministic → unfiltered), so it shows what a user would
actually get — and, after 4b, that the light ladder reaches a cheap tier *fast* instead
of burning minutes first.

This guide does **not** add: the LLM-judge/critique, a per-alias sweep (that's
`cv_test`'s job), or any change to `cv.py` (that's 4b).

---

## 2. Preflight

From `backend/`:

- **Tree at a known commit.** `git log --oneline -1`. Note it here: ____.
- **Suite green.** `python manage.py test` → `Ran N tests … OK`. Note N: ____.
- **A populated CV exists for user 1.**
  ```bash
  python manage.py shell -c "from jac.cv import CV; cv=CV(user_pk=1); print({k: len(v) for k,v in cv.entries.items()})"
  ```
  → non-trivial counts in most sections.
- **The pipeline runs at all.** `python manage.py cv_test --user 1 --job-file data/test_job.md`
  produces output (even if the local LLM passes fail — that's expected pre-4b).

If any fail, stop and fix before continuing.

---

## 3. Build the corpus (`data/postings/`)

The corpus is the whole point of a *regression* harness — a single posting can't tell
you if a change generalizes.

```bash
mkdir -p data/postings
cp data/test_job.md data/postings/   # the data-center cabling job you already have
```

Then add **6–10 real postings** as `.txt` or `.md` files. Two rules:

1. **Mix match quality.** Some that fit your CV well (physics, dev, teaching, lab), a
   couple that fit only via transferable skills (like the cabling job), and one or two
   that clearly *don't*. You want to see the floor/fallback behave across the spectrum.
2. **Mix language** (German + English) — selection is language-agnostic, but you want
   the keyword/deterministic tiers exercised on German tokens too.

`data/` is already gitignored, so the corpus and run outputs stay local. Name files
descriptively (`backend_dev_berlin.md`, `physics_lab_assistant.txt`) — the stem becomes
the row label.

**Verify:** `ls data/postings/` shows your files.

---

## 4. Write the command, piece by piece

Create `backend/jac/management/commands/cv_eval.py`. We'll build it in four chunks, then
the full file is at the end of this section to copy.

### 4.1 — Header, imports, helpers

```python
"""Batch-evaluate the SHIPPED CV pipeline over a corpus of job postings.

Unlike cv_test (which probes agentic_tailor per alias on ONE posting), this runs
CV.ai_tailor_with_fallback over a directory of postings and writes a comparable
findings artifact, so a pipeline change can be measured before/after.

Usage:
    python manage.py cv_eval --user 1 --jobs-dir data/postings
    python manage.py cv_eval --user 1 --job-file data/test_job.md          # quick single
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

_REPO_ROOT = Path(__file__).resolve().parents[4]      # .../backend/jac/management/commands -> repo
_SECTIONS = ["skills", "jobs", "educations", "certifications", "projects", "languages"]


def _safe(name: str) -> str:
    """Filesystem-safe slug from a posting filename stem."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned or "posting"
```

> `_SECTIONS` is the canonical order so every findings table has the same columns.

### 4.2 — Evaluate one posting

The heart of it: a fresh `CV` (so one posting's in-place mutation can't leak into the
next), time the production call, render the result, and fold it into a row.

```python
    def _evaluate(self, user_pk, job_text, slug, llm, threshold, out_dir, write):
        cv = CV(user_pk=user_pk)
        t0 = time.monotonic()
        try:
            result = cv.ai_tailor_with_fallback(job_text, llm=llm, threshold=threshold)
        except Exception as exc:                       # the ladder shouldn't raise, but be safe
            write(f"  {slug:<28} ERROR: {exc}")
            return {"posting": slug, "tier": "error", "error": str(exc),
                    "elapsed_s": round(time.monotonic() - t0, 1),
                    "total": 0, "counts": {s: 0 for s in _SECTIONS},
                    "n_keywords": None, "n_selected": None}
        elapsed = time.monotonic() - t0

        (out_dir / f"{slug}.cv.md").write_text(CvRender(cv).export_md(), encoding="utf-8")

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
        write(f"  {slug:<28} tier={row['tier']:<14}{elapsed:>6.1f}s  total={row['total']}")
        return row
```

> Why record `tier`, `elapsed`, and per-section `counts`? Those are exactly the three
> things 4b changes: *which* tier the model reaches, *how fast*, and *how much* survives.
> A before/after diff on these is the regression signal.

### 4.3 — The corpus loop + findings writers

```python
    def _write_findings(self, rows, out_dir, meta):
        (out_dir / "findings.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

        header = (f"user={meta['user']}  llm={meta['llm']}  "
                  f"threshold={meta['threshold']}  postings={len(rows)}")
        lines = [f"# CV eval — {meta['timestamp']}", "", header, "",
                 "| posting | tier | total | skills | jobs | edu | certs | proj | lang | elapsed |",
                 "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for r in rows:
            c = r["counts"]
            lines.append(
                f"| {r['posting']} | {r['tier']} | {r['total']} | "
                f"{c['skills']} | {c['jobs']} | {c['educations']} | {c['certifications']} | "
                f"{c['projects']} | {c['languages']} | {r['elapsed_s']}s |"
            )
        (out_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

### 4.4 — Optional: `--compare` against an earlier run

This is what turns "a batch printer" into "a regression check". Skip it on the first
build if you want; add it before you start 4b.

```python
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
            tier = "" if p["tier"] == r["tier"] else f"   tier {p['tier']} → {r['tier']}"
            write(f"  {r['posting']:<28} total {p['total']}→{r['total']} ({d_total:+d})  "
                  f"time {d_time:+.1f}s{tier}")
```

### Full file

```python
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
        parser.add_argument("--jobs-dir", type=str, help="Directory of *.txt / *.md postings")
        parser.add_argument("--job-file", type=str, help="A single posting file (quick check)")
        parser.add_argument("--llm", type=str, default="default", help="LLM alias (default: 'default')")
        parser.add_argument("--threshold", type=float, default=0.25)
        parser.add_argument("--out-dir", type=str, default=None,
                            help="Output dir (default: data/eval/<UTC-timestamp>)")
        parser.add_argument("--compare", type=str, default=None,
                            help="Path to a prior findings.json to diff against")
        parser.add_argument("--verbose", action="store_true",
                            help="Show jac.cv DEBUG logs (tier selection, distill rounds)")

    def handle(self, *args, **opts):
        write = lambda m="": self.stdout.write(m)

        # Resolve the posting set.
        postings: list[tuple[str, str]] = []   # (slug, text)
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
        out_dir = Path(opts["out_dir"]) if opts["out_dir"] else _REPO_ROOT / "data" / "eval" / stamp
        out_dir.mkdir(parents=True, exist_ok=True)

        # Route jac.cv logs to stdout so you can watch tier/round behaviour.
        handler = logging.StreamHandler(self.stdout)
        handler.setFormatter(logging.Formatter("    [cv] %(message)s"))
        log = logging.getLogger("jac.cv")
        log.setLevel(logging.DEBUG if opts["verbose"] else logging.INFO)
        log.addHandler(handler)
        log.propagate = False

        meta = {"timestamp": stamp, "user": opts["user"],
                "llm": opts["llm"], "threshold": opts["threshold"]}
        write(f"cv_eval — {len(postings)} posting(s) → {out_dir}")
        write(f"  user={meta['user']} llm={meta['llm']} threshold={meta['threshold']}\n")

        rows = [self._evaluate(opts["user"], text, slug, opts["llm"],
                               opts["threshold"], out_dir, write)
                for slug, text in postings]

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
            return {"posting": slug, "tier": "error", "error": str(exc),
                    "elapsed_s": round(time.monotonic() - t0, 1),
                    "total": 0, "counts": {s: 0 for s in _SECTIONS},
                    "n_keywords": None, "n_selected": None}
        elapsed = time.monotonic() - t0

        (out_dir / f"{slug}.cv.md").write_text(CvRender(cv).export_md(), encoding="utf-8")

        counts = {s: len(cv.entries.get(s, [])) for s in _SECTIONS}
        row = {
            "posting": slug, "tier": result["tier"], "elapsed_s": round(elapsed, 1),
            "total": sum(counts.values()), "counts": counts,
            "n_keywords": len(result["keywords"]) if result.get("keywords") else None,
            "n_selected": len(result["selection"]) if result.get("selection") else None,
        }
        write(f"  {slug:<28} tier={row['tier']:<14}{elapsed:>6.1f}s  total={row['total']}")
        return row

    def _write_findings(self, rows, out_dir, meta):
        (out_dir / "findings.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        header = (f"user={meta['user']}  llm={meta['llm']}  "
                  f"threshold={meta['threshold']}  postings={len(rows)}")
        lines = [f"# CV eval — {meta['timestamp']}", "", header, "",
                 "| posting | tier | total | skills | jobs | edu | certs | proj | lang | elapsed |",
                 "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for r in rows:
            c = r["counts"]
            lines.append(
                f"| {r['posting']} | {r['tier']} | {r['total']} | "
                f"{c['skills']} | {c['jobs']} | {c['educations']} | {c['certifications']} | "
                f"{c['projects']} | {c['languages']} | {r['elapsed_s']}s |")
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
            tier = "" if p["tier"] == r["tier"] else f"   tier {p['tier']} → {r['tier']}"
            write(f"  {r['posting']:<28} total {p['total']}→{r['total']} ({d_total:+d})  "
                  f"time {d_time:+.1f}s{tier}")
```

---

## 5. Run it (follow along)

**Single posting first — prove the spine:**
```bash
python manage.py cv_eval --user 1 --job-file data/test_job.md --llm opeani
```
Expected (shape):
```
cv_eval — 1 posting(s) → /…/data/eval/20260613T…Z
  user=1 llm=opeani threshold=0.25

  test_job                     tier=conversational  25.4s  total=19

  findings → /…/data/eval/20260613T…Z/findings.md
```
Open the `data/eval/<stamp>/test_job.cv.md` — it's the same render you saw before, but
produced through `ai_tailor_with_fallback`, not `agentic_tailor`.

**Then the corpus:**
```bash
python manage.py cv_eval --user 1 --jobs-dir data/postings --llm opeani
```
You get one `*.cv.md` per posting + `findings.md` (a table) + `findings.json`. Read the
table top to bottom: every row should have a sensible tier and total. A row landing on
`deterministic`/`unfiltered` on a posting that clearly matches your CV is a finding worth
noting — the LLM tiers came back empty there.

**Verify:** `ls data/eval/<stamp>/` shows `findings.json`, `findings.md`, and one
`*.cv.md` per posting; the table row count equals the posting count.

---

## 6. Use it as a 4b before/after gate

This is the payoff. Capture a baseline **now**, on the local model, before you touch
`cv.py`:

```bash
python manage.py cv_eval --user 1 --jobs-dir data/postings --llm ollama --out-dir data/eval/baseline-ollama
```
(Expect slow rows and some `deterministic`/`unfiltered` tiers — that's the pre-4b state.)

Then implement Phase 4b, and re-run with `--compare`:
```bash
python manage.py cv_eval --user 1 --jobs-dir data/postings --llm ollama \
    --compare data/eval/baseline-ollama/findings.json
```
The compare block tells you, per posting, whether the **tier improved** (e.g.
`deterministic → keyword`), whether **timing dropped**, and whether **totals** moved
into a sane range. That's the evidence that 4b did what the guide claims — not vibes.

---

## 7. What you should have at the end

```
backend/jac/management/commands/cv_eval.py     # the command
data/postings/*.{txt,md}                        # the corpus (gitignored)
data/eval/<stamp>/{*.cv.md, findings.json, findings.md}   # run output (gitignored)
```

Commit just the command (the corpus + outputs stay gitignored):
```
Phase 4a: cv_eval — production-pipeline regression harness over a postings corpus
```

---

## 8. Deliberately dropped (and why it's fine)

- **The LLM-judge / `--judge` critique.** A single-call "rate this CV 0–1" is a smell
  test, not a benchmark, and it doubles run cost/time. Human review of the `*.cv.md`
  plus the tier/count table is the source of truth for a solo dogfood.
- **A `phase-4a-findings.md` go/no-go writeup.** Already decided manually → do 4b.
- **Per-alias sweep.** That's `cv_test`'s job; `cv_eval` deliberately runs one model at
  a time so the corpus is the variable, not the model.

---

## 9. Optional test

Keep it light: a temp dir with one tiny posting file, `ai_tailor_with_fallback` mocked
(or the LLM wrappers mocked as elsewhere in [tests.py](../../backend/jac/tests.py)),
asserting `cv_eval` writes `findings.json` + one `*.cv.md` and that the row count
matches. This guards the command's plumbing, not selection quality.

---

## 10. What's next

**Phase 4b — SLM-robust CV pipeline** ([phase-4b-setup-guide.md](phase-4b-setup-guide.md)).
Capture the `--out-dir data/eval/baseline-ollama` baseline (§6) *before* you start, so
the 4b `--compare` has something to diff against.
