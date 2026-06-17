# [backend] update the cv_test / cv_eval commands for the refactored CV + CVFilter

**Roadmap item 1 (tooling).** The two management commands that exercise the CV pipeline —
`cv_test` (one posting, compare modes) and `cv_eval` (corpus, write findings) — still call the
pre-refactor API and are broken:

- `CV.ai_tailor_with_fallback(...)`, `CV.deterministic_filter(...)`, `CV.agentic_tailor(...)` — all
  removed. The pipeline is now `CV.filter_cv(job_post_text, grade)` → `CVFilter.output()`.
- The old filters **mutated `cv.entries` in place** (pruned the model list), so `CvRender(cv)` and the
  print helpers could read the survivors. `filter_cv` instead **returns** `{type: [flat dict +
  score]}` and leaves `cv.entries` full. Those flat dicts aren't model instances, so the renderer and
  `_format_entry` can't consume them directly.

This guide adds one glue method (`CV.apply_selection`) that maps a `CVFilter` selection back onto the
model instances and prunes `cv.entries` to the ranked survivors, then rewrites both commands around
the **grade** ladder (`light` / `standard` / `strong`) instead of the gone `--llm` alias + `--threshold`
knobs.

> **Depends on `[backend]-fix_cv_class.md`.** `filter_cv` only returns the `{type: […+score]}` shape
> once the `CVFilter` rewrite in that guide is typed. Implement that first, or these commands will see
> the stub's `None` and the `apply_selection` call will no-op/raise.

---

## Affected files

| path | change |
| ---- | ------ |
| `backend/jac/cv.py` | add `CV.apply_selection(selection)` — prune `self.entries` to the selected models in ranked order, annotate each with `relevance_score` |
| `backend/jac/management/commands/cv_test.py` | rewrite: loop over grades (not LLM aliases); drop `--threshold`/`--aliases`/`LLMConfig`; use `filter_cv` + `apply_selection` |
| `backend/jac/management/commands/cv_eval.py` | rewrite `_evaluate` + args around `filter_cv(grade=…)`; drop `--llm`/`--threshold`/keywords; add `--grade` |
| `backend/jac/tests.py` | add `CVApplySelectionTests` (offline) and `CVCommandSmokeTests` (patches `filter_cv`, no model) |

`backend/jac/render.py` is **unchanged** — `apply_selection` hands it the same `{plural_section:
[model instances]}` shape it already expects.

---

## The code

### 1. `cv.py` — `CV.apply_selection`

Add this method to the `CV` class, directly after `filter_cv`. It's the bridge from the filter's flat
output back to model instances so the renderer and inspectors keep working.

```python
    def apply_selection(self, selection: dict) -> None:
        """Prune self.entries to the entries chosen by CVFilter, in ranked order.

        `selection` is CVFilter.output(): {type: [{id, score, ...}, ...]} where `type` is the
        singular entry type ("job", "skill", …) and each list is already ranked descending.

        Each surviving model instance gets a `relevance_score` attribute for downstream rendering
        / inspection. Sections absent from `selection` are emptied. self.entries section keys are
        the plural form ("jobs", "skills", …); the flat ids are "<singular>:<pk>".
        """
        by_id = {
            f"{section[:-1]}:{obj.pk}": obj
            for section, items in self.entries.items()
            for obj in items
        }
        pruned = {section: [] for section in self.entries}
        for ftype, chosen in selection.items():
            section = f"{ftype}s"
            if section not in pruned:
                continue
            for item in chosen:
                obj = by_id.get(item.get("id"))
                if obj is None:
                    continue
                obj.relevance_score = item.get("score")
                pruned[section].append(obj)
        self.entries = pruned
```

> The plural↔singular mapping is purely additive `+ "s"` / `[:-1]` because every section here
> pluralises regularly (`education` → `educations`, `certification` → `certifications`). If an
> irregular section is ever added, this needs an explicit map.

### 2. `cv_test.py` — full rewrite

