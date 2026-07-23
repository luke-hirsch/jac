# [backend] letter-eval-judge

> **QUEUED — do after `[backend]-letter-matrix-pipeline` (needs the new writer).** Guide 3 of 3 of
> the "gold-standard cover letter" rework. Branch: `backend/letter-eval-judge` off `main`. Live
> tests follow the [[test-output-hygiene]] + `LivePromptTestCase` conventions; the deterministic
> parse/command tests land red at activation (step 0 = unskip).

## Context / goal

Lukas hand-made a **gold-standard** cover letter today. That gives us a stable way to evaluate the
generator: *a good letter is one at least as good as the gold standard for the same posting.* Because
"good" isn't a scalar, we judge with an LLM and test **statistically** — regenerate a candidate from
the same ingredients, ask a judge N times whether it matches the gold standard, and pass at a rate
(the repo already does this: `test_prompts.py` `LivePromptTestCase.assertPasses`, `JAC_PROMPT_RUNS`/
`JAC_PROMPT_PASS_RATE`).

The point Lukas made: **tests should track the goal, not the prompt.** A prompt refactor that keeps
producing gold-comparable letters stays green; a degrading prompt goes red. The gold letter is the
fixed target, so the test survives refactors of the writer prompt.

This guide adds:

1. `CoverLetterJudge` — a reference-anchored, line-format LLM judge (mirrors the recovered
   `TheJudge`, retargeted from CV selection to whole-letter quality vs a gold standard).
2. A `letter_eval` management command — the iteration tool: regenerate candidates from gold
   ingredients on any executor, judge them N times on a (ideally stronger, commercial) judge
   executor, write a `findings.md` with per-case pass rates. This is how Lukas tunes prompts.
3. Live statistical acceptance tests re-anchored on the new writer (writer shape, faithfulness) +
   the gold-standard quality gate, all gated by `LivePromptTestCase`.

> **Gold fixtures are Lukas's to supply** — his real winning letter + the ingredients behind it (CV
> facts, personality/style dossiers, tone/focus, posting). The code ships with an empty fixture dir
> and skips until it's filled. See "Fixtures" below.

Roadmap: closes the "how do we know the letter is good?" gap the whole rework opened, and keeps the
[[cover-letter-grounding-metric]] honest (grounding ≠ quality — the judge is the quality axis).

## Affected files

| path | change |
| --- | --- |
| `backend/jac/llm_prompts.py` | add `CoverLetterJudge` (reference-anchored gold-standard judge) |
| `backend/jac/management/commands/letter_eval.py` | **new** — regenerate candidates from gold ingredients + judge N× + write `findings.md` |
| `backend/jac/tests/fixtures/gold/` | **new dir** — `inputs.json` + `gold.md` (+ optional `posting.txt`); Lukas fills it. A `.gitkeep` + `README.md` document the format |
| `backend/jac/tests/test_pipeline.py` | `CoverLetterJudgeTests` (mocked parse) + `LetterEvalCommandTests` (mocked writer/judge, `call_command`) |
| `backend/jac/tests/test_prompts.py` | re-anchored live smokes: `CoverLetterWriterPromptTests` (cv_facts), `FaithfulnessPromptTests` (sources), `CoverLetterQualityTests` (gold gate) |

---

## The code

### 1. `backend/jac/llm_prompts.py` — `CoverLetterJudge`

Append after `AddressExtract` (reuses the module `complete`/`logger`/`re`):

