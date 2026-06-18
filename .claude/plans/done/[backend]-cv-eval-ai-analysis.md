# [backend] cv_eval — AI analysis of the findings (judge + summary)

## Context / goal

`cv_eval` today produces a **deterministic, comparable** artifact: per posting × model it writes
counts-vs-target, the rank+score of every kept entry, and elapsed time (`findings.json/md`,
`*.ranks.md`, `*.cv.md`). That's all *numbers* — to know whether the pipeline picked the **right**
entries you still have to read every `ranks.md` by hand.

This guide adds an **opt-in AI analysis layer** on top, in two stacked steps (decided with Lukas):

1. **Judge** — per posting × model, a *fixed strong* LLM reads the posting + what was **kept vs
   dropped** and grades the selection quality (`GRADE A–F` + id-anchored notes on bad keeps/drops).
2. **Analyst** — one call over the whole sweep (the numbers + every run's judge grade/notes) writes
   a human-readable `analysis.md`: which model/grade selects best, where sections over/under-shoot,
   recurring mistakes, next steps.

Plus a new matrix mode, **`--all-models`** (every configured model at its *own* auto-detected grade)
— the natural input for an analysis sweep, and a gap in the current matrix (today `--grade` alone
forces *one* grade across all models).

Design guardrails baked in:

- **Determinism preserved.** `findings.json/md` are unchanged in shape and stay the before/after
  ground truth. The analysis is purely additive (`*.judge.md`, `analysis.md`) behind `--analyze`.
  The heavy `kept`/`dropped` payloads are stripped before `findings.json` is written.
- **One consistent grader.** The judge runs under a single `--analyst` alias (default: the
  strongest configured model), never each run's own model — otherwise weak models grade their own
  homework.
- **Line-format I/O for the judge** (parsed) per the `no-json-llm-io` memory. The Analyst output is
  human-read prose (not parsed), so it's free-form.
- **Cost is opt-in.** Judge is N postings × M models LLM calls; it only runs with `--analyze`.

Roadmap: supports item 1 (CV ladder) as eval tooling — it's how we'll compare the `strong` rung
against `standard`/`light` once it lands.

## Affected files

| path | change |
| --- | --- |
| `backend/jac/llm_prompts.py` | add `Judge` (per-run selection-quality grader) and `Analyst` (cross-run summariser) classes |
| `backend/jac/management/commands/cv_eval.py` | `--all-models`/`--analyze`/`--analyst` flags; capture kept/dropped in `_evaluate`; `_analyze` pass; slim `findings.json`; `_resolve_runs` gains `all_models` |
| `backend/jac/tests.py` | parse/behaviour tests for `Judge` + `Analyst`, and `--all-models` `_resolve_runs` cases |

---

## The code

### 1. `backend/jac/llm_prompts.py` — append two classes

Add at the end of the file (after `Instruct`). Both reuse the existing module-level
`complete` import and `logger`.

```python
class Judge:
    """Selection-quality grader for one eval run: a fixed strong LLM reads the job posting plus
    what the pipeline KEPT vs DROPPED and critiques the choice — a letter grade for the run, then
    id-anchored notes on questionable keeps/drops. Used by `cv_eval --analyze` to turn raw
    counts/scores into a judgment and to feed the cross-run `Analyst` summary.

    Provider-agnostic. Line-format I/O (never JSON — see the `no-json-llm-io` memory): the reply's
    `GRADE <A-F>` line is parsed for the table; `<id> — <note>` lines are collected as critique,
    ids validated against this run's entry set, unreadable lines skipped. Any failure yields a null
    grade + empty notes so the analysis degrades instead of crashing.
    """

    _INSTRUCTION = (
        "You are auditing how well an automated system tailored a ONE-PAGE CV to a job posting.\n"
        "Below are the job posting, the entries the system KEPT (best first), and the entries it "
        "DROPPED. Judge the SELECTION quality for THIS posting:\n"
        "  - did it keep what the posting actually calls for, and drop the off-topic?\n"
        "  - flag any KEPT entry that is weak or irrelevant, and any DROPPED entry that should "
        "have stayed (e.g. a required skill).\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'GRADE <A-F>' — overall selection quality (A best);\n"
        "  - then ONE line per problem, '<id> — <short note>' (<=15 words), worst first;\n"
        "  - if the selection is sound, emit no problem lines.\n"
        "Use the exact ids given below. No prose, no markdown, no JSON."
    )
    _MAX_POST_CHARS = 12000

    _GRADE_RE = re.compile(r"\bGRADE\s+([A-Fa-f])\b")
    # entry ids are  type:pk  (e.g. job:2); anchor on a leading id, the rest of the line is the note.
    _NOTE_RE = re.compile(r"([a-z]+:\d+)\s*[-—:.)\]]*\s*(.*)")

    def __init__(
        self, job_post_text: str, kept: list[dict], dropped: list[dict], user=None,
        alias: str = "default",
    ):
        self.job_post_text = job_post_text
        self.kept = kept  # [{id, text}], ranked best-first
        self.dropped = dropped  # [{id, text}]
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        """Return {'grade': 'A'..'F' | None, 'notes': [{id, note}]}. Safe defaults on any failure."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("Judge: LLM call failed")
            return {"grade": None, "notes": []}
        return self._parse(raw)

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        kept = "\n".join(f"{e['id']} — {e.get('text') or ''}" for e in self.kept) or "(none)"
        dropped = (
            "\n".join(f"{e['id']} — {e.get('text') or ''}" for e in self.dropped) or "(none)"
        )
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"KEPT (best first):\n{kept}\n\n"
            f"DROPPED:\n{dropped}\n\n"
            f"VERDICT:"
        )

    def _parse(self, raw: str) -> dict:
        text = raw or ""
        gm = self._GRADE_RE.search(text)
        grade = gm.group(1).upper() if gm else None
        valid = {e["id"] for e in (self.kept + self.dropped)}
        notes: list[dict] = []
        seen: set[str] = set()
        for line in text.splitlines():
            if self._GRADE_RE.search(line):  # don't read the grade line as a note
                continue
            m = self._NOTE_RE.search(line)
            if not m:
                continue
            eid = m.group(1)
            if eid in valid and eid not in seen:
                seen.add(eid)
                notes.append({"id": eid, "note": m.group(2).strip()[:200]})
        return {"grade": grade, "notes": notes}


class Analyst:
    """Cross-run summariser for `cv_eval --analyze`: a strong LLM reads the whole evaluation —
    every posting×model run's counts-vs-target, score range, elapsed time, and the per-run `Judge`
    grade + notes — and writes a human-readable analysis (which models/grades pick well, where they
    over/under-shoot, speed/quality trade-offs, recurring mistakes, next steps).

    Unlike the other rungs this output is read by a human (written to `analysis.md`), not parsed, so
    it returns free-form prose. Any failure returns '' so the caller can note the analysis was
    skipped.
    """

    _INSTRUCTION = (
        "You are analysing an evaluation of an automated CV-tailoring pipeline run over several job "
        "postings with several models/grades. Each run below shows how many entries it KEPT per "
        "section (vs a one-page target), the relevance-score range, elapsed time, and an auditor's "
        "letter grade plus notes on questionable keeps/drops.\n"
        "Write a concise analysis for the engineer tuning the pipeline:\n"
        "  - which model/grade selects best, and the speed/quality trade-off;\n"
        "  - sections that consistently over- or under-shoot the one-page target;\n"
        "  - recurring selection mistakes across postings (cite ids);\n"
        "  - one or two concrete next steps.\n"
        "Use short paragraphs and bullet points. Be specific; cite postings, models, and ids."
    )

    def __init__(self, report: str, user=None, alias: str = "default"):
        self.report = report
        self.user = user
        self.alias = alias

    def analyse(self) -> str:
        try:
            return complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("Analyst: summary call failed")
            return ""

    def _prompt(self) -> str:
        return f"{self._INSTRUCTION}\n\nEVALUATION DATA:\n{self.report}\n\nANALYSIS:"
```

### 2. `backend/jac/management/commands/cv_eval.py`

#### 2a. Imports — add `Judge`, `Analyst`

```python
from jac.cv import CV
from jac.llm_prompts import Analyst, Judge
from jac.render import CvRender
```

#### 2b. `_resolve_runs` — add the `all_models` mode

Replace the function body's leading logic so `all_models` wins first (it ignores `--grade`/`--llm`):

```python
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
```

#### 2c. `add_arguments` — three new flags

Add alongside the existing ones (e.g. after `--llm`):

```python
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
```

#### 2d. `handle` — pass `all_models`, pick the analyst, run the analysis pass

In `handle`, update the `_resolve_runs` call to forward the new mode:

```python
        runs = _resolve_runs(
            opts["grade"],
            opts["llm"],
            aliases,
            lambda a: get_alias_strength(a, user=opts["user"]),
            all_models=opts["all_models"],
        )
```

Then, after the existing findings block:

```python
        self._write_findings(rows, out_dir, meta)
        write(f"\n  findings → {out_dir / 'findings.md'}")
```

append the analysis trigger:

```python
        if opts["analyze"]:
            analyst = opts["analyst"] or next(
                (
                    a
                    for a in aliases
                    if get_alias_strength(a, user=opts["user"]) == "strong"
                ),
                "default",
            )
            slug_text = {slug: text for slug, text in postings}
            self._analyze(rows, slug_text, analyst, opts["user"], out_dir, write)
```

> Note: keep the existing `--compare` block after this — the order doesn't matter, but `_analyze`
> reads the in-memory `kept`/`dropped` on each row, so it must run before nothing strips them
> (we strip only inside `_write_findings`, which writes a copy — see 2f).

#### 2e. `_evaluate` — capture kept/dropped

Inside `_evaluate`, the current success path does:

```python
        try:
            selection = cv.filter_cv(job_text, grade=grade, alias=alias)
            cv.apply_selection(selection)
        except Exception as exc:
            ...
```

Capture the full candidate set **before** `apply_selection` prunes `cv.entries`, and derive
kept/dropped:

```python
        try:
            selection = cv.filter_cv(job_text, grade=grade, alias=alias)
            candidates = cv._flatten_entries()  # full set + text, before pruning
            cv.apply_selection(selection)
        except Exception as exc:
            ...
```

Then, just before building `row` (after `counts = {...}`), add:

```python
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
```

and include them in `row`:

```python
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
```

#### 2f. `_write_findings` — strip the heavy payloads from `findings.json`

`findings.json` stays lean and shape-compatible (so `--compare` is unaffected). Replace the JSON
write at the top of `_write_findings`:

```python
    def _write_findings(self, rows, out_dir, meta):
        # Drop the per-run kept/dropped payloads from the comparable artifact (they're heavy and
        # already captured in *.ranks.md / *.cv.md); they live only in-memory for --analyze.
        slim = [
            {k: v for k, v in r.items() if k not in ("kept", "dropped")} for r in rows
        ]
        (out_dir / "findings.json").write_text(
            json.dumps(slim, indent=2), encoding="utf-8"
        )
        ...  # rest of the method unchanged
```

#### 2g. New methods — `_analyze`, `_run_block`, `_write_judge`

Add these to `Command` (e.g. after `_compare`):

```python
    def _analyze(self, rows, slug_text, analyst_alias, user, out_dir, write):
        """Judge each run's selection (kept vs dropped vs posting), then summarise the whole sweep.

        Writes one `*.judge.md` per run and a single `analysis.md`. The judge + analyst both run
        under `analyst_alias` (a fixed strong grader). Error rows are skipped.
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
            verdict = Judge(
                slug_text.get(r["posting"], ""),
                r.get("kept", []),
                r.get("dropped", []),
                user=user,
                alias=analyst_alias,
            ).critique()
            self._write_judge(r, verdict, out_dir)
            write(
                f"  {label:<28} grade {verdict['grade'] or '?'}  "
                f"({len(verdict['notes'])} note(s))"
            )
            blocks.append(self._run_block(r, verdict))

        if not blocks:
            write("\n  analysis skipped (no successful runs to judge)")
            return

        summary = Analyst(
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
```

#### 2h. Module docstring — extend the usage block (optional but nice)

Add to the `Usage:` examples:

```python
    python manage.py cv_eval --user 1 --jobs-dir data/postings --all-models --analyze
    python manage.py cv_eval --user 1 --jobs-dir data/postings --all-models --analyze \
        --analyst reasoning
```

---

## Tests

`backend/jac/tests.py` — extend the import and add three test classes (place near the existing
`ConversationalSelectorTests` / `ResolveRunsTests`). No network: `Judge`/`Analyst` parse logic is
pure; `complete` is patched for the call-path tests, matching the existing
`patch("jac.llm_prompts.complete", ...)` style.

Update the import:

```python
from jac.llm_prompts import Analyst, Conversational, Embed, Instruct, Judge
```

Add the test classes:

```python
class JudgeCritiqueTests(TestCase):
    """Judge._parse / critique(): grade + id-anchored notes, tolerant, validating — no network."""

    def _judge(self):
        return Judge(
            "posting",
            kept=[{"id": "skill:1", "text": "Python"}],
            dropped=[{"id": "job:9", "text": "old job"}],
        )

    def test_parses_grade_and_notes(self):
        out = self._judge()._parse("GRADE B\njob:9 — required, should have stayed")
        self.assertEqual(out["grade"], "B")
        self.assertEqual(
            out["notes"], [{"id": "job:9", "note": "required, should have stayed"}]
        )

    def test_grade_only_yields_no_notes(self):
        out = self._judge()._parse("GRADE A")
        self.assertEqual(out["grade"], "A")
        self.assertEqual(out["notes"], [])

    def test_missing_grade_is_none(self):
        out = self._judge()._parse("skill:1 — weak match")
        self.assertIsNone(out["grade"])
        self.assertEqual(out["notes"], [{"id": "skill:1", "note": "weak match"}])

    def test_unknown_ids_dropped_and_deduped(self):
        out = self._judge()._parse(
            "GRADE C\nskill:99 — not in set\nskill:1 — weak\nskill:1 — dupe"
        )
        self.assertEqual(out["notes"], [{"id": "skill:1", "note": "weak"}])

    def test_tolerates_separator_drift(self):
        out = self._judge()._parse("GRADE D\njob:9: missing required stack")
        self.assertEqual(out["notes"], [{"id": "job:9", "note": "missing required stack"}])

    def test_critique_safe_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._judge().critique(), {"grade": None, "notes": []})

    def test_critique_parses_reply(self):
        with patch(
            "jac.llm_prompts.complete", return_value="GRADE B\nskill:1 — weak match"
        ):
            out = self._judge().critique()
        self.assertEqual(out["grade"], "B")
        self.assertEqual(out["notes"], [{"id": "skill:1", "note": "weak match"}])


class AnalystSummaryTests(TestCase):
    """Analyst.analyse(): free-form prose passthrough, safe on failure — no network."""

    def test_prompt_includes_report(self):
        self.assertIn("REPORT-DATA", Analyst("REPORT-DATA")._prompt())

    def test_analyse_returns_text(self):
        with patch("jac.llm_prompts.complete", return_value="the analysis"):
            self.assertEqual(Analyst("r").analyse(), "the analysis")

    def test_analyse_empty_on_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("x")):
            self.assertEqual(Analyst("r").analyse(), "")
```

And add to the existing `ResolveRunsTests` class:

```python
    def test_all_models_runs_each_at_its_own_grade(self):
        self.assertEqual(
            _resolve_runs(
                None, None, ["default", "reasoning"], self._strength, all_models=True
            ),
            [("default", "light"), ("reasoning", "standard")],
        )

    def test_all_models_overrides_grade_and_llm(self):
        self.assertEqual(
            _resolve_runs(
                "strong", "reasoning", ["a", "b"], self._strength, all_models=True
            ),
            [("a", "strong"), ("b", "strong")],
        )
```

---

## Verification

1. **Unit tests** (pure parse + matrix logic, no network):

   ```bash
   cd backend
   python manage.py test jac.tests.JudgeCritiqueTests jac.tests.AnalystSummaryTests \
       jac.tests.ResolveRunsTests -v 2
   ```

   Expect all green. These prove the grade/notes parsing, id validation, error-safety, and the
   `--all-models` matrix without hitting an LLM.

2. **Smoke the matrix mode without analysis** (no extra LLM cost — confirms nothing regressed and
   `findings.json` is still slim):

   ```bash
   python manage.py cv_eval --user 1 --job-file data/test_job.md --all-models
   ```

   Expect: one row per configured model, each at its own grade (the header `runs:` line lists
   `alias:grade` pairs). Open the new `data/eval/<stamp>/findings.json` and confirm there are **no**
   `kept`/`dropped` keys on the rows (only `posting/model/grade/total/counts/ranks/elapsed_s`).

3. **Full judge + summary** (this spends LLM calls — make sure Ollama or your configured analyst is
   up):

   ```bash
   python manage.py cv_eval --user 1 --jobs-dir data/postings --all-models --analyze
   ```

   Expect on stdout, after the findings line:
   ```
   ========================================================================
   AI ANALYSIS — judge + summary via '<analyst-alias>'
   ========================================================================
     <model>/<posting>            grade B  (2 note(s))
     ...
     analysis → .../analysis.md
   ```
   In `data/eval/<stamp>/`: one `*.judge.md` per run (kept / dropped / flagged sections with the
   letter grade) and one `analysis.md` with the cross-run prose. The grader alias is whatever
   `--analyst` resolved to (strongest configured model by default).

4. **Pin the analyst explicitly** and confirm it's used (check the header line + `analysis.md`
   title):

   ```bash
   python manage.py cv_eval --user 1 --job-file data/test_job.md --all-models --analyze \
       --analyst reasoning
   ```

5. **Graceful degradation** — if the analyst model is unreachable, the run must still finish: the
   judge lines show `grade ?`, `analysis.md` is skipped with `analysis skipped (...)`, and
   `findings.*` are written as normal. (Stop Ollama, or point `--analyst` at a broken alias, to
   confirm.)

**Done looks like:** `--all-models --analyze` produces per-run `*.judge.md` grades + a cross-run
`analysis.md`, `findings.json` is unchanged in shape (still diffs cleanly with `--compare`), and a
dead analyst degrades to `grade ?` / skipped summary without breaking the eval.