Compares the three grades on a single posting and exports one CV per grade. The `_entry_label` /
`_print_pass` helpers are unchanged (they read model attributes, which survive `apply_selection`).

```python
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
```

### 3. `cv_eval.py` — rewrite args + `_evaluate`

Keep the corpus loop, findings writer, and compare logic; swap the per-posting evaluation to the new
API. Below are the parts that change — the unchanged top-of-file helpers (`_safe`, `_REPO_ROOT`,
`_SECTIONS`) stay as they are.

**Module docstring** (replace the top docstring):

```python
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
```

Add next to the existing module constants:

```python
_GRADES = ["light", "standard", "strong"]
```

**`add_arguments`** — drop `--llm` and `--threshold`, add `--grade`:

```python
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
```

**`handle`** — update only the `meta`/header block and the `_evaluate` call to pass `grade` instead of
`llm`/`threshold` (everything else in `handle` is unchanged):

```python
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
```

**`_evaluate`** — replace the whole method:

```python
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
```

**`_write_findings`** — the header line referenced `llm`/`threshold`; point it at `grade`:

```python
        header = f"user={meta['user']}  grade={meta['grade']}  postings={len(rows)}"
```

And the per-row table line uses `grade` instead of `tier`:

```python
        for r in rows:
            c = r["counts"]
            lines.append(
                f"| {r['posting']} | {r['grade']} | {r['total']} | "
                f"{c['skills']} | {c['jobs']} | {c['educations']} | {c['certifications']} | "
                f"{c['projects']} | {c['languages']} | {r['elapsed_s']}s |"
            )
```

(Update the table header cell `| tier |` → `| grade |` in the `lines = [...]` list too.)

**`_compare`** — make it tolerant of both new (`grade`) and old (`tier`) findings files:

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
            prev_grade = p.get("grade", p.get("tier"))
            cur_grade = r.get("grade")
            grade = "" if prev_grade == cur_grade else f"   grade {prev_grade} → {cur_grade}"
            write(
                f"  {r['posting']:<28} total {p['total']}→{r['total']} ({d_total:+d})  "
                f"time {d_time:+.1f}s{grade}"
            )
```

---

## Tests

Add to `backend/jac/tests.py`. `CVApplySelectionTests` is offline and pure. `CVCommandSmokeTests`
patches `CV.filter_cv` to a canned "keep all" selection so the command path (apply_selection → render
→ file write) runs without an embedding model or network. Both reuse the existing imports
(`call_command`, `patch`, `tempfile`, `CV`, models) already at the top of `tests.py`; add
`from jac.cv import CV, CVFilter` if not already present (the CVFilter import is from the sibling
guide).

```python
# ---------------------------------------------------------------------------
# CV.apply_selection + eval-command smoke tests
# ---------------------------------------------------------------------------


class CVApplySelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="applyuser")
        cls.s1 = Skill.objects.create(user=cls.user, name="Python")
        cls.s2 = Skill.objects.create(user=cls.user, name="SQL")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )

    def test_prunes_and_orders_and_scores(self):
        cv = CV(user_pk=self.user.pk)
        selection = {
            "skill": [
                {"id": f"skill:{self.s2.pk}", "score": 0.9},
                {"id": f"skill:{self.s1.pk}", "score": 0.4},
            ],
            "job": [{"id": f"job:{self.job.pk}", "score": 0.7}],
        }
        cv.apply_selection(selection)
        # skills kept in the selection's (ranked) order, not DB order.
        self.assertEqual(
            [s.pk for s in cv.entries["skills"]], [self.s2.pk, self.s1.pk]
        )
        self.assertEqual(cv.entries["skills"][0].relevance_score, 0.9)
        self.assertEqual([j.pk for j in cv.entries["jobs"]], [self.job.pk])

    def test_section_absent_from_selection_is_emptied(self):
        cv = CV(user_pk=self.user.pk)
        cv.apply_selection({"job": [{"id": f"job:{self.job.pk}", "score": 1.0}]})
        self.assertEqual(cv.entries["skills"], [])
        self.assertEqual([j.pk for j in cv.entries["jobs"]], [self.job.pk])

    def test_unknown_ids_are_ignored(self):
        cv = CV(user_pk=self.user.pk)
        cv.apply_selection({"skill": [{"id": "skill:999999", "score": 1.0}]})
        self.assertEqual(cv.entries["skills"], [])