```python
class CoverLetterJudge:
    """Reference-anchored quality judge for a generated cover letter.

    A judge LLM reads the job posting, a GOLD-STANDARD letter (human-approved, same posting), and a
    CANDIDATE letter, and decides whether the candidate is at least as good as the gold: same fit for
    the posting, every claim grounded, comparable craft. Because the gold standard is the fixed
    target, this stays green across prompt refactors that keep producing gold-comparable letters, and
    fails a degrading prompt — which is the whole point (Lukas, 2026-07-22).

    'Good' is statistical, so callers run it N times and pass at a rate (see test_prompts /
    assertPasses). Line-format I/O ([[no-json-llm-io]]): 'VERDICT good|weak' then optional
    '- <reason>' lines. An unreadable reply is 'weak' — a non-verdict must never pass as good.
    """

    _INSTRUCTION = (
        "You are judging whether a CANDIDATE cover letter is at least as good as a GOLD-STANDARD "
        "letter written by hand for the SAME job posting. Judge three things:\n"
        "  - FIT: does the candidate address the posting as well as the gold letter?\n"
        "  - GROUNDING: is every factual claim supported — no fabrication the gold letter avoids?\n"
        "  - CRAFT: is the writing at least as clear, specific, and compelling?\n"
        "Wording differences are fine — judge quality and fit, not sameness.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'VERDICT good' if the candidate is at least as good as the gold standard, "
        "otherwise 'VERDICT weak';\n"
        "  - then ONE line per shortfall, '- <reason>' (<=20 words), worst first (none if good).\n"
        "No prose, no markdown, no JSON."
    )
    _MAX_CHARS = 8000

    _VERDICT_RE = re.compile(r"\bVERDICT\s+(good|weak)\b", re.IGNORECASE)
    _REASON_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, posting_text, gold_letter, candidate_letter, executor):
        self.posting_text = posting_text or ""
        self.gold_letter = gold_letter or ""
        self.candidate_letter = candidate_letter or ""
        self.executor = executor

    def verdict(self) -> dict:
        """Return {'good': bool, 'reasons': [str]}. Unreadable / LLM failure -> good=False."""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("CoverLetterJudge: LLM call failed")
            return {"good": False, "reasons": []}
        return self._parse(raw)

    def _parse(self, raw: str) -> dict:
        text = raw or ""
        m = self._VERDICT_RE.search(text)
        good = bool(m) and m.group(1).lower() == "good"
        reasons: list[str] = []
        for line in text.splitlines():
            if self._VERDICT_RE.search(line):  # don't read the verdict line as a reason
                continue
            rm = self._REASON_RE.match(line)
            if rm:
                reasons.append(rm.group(1).strip()[:200])
        return {"good": good, "reasons": reasons}

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{self.posting_text[: self._MAX_CHARS]}\n\n"
            f"GOLD-STANDARD LETTER:\n{self.gold_letter[: self._MAX_CHARS]}\n\n"
            f"CANDIDATE LETTER:\n{self.candidate_letter[: self._MAX_CHARS]}\n\n"
            f"VERDICT:"
        )
```

### 2. `backend/jac/management/commands/letter_eval.py` — new command

Drives `CoverLetterWriter` directly from a gold case's **ingredients** (so it isolates the *prompt*,
not a whole DB run), then judges. Writer + judge executors are separate picks — point a strong
commercial `--judge-provider` at a HirschAI writer to grade a small model with a big one.

```python
"""Evaluate the cover-letter writer against hand-made GOLD-STANDARD letters.

For each gold case (a posting + the winning hand-written letter + the ingredients that produced it),
regenerate a CANDIDATE letter from the SAME ingredients on the writer executor, then have the JUDGE
executor decide — RUNS times — whether the candidate is at least as good as the gold standard. A
case passes at PASS_RATE. The gold letter is the fixed target, so a prompt refactor that keeps
producing gold-comparable letters stays green; a degrading prompt fails.

Usage:
  python manage.py letter_eval --user 1
  python manage.py letter_eval --user 1 --provider ollama --judge-provider anthropic --runs 5
"""

import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from jac.llm_prompts import CoverLetterJudge, CoverLetterWriter
from llm_connector.conf import ExecutorError, resolve_executor


class Command(BaseCommand):
    help = "Evaluate the cover-letter writer against hand-made gold-standard letters."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True)
        parser.add_argument("--gold-dir", default="data/gold_letters")
        parser.add_argument("--out-dir", default="data/letter_eval")
        parser.add_argument("--provider", default="ollama", help="writer executor provider")
        parser.add_argument("--model", default="")
        parser.add_argument(
            "--judge-provider", default="", help="judge provider; blank = same as writer"
        )
        parser.add_argument("--judge-model", default="")
        parser.add_argument("--runs", type=int, default=5)
        parser.add_argument("--pass-rate", type=float, default=0.6)

    def handle(self, *args, **opts):
        user = User.objects.get(pk=opts["user"])
        cases = self._load_cases(Path(opts["gold_dir"]))
        if not cases:
            raise CommandError(f"no gold cases under {opts['gold_dir']}")
        try:
            writer_x = resolve_executor(user, opts["provider"], opts["model"])
            judge_x = resolve_executor(
                user,
                opts["judge_provider"] or opts["provider"],
                opts["judge_model"],
            )
        except ExecutorError as exc:
            raise CommandError(str(exc))

        stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path(opts["out_dir"]) / stamp
        out.mkdir(parents=True, exist_ok=True)
        runs, pass_rate = opts["runs"], opts["pass_rate"]

        rows = []
        for slug, case in cases:
            self.stdout.write(f"\n{slug}:")
            good, reasons, last = 0, [], ""
            for i in range(runs):
                last = self._write(writer_x, case)
                v = CoverLetterJudge(
                    case.get("posting", ""), case.get("gold", ""), last, executor=judge_x
                ).verdict()
                good += int(v["good"])
                reasons += v["reasons"]
                self.stdout.write(f"  run {i + 1}: {'good' if v['good'] else 'weak'}")
            rate = good / runs if runs else 0.0
            verdict = "PASS" if rate >= pass_rate else "FAIL"
            rows.append((slug, good, runs, rate, verdict, reasons))
            (out / f"{slug}.candidate.md").write_text(last, encoding="utf-8")
            self.stdout.write(f"  {verdict}  {good}/{runs}")

        self._write_findings(out, rows, writer_x, judge_x, runs, pass_rate)
        self.stdout.write(f"\nfindings → {out / 'findings.md'}")

    @staticmethod
    def _write(executor, case) -> str:
        return CoverLetterWriter(
            executor=executor,
            candidate_name=case.get("candidate_name", ""),
            title=case.get("title", ""),
            language=case.get("language", "en"),
            tone=case.get("tone", "neutral"),
            focus=case.get("focus", "balanced"),
            cv_facts=case.get("cv_facts", ""),
            personality_dossier=case.get("personality", ""),
            style_dossier=case.get("style", ""),
            company_dossier=case.get("research", ""),
            mode=case.get("mode", "standard"),
            posting_text=case.get("posting", ""),
        ).write()

    @staticmethod
    def _load_cases(gold_dir: Path):
        cases = []
        if not gold_dir.exists():
            return cases
        for d in sorted(p for p in gold_dir.iterdir() if p.is_dir()):
            inputs, gold, posting = d / "inputs.json", d / "gold.md", d / "posting.txt"
            if not (inputs.exists() and gold.exists()):
                continue
            case = json.loads(inputs.read_text(encoding="utf-8"))
            case["gold"] = gold.read_text(encoding="utf-8")
            if posting.exists() and not case.get("posting"):
                case["posting"] = posting.read_text(encoding="utf-8")
            cases.append((d.name, case))
        return cases

    @staticmethod
    def _write_findings(out, rows, writer_x, judge_x, runs, pass_rate):
        lines = [
            "# Letter eval — gold-standard judgement",
            "",
            f"writer: {writer_x.provider}/{writer_x.model or 'default'}  ·  "
            f"judge: {judge_x.provider}/{judge_x.model or 'default'}  ·  "
            f"runs: {runs}  ·  pass_rate: {pass_rate}",
            "",
            "| case | good/runs | verdict |",
            "| --- | --- | --- |",
        ]
        for slug, good, n, rate, verdict, _ in rows:
            lines.append(f"| {slug} | {good}/{n} ({rate:.0%}) | {verdict} |")
        lines.append("")
        for slug, _good, _n, _rate, _verdict, reasons in rows:
            if reasons:
                lines.append(f"## {slug} — flagged shortfalls")
                lines += [f"- {r}" for r in dict.fromkeys(reasons)]  # de-dupe, keep order
                lines.append("")
        (out / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

### 3. Fixtures — `backend/jac/tests/fixtures/gold/`

Create the dir with a `README.md` documenting the format and a `.gitkeep`. **Lukas fills the real
case** from today's application. One case = one sub-directory (for the live smoke, put it directly in
`gold/`; for the command's `--gold-dir`, use `data/gold_letters/<slug>/`). Files per case:

- `posting.txt` — the job posting text.
- `gold.md` — the winning hand-written letter **body** (what `CoverLetterWriter` emits — no address/
  salutation furniture).
- `inputs.json` — the ingredients that produced it, so a candidate is regenerated from the same
  material:

```json
{
  "candidate_name": "…",
  "title": "…",
  "language": "de",
  "tone": "personal",
  "focus": "balanced",
  "mode": "standard",
  "cv_facts": "- <one CV fact line per entry, as cv._flatten_entries() emits>\n- …",
  "personality": "<the distilled personality dossier>",
  "style": "<the distilled writing-style dossier>",
  "research": "<company-research dossier, or empty>"
}
```

> `cv_facts` mirrors `CoverLetter._cv_facts()` (guide 1): `- <entry text>` per line. Grab a real one
> from a `manage.py shell` run (`print(CoverLetter(...)._cv_facts())`) so the fixture matches what the
> pipeline actually feeds the writer.

---

## Tests

Land at activation (step 0 = unskip). Two deterministic (mocked, red immediately) + three live
(gated by `LivePromptTestCase` / the gold fixture).

**`backend/jac/tests/test_pipeline.py`** — deterministic, no network:

- `CoverLetterJudgeTests` — `_parse`: `VERDICT good` → `{good:True, reasons:[]}`; `VERDICT weak` +
  bullet reasons parse; an unreadable reply → `{good:False, reasons:[]}` (a non-verdict never passes);
  `verdict()` safe on LLM error (patch `jac.llm_prompts.complete`); `_prompt()` carries the posting,
  gold, and candidate text.
- `LetterEvalCommandTests` — `call_command("letter_eval", user=…, gold_dir=<tmp>, out_dir=<tmp>,
  runs=2)` with `CoverLetterWriter.write` patched to a fixed body and `CoverLetterJudge.verdict`
  patched (good×2 → PASS row; weak×2 → FAIL row). Assert `findings.md` is written and contains the
  verdict. Use a tmp gold dir with one minimal case; capture stdout via `io.StringIO`
  ([[test-output-hygiene]]). Patch `resolve_executor` to a fake so no key is needed.

**`backend/jac/tests/test_prompts.py`** — live, re-anchored on the new writer (replaces the
snippet-era smokes guide 1 removed). Import `CoverLetterJudge`; drop the `SNIPPETS` fixture; add a
small `CV_FACTS` string constant:

- `CoverLetterWriterPromptTests` — `CoverLetterWriter(executor=…, cv_facts=CV_FACTS, tone="personal",
  focus="balanced", …).write()` returns a real body: 60–400 words, no `dear` in the first 80 chars
  (furniture is ours), and **no `REFUSAL_MARKERS`** (the roadmap refusal guard).
- `FaithfulnessPromptTests` — clean body (the CV facts stitched) audits `count == 0` via
  `FaithfulnessCheck(body, sources=CV_FACTS, executor=…)`; a fabricated "Nobel Prize" line flags
  `count >= 1`.
- `CoverLetterQualityTests` — the gold gate. `setUpClass` loads `jac/tests/fixtures/gold/`
  (`inputs.json` + `gold.md`); **skips** if absent. `assertPasses`: each run writes a candidate from
  the gold ingredients on HirschAI and `CoverLetterJudge(...).verdict()["good"]`. Note in the
  docstring that HirschAI judging itself is weak — this is a smoke; the authoritative eval is
  `letter_eval` with a strong `--judge-provider`.

Run:
```bash
cd backend
python manage.py test jac.tests.test_pipeline.CoverLetterJudgeTests \
    jac.tests.test_pipeline.LetterEvalCommandTests            # deterministic