def _keep_all(self, job_post_text, grade=None):
    """Stand-in for CV.filter_cv: keep every flattened entry, score 1.0."""
    out: dict = {}
    for e in self._flatten_entries():
        out.setdefault(e["type"], []).append({**e, "score": 1.0})
    return out


class CVCommandSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="cmduser")
        cls.skill = Skill.objects.create(user=cls.user, name="Python")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )
        cls.job.skills.add(cls.skill)

    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_test_writes_one_md_per_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                "cv_test",
                "--user", str(self.user.pk),
                "--job", "Senior Python engineer",
                "--grades", "light", "standard",
                "--out-dir", tmp,
                stdout=io.StringIO(),
            )
            self.assertTrue((Path(tmp) / "cv_light.md").exists())
            self.assertTrue((Path(tmp) / "cv_standard.md").exists())

    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_eval_writes_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "posting.md"
            job.write_text("Senior Python engineer")
            call_command(
                "cv_eval",
                "--user", str(self.user.pk),
                "--job-file", str(job),
                "--out-dir", tmp,
                stdout=io.StringIO(),
            )
            self.assertTrue((Path(tmp) / "findings.json").exists())
            self.assertTrue((Path(tmp) / "findings.md").exists())
```

> `Path` is needed in the test module — it isn't imported in `tests.py` today. Add
> `from pathlib import Path` to the imports.

---

## Verification

Run from `backend/` in the `jac` virtualenv. **`[backend]-fix_cv_class.md` must be implemented first.**

**1. Unit + command smoke tests (offline):**

```bash
cd /Users/lukas/Projects/jac/backend
python manage.py test jac.tests.CVApplySelectionTests jac.tests.CVCommandSmokeTests -v 2
```

Expect all green. These don't touch Ollama.

**2. Real single-posting run** (Ollama up with `qwen3-embedding:0.6b`, user pk 1 has career data):

```bash
python manage.py cv_test --user 1 --job-file data/test_job.md --grades light
```

Expect: a `light` pass prints each kept section with `[score] label` lines, ranked descending, and
writes `data/cv_light.md`. Section counts should reflect the weak-filter rules (jobs ≥3, skills ≥5,
languages all present). `--grades light standard strong` runs all three; standard/strong currently
fall back to light, so their CVs should match `cv_light.md` until those score sources are built.

**3. Real corpus run + findings:**

```bash
python manage.py cv_eval --user 1 --jobs-dir data/postings
```

Expect: one row per posting on stdout (`grade=light  <Ns>  total=<n>`), and
`data/eval/<timestamp>/findings.{json,md}` plus a `<slug>.cv.md` per posting. The findings table has a
`grade` column (not `tier`).

**4. Before/after compare** (after you change a floor or the propagation weight, re-run and diff):

```bash
python manage.py cv_eval --user 1 --jobs-dir data/postings \
    --compare data/eval/<earlier-run>/findings.json
```

Expect a `total A→B (±d)` line per posting. "Done" = both commands run end-to-end, write their
artifacts, and the counts move sensibly when you tune `CVFilter._SECTION_POLICY`.

---

## Out of scope

- The `CVFilter` selection logic itself (sibling guide `[backend]-fix_cv_class.md`).
- `Instruct` / `Conversational` score sources — grades `standard`/`strong` intentionally fall back to
  `light` here.
- Wiring an LLM alias through `filter_cv` (the old `--llm` knob). If per-alias evaluation is wanted
  later, thread an `alias` arg from `filter_cv` → `CVFilter` → the score-source `embed()`/LLM calls;
  not needed for this chunk.