JAC_PROMPT_RUNS=3 python manage.py test jac.tests.test_prompts  # live (tower up + gold fixture)
```

---

## Verification

1. Deterministic tests green (above) — proves the judge parses/degrades and the command writes
   findings, no LLM.
2. Drop the real gold case into `jac/tests/fixtures/gold/`, bring HirschAI up, run
   `JAC_PROMPT_RUNS=5 python manage.py test jac.tests.test_prompts.CoverLetterQualityTests`.
   Expect it to pass at ≥ `JAC_PROMPT_PASS_RATE` — if it doesn't on the small model, that's a real
   signal, not a broken test (log it in Results and tune the writer prompt / try `letter_eval` with a
   stronger judge to separate writer weakness from judge weakness).
3. **The iteration loop** (the point of the guide): with an Anthropic/OpenAI key configured,
   ```bash
   python manage.py letter_eval --user 1 --provider ollama --judge-provider anthropic --runs 5
   ```
   Open `data/letter_eval/<stamp>/findings.md`: a per-case `good/runs` + PASS/FAIL table and the
   flagged shortfalls. Change the writer prompt, re-run — a real improvement moves the rate up; a
   regression moves it down. That's the stable, goal-anchored test Lukas asked for.
4. Point `--judge-provider` at a broken/keyless provider → `CommandError` (no silent bad run); a
   dead judge mid-run → every verdict `weak` (`good=False`), FAIL, findings still written.

**Done looks like:** `CoverLetterJudge` grades a candidate against the gold standard in a parseable
line format; the live suite passes statistically when the writer is gold-comparable and goes red when
it degrades; `letter_eval` gives Lukas a strong-judge iteration loop with a `findings.md` scoreboard.

## Results

<!-- Human fills this in after testing: raw test output, observed issues, what works. -->
