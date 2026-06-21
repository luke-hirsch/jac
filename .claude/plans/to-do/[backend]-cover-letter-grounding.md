# Cover-letter: drop the posting from the writer + add a faithfulness (grounding) check

## Context / goal

Roadmap item **1** (cover-letter generation), follow-up sub-step. A real letter came back with
fabricated claims even though `ai_share` read **5%**. That exposed two things:

1. **`ai_share` measures the wrong axis for truth.** It is a *provenance* metric — "how much prose
   did the machine produce / translate" — computed purely from snippet languages + a per-grade
   rewrite tax (`CoverLetter._ai_share`). It never inspects the output, so a low value says nothing
   about whether the writer *invented facts*. Provenance and faithfulness are orthogonal.
2. **The writer is handed the job posting**, which is the fabrication vector. `CoverLetterWriter`
   feeds the full posting into its prompt (`JOB POSTING:\n{post}`), then asks a small model
   (`llama3.2:1b`) to write a persuasive letter *for that posting*. A weak model mirrors the
   posting's wish-list ("led a team of 10", "5 yrs Kubernetes") back as if they were the
   candidate's facts. The posting already did its job at the **selection** stage; the writer only
   needs the authored snippets.

This guide makes two changes:

- **(1) Strip the posting out of `CoverLetterWriter`.** The writer weaves authored snippets and
  nothing else (it keeps the role *title* for tone/addressing). Removes the main hallucination
  source and tightens the anti-invention instruction now that the tempting source is gone.
- **(3) Add a faithfulness/grounding check.** A new `FaithfulnessCheck` (mirrors `TheJudge` in
  `llm_prompts.py`) reads the generated body + the snippets — **not** the posting — and lists every
  claim the snippets don't support. It is surfaced like `ai_share`: not "5% AI" but
  "⚠ 2 unsupported claims". Opt-in (one extra LLM call), run under a strong verifier alias.

**Key honesty rule baked into the design:** on any audit failure the check returns `count=None`
("not checked"), **never `0`**. A failed audit must never read as a clean letter — that is exactly
the false-assurance trap that motivated this work.

### Why a separate verifier alias

The writer can be a 1B model; a 1B model cannot reliably fact-check. Like `TheJudge`/`TheAnalyst`,
the verifier runs under its own alias so you can point it at a strong model while the writer stays
cheap. It defaults to the build alias but the management command exposes `--verifier-llm`.

## Affected files

| path | change |
| --- | --- |
| `backend/jac/llm_prompts.py` | **(1)** drop `job_post_text` from `CoverLetterWriter` (ctor, `_prompt`, grade clauses, `_COMMON`); **(3)** add new `FaithfulnessCheck` class |
| `backend/jac/cover_letter.py` | import `FaithfulnessCheck`; update the `CoverLetterWriter(...)` call (no posting); add `verify_grounding` / `verifier_alias` ctor params; add `_grounding()`; add `"grounding"` to the `build()` result |
| `backend/jac/management/commands/cover_letter.py` | add `--verify` / `--verifier-llm`; thread them into `CoverLetter(...)`; print grounding + add it to the artifact header |
| `backend/jac/tests.py` | new `CoverLetterWriterPromptTests`, `FaithfulnessCheckParseTests`, `CoverLetterGroundingTests` |

No migration, no API, no frontend. Surfacing `grounding` in the UI is roadmap item #2.

## Branch

Prerequisite: this stacks on the `ai_share`/`build()` structure currently **uncommitted on
`backend/snippet-language-ai-share`**, not on `main`. So land that first, then:

```bash
git checkout main && git pull --ff-only       # once snippet-language is merged
git checkout -b backend/cover-letter-grounding
```

(If you'd rather keep going before merging, branch off the current branch instead — just know the
two sub-steps will share a branch history.)

---

## The code

### 1. `backend/jac/llm_prompts.py` — drop the posting from `CoverLetterWriter`

Replace the **entire** `CoverLetterWriter` class (currently lines ~444–522) with this. Changes:
`job_post_text` and `_MAX_POST_CHARS` are gone; `_COMMON` gains an explicit "snippets are the only
source" line; the `strong` clause says "for THIS role" (no posting to order against); `_prompt`
drops the `JOB POSTING` block.

```python
class CoverLetterWriter:
    """Weave selected `ResumeSnippet`s into cover-letter body prose with the chat model.

    Snippet content is authoritative: the model stitches and smooths, it does not invent facts.
    The job posting is deliberately NOT given to the writer — it tailored the *selection* upstream,
    and feeding its wish-list here is the main way a weak model fabricates claims about the
    candidate. The writer sees only the chosen snippets plus the role title (for tone/addressing).

    `grade` tunes only how much rewriting is allowed (light = glue; standard = smooth transitions;
    strong = polished, reordered for impact) — never the content. Output is free prose (the body),
    the one place structured line-format I/O does not apply. Any failure -> '' so the caller falls
    back to the raw stitched snippets.
    """

    _GRADE_CLAUSE = {
        "light": (
            "Join the snippets into one letter body. Keep their wording where you can; add only "
            "minimal connective phrases so it reads as one piece. Do not rewrite or embellish."
        ),
        "standard": (
            "Weave the snippets into a smooth, cohesive letter body. You may lightly rephrase "
            "for flow and transitions, but preserve every concrete claim and the candidate's "
            "voice. Do not invent facts the snippets do not state."
        ),
        "strong": (
            "Compose a polished, persuasive letter body from the snippets, ordered for impact "
            "for THIS role. Improve prose and transitions freely, but every factual claim must "
            "come from the snippets — invent nothing."
        ),
    }
    _COMMON = (
        "Write ONLY the body paragraphs of a cover letter — no date, no addresses, no subject "
        "line, no salutation, no sign-off, no markdown, no placeholders. Write in {language}. "
        "Use ONLY facts stated in the snippets below; do not add skills, employers, job titles, "
        "numbers, dates, or achievements that the snippets do not state."
    )

    def __init__(
        self,
        snippets: list,
        *,
        candidate_name: str = "",
        title: str = "",
        language: str = "en",
        grade: str = "standard",
        alias: str = "default",
        user=None,
    ):
        self.snippets = snippets
        self.candidate_name = candidate_name
        self.title = title
        self.language = language
        self.grade = grade
        self.alias = alias
        self.user = user

    def write(self) -> str:
        """Return the woven body prose. '' when there are no snippets or the LLM fails."""
        if not self.snippets:
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("CoverLetterWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        clause = self._GRADE_CLAUSE.get(self.grade, self._GRADE_CLAUSE["standard"])
        common = self._COMMON.format(language=self.language)
        blocks = "\n\n".join(
            f"[{s.get_kind_display()}] {s.title}\n{s.content}" for s in self.snippets
        )
        return (
            f"{clause}\n{common}\n\n"
            f"CANDIDATE: {self.candidate_name}\n"
            f"ROLE: {self.title}\n\n"
            f"SNIPPETS (in order, use them all):\n{blocks}\n\nLETTER BODY:"
        )
```

### 2. `backend/jac/llm_prompts.py` — add `FaithfulnessCheck`

Add this class right **after** `TheJudge` (it mirrors its shape: line-format I/O, provider-agnostic,
safe defaults). `re`, `logger`, and `complete` are already imported at the top of the file.

```python
class FaithfulnessCheck:
    """Grounding auditor for a generated cover-letter body: a fixed strong LLM reads the body plus
    the candidate's authored snippets (the ONLY permitted source of fact) and lists every claim in
    the body the snippets do not support.

    `ai_share` measures PROVENANCE (how much prose the machine produced); this measures
    FAITHFULNESS (did the machine assert something untrue) — an orthogonal axis, which is why a 5%
    `ai_share` letter can still hallucinate. The job posting is deliberately NOT given: a
    requirement appearing in a posting must never be treated as a fact about the candidate.

    Provider-agnostic. Line-format I/O (never JSON — see the `no-json-llm-io` memory): the reply's
    'UNSUPPORTED <n>' line anchors the count; each following bullet line is one claim. On ANY
    failure it returns count=None ('not checked'), NEVER 0 — a failed audit must not be mistaken for
    a clean letter (the false-assurance trap this check exists to close).
    """

    _INSTRUCTION = (
        "You are fact-checking a COVER LETTER BODY against the candidate's authored SNIPPETS.\n"
        "The snippets are the ONLY permitted source of factual claims — skills, employers, job "
        "titles, numbers, dates, achievements. A claim is UNSUPPORTED if the snippets do not state "
        "or clearly imply it.\n"
        "List every unsupported factual claim in the letter body.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'UNSUPPORTED <n>' — the number of unsupported claims (0 if none);\n"
        "  - then ONE line per claim, '- <claim, quoted or paraphrased>' (<=20 words), worst "
        "first;\n"
        "  - if every claim is grounded, write 'UNSUPPORTED 0' and nothing else.\n"
        "Do not flag style, tone, opinion, or first-person framing — only checkable facts. "
        "No prose, no markdown headers, no JSON."
    )

    _COUNT_RE = re.compile(r"\bUNSUPPORTED\s+(\d+)\b", re.IGNORECASE)
    # a claim line: an optional bullet / number marker, then the claim text.
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, body: str, snippets: list, user=None, alias: str = "default"):
        self.body = body
        self.snippets = snippets
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        """Return {'count': int | None, 'claims': [str]}.

        count=None  -> audit failed / unreadable (surface as 'not checked', NOT clean).
        count=0     -> audited and fully grounded.
        count>0     -> that many claims the snippets do not support, worst first.
        """
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("FaithfulnessCheck: LLM call failed")
            return {"count": None, "claims": []}
        return self._parse(raw)

    def _prompt(self) -> str:
        blocks = (
            "\n\n".join(
                f"[{s.get_kind_display()}] {s.title}\n{s.content}" for s in self.snippets
            )
            or "(no snippets)"
        )
        return (
            f"{self._INSTRUCTION}\n\n"
            f"SNIPPETS (the only source of truth):\n{blocks}\n\n"
            f"LETTER BODY:\n{self.body}\n\n"
            f"AUDIT:"
        )

    def _parse(self, raw: str) -> dict:
        text = raw or ""
        cm = self._COUNT_RE.search(text)
        claims: list[str] = []
        for line in text.splitlines():
            if self._COUNT_RE.search(line):  # don't read the count line as a claim
                continue
            m = self._CLAIM_RE.match(line)
            if m:
                claims.append(m.group(1).strip()[:200])
        if claims:  # the listed claims are the truth; trust their length over the declared n
            return {"count": len(claims), "claims": claims}
        # No readable claim lines. Only an explicit 'UNSUPPORTED 0' counts as a clean verdict;
        # a missing count line, or a positive count with no parseable claims (truncated reply),
        # is an unreadable audit -> not checked.
        if cm and cm.group(1) == "0":
            return {"count": 0, "claims": []}
        return {"count": None, "claims": []}
```

### 3. `backend/jac/cover_letter.py` — wire it into `build()`

**3a. Extend the import** (currently `from jac.llm_prompts import CoverLetterWriter`):

```python
from jac.llm_prompts import CoverLetterWriter, FaithfulnessCheck
```

**3b. Add two ctor params** to `CoverLetter.__init__`. Replace the signature + the tail of the
body. The current signature ends with `max_body_snippets: int = 4,` and the body ends with
`self.max_body_snippets = max_body_snippets`. Make it:

```python
    def __init__(
        self,
        user,
        job_posting,
        cv,
        *,
        address=None,
        grade: str = "standard",
        alias: str = "default",
        max_body_snippets: int = 4,
        verify_grounding: bool = False,
        verifier_alias: str | None = None,
    ):
        self.user = user if isinstance(user, User) else User.objects.get(pk=user)
        self.job_posting = job_posting
        self.cv = cv
        if address is not None:
            self.address = address
        else:
            try:
                self.address = job_posting.address
            except Exception:  # noqa: BLE001 — unsaved/absent reverse 1:1
                self.address = None
        self.grade = grade
        self.alias = alias
        self.max_body_snippets = max_body_snippets
        self.verify_grounding = verify_grounding
        self.verifier_alias = verifier_alias
```

**3c. Update the writer call + result in `build()`.** Replace the block from the
`body = CoverLetterWriter(...)` call down through the `result = {...}` dict with the following.
Note: the `CoverLetterWriter(...)` call no longer passes the posting; `weave_failed` is tracked so
`_grounding` can short-circuit the raw-fallback path; a `"grounding"` key is added.

```python
        woven = CoverLetterWriter(
            sel["ordered"],
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            grade=self.grade,
            alias=self.alias,
            user=self.user,
        ).write()
        # The writer returns '' when the LLM failed OR there were no snippets to weave. Either
        # way fall back to the raw stitched snippets (no slop), and remember it for _grounding.
        weave_failed = not woven
        body = woven or "\n\n".join(s.content for s in sel["ordered"])
        body_is_ai_fallback = not sel["ordered"]

        result = {
            "language": language,
            "subject": self._subject(language, title),
            "salutation": self._salutation(language),
            "body": body,
            "sender": self._sender(),
            "recipient": self._recipient(),
            "date": timezone.localdate().isoformat(),
            "snippets_used": [f"{s.kind}:{s.pk}" for s in sel["ordered"]],
            "ai_share": self._ai_share(sel["ordered"], language, body_is_ai_fallback),
            "snippet_provenance": {
                "native": [
                    f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language == language
                ],
                "translated": [
                    f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language != language
                ],
            },
            "grounding": self._grounding(body, sel["ordered"], weave_failed),
        }
        result["text"] = self.render_markdown(result)
        return result
```

**3d. Add `_grounding()`** next to `_ai_share` (e.g. directly below it):

```python
    def _grounding(self, body, snippets, weave_failed) -> dict:
        """Audit the woven body against the snippets. {'count': int | None, 'claims': [str]}.

        count=None  -> not checked: audit off, no snippets to check against, or the audit LLM
                       failed (FaithfulnessCheck never returns 0 on failure — see its docstring).
        count=0     -> checked and fully grounded. Includes the raw-fallback path, where the body
                       IS the verbatim snippet text, so by construction nothing is unsupported.
        count>0     -> that many claims in the body the snippets do not support.

        Opt-in (one extra LLM call) and run under verifier_alias — a 1B writer cannot fact-check
        itself, so point verifier_alias at a strong model.
        """
        if not self.verify_grounding or not snippets:
            return {"count": None, "claims": []}
        if weave_failed:  # body is the verbatim snippets -> grounded by construction, no call
            return {"count": 0, "claims": []}
        return FaithfulnessCheck(
            body,
            snippets,
            alias=self.verifier_alias or self.alias,
            user=self.user,
        ).critique()
```

### 4. `backend/jac/management/commands/cover_letter.py` — flag + surface

**4a. Add two arguments** in `add_arguments`, after the existing `--persist` block:

```python
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Run the faithfulness/grounding check on each generated body.",
        )
        parser.add_argument(
            "--verifier-llm",
            type=str,
            default=None,
            help="LLMConfig alias for the grounding check (default: same as --llm). "
            "Point at a STRONG model — a weak writer cannot fact-check itself.",
        )
```

**4b. Thread the options** into the per-posting loop. Replace the `for slug, text in postings:`
loop at the end of `handle`:

```python
        for slug, text in postings:
            self._one(
                user,
                slug,
                text,
                alias,
                grade,
                opts["persist"],
                out_dir,
                write,
                opts["verify"],
                opts["verifier_llm"],
            )
```

**4c. Update `_one`.** Replace the whole method (signature gains `verify`, `verifier_alias`; the
`CoverLetter(...)` call passes them; the artifact header + console line gain grounding):

```python
    def _one(
        self,
        user,
        slug,
        text,
        alias,
        grade,
        persist,
        out_dir,
        write,
        verify,
        verifier_alias,
    ):
        cv = CV(user_pk=user.pk)
        cv.apply_selection(cv.filter_cv(text, grade=grade, alias=alias))

        extracted = AddressExtract(text, alias=alias, user=user).extract()
        jp = JobPosting(
            user=user,
            title=extracted.get("title", ""),
            posting_text=text,
            language=extracted.get("language", "en"),
        )
        addr = JobPostAddress(**{f: extracted.get(f, "") for f in _ADDRESS_FIELDS})
        if persist:
            jp.save()
            addr.job_posting = jp
            addr.save()

        result = CoverLetter(
            user,
            jp,
            cv,
            address=addr,
            grade=grade,
            alias=alias,
            verify_grounding=verify,
            verifier_alias=verifier_alias,
        ).build()

        header_lines = [f"> AI share: {result['ai_share']:.0%}"]
        header_lines.append(self._grounding_line(result["grounding"]))
        for claim in result["grounding"]["claims"]:
            header_lines.append(f">   - {claim}")
        header = "\n".join(header_lines) + "\n\n"

        stem = f"{_safe(alias)}__{slug}"
        (out_dir / f"{stem}.cover.md").write_text(
            header + result["text"], encoding="utf-8"
        )
        write(
            f"  {slug:<28} {len(result['snippets_used'])} snippet(s), "
            f"recipient={result['recipient']['company'] or '—'}"
            + ("  [persisted]" if persist else "")
            + f"  AI share: {result['ai_share']:.0%}"
            + f"  |  {self._grounding_line(result['grounding']).lstrip('> ')}"
        )

    @staticmethod
    def _grounding_line(grounding: dict) -> str:
        count = grounding["count"]
        if count is None:
            return "> Grounding: not checked"
        if count == 0:
            return "> Grounding: ✓ all claims supported"
        return f"> Grounding: ⚠ {count} unsupported claim(s)"
```

> `_grounding_line` is a `@staticmethod` so both the header and the console line share one
> formatter. The console line strips the leading `"> "` markdown.

---

## Tests

Add to `backend/jac/tests.py`. `patch` and the `CoverLetter`/`SnippetSelector` imports already
exist at the top; add `CoverLetterWriter` and `FaithfulnessCheck` to the existing
`from jac.llm_prompts import ...` line (or add one). The build tests reuse the
`CoverLetterBuildTests` fixture style (real snippets, patched `complete`).

```python
class _StubSnippet:
    """Minimal stand-in for a ResumeSnippet in writer/verifier prompt tests."""

    def __init__(self, title, content, kind_display="Achievement"):
        self.title = title
        self.content = content
        self._kind_display = kind_display

    def get_kind_display(self):
        return self._kind_display


class CoverLetterWriterPromptTests(TestCase):
    """(1) The writer prompt carries the snippets + role, never the job posting."""

    def _writer(self):
        return CoverLetterWriter(
            [_StubSnippet("Achv", "Shipped the billing service.")],
            candidate_name="Ada Lovelace",
            title="Backend Engineer",
            language="en",
        )

    def test_prompt_includes_snippets_and_role(self):
        p = self._writer()._prompt()
        self.assertIn("Shipped the billing service.", p)
        self.assertIn("Backend Engineer", p)
        self.assertIn("Ada Lovelace", p)

    def test_prompt_omits_job_posting(self):
        p = self._writer()._prompt()
        self.assertNotIn("JOB POSTING", p)

    def test_common_clause_forbids_invention(self):
        p = self._writer()._prompt()
        self.assertIn("Use ONLY facts stated in the snippets", p)

    def test_write_returns_empty_without_snippets(self):
        w = CoverLetterWriter([], title="X")
        self.assertEqual(w.write(), "")


class FaithfulnessCheckParseTests(TestCase):
    """FaithfulnessCheck._parse / .critique: tolerant line parsing, honest failure default."""

    def _check(self):
        return FaithfulnessCheck("some body", [_StubSnippet("A", "I ship code.")])

    def test_clean_verdict_is_zero(self):
        self.assertEqual(self._check()._parse("UNSUPPORTED 0"), {"count": 0, "claims": []})

    def test_lists_claims_and_counts_them(self):
        raw = "UNSUPPORTED 2\n- Led a team of 10\n- Increased revenue 30%"
        self.assertEqual(
            self._check()._parse(raw),
            {"count": 2, "claims": ["Led a team of 10", "Increased revenue 30%"]},
        )

    def test_trusts_listed_claims_over_declared_count(self):
        # declared 1 but two bullets present -> the bullets win.
        raw = "UNSUPPORTED 1\n- claim a\n* claim b"
        self.assertEqual(self._check()._parse(raw)["count"], 2)

    def test_tolerates_markdown_and_prose(self):
        raw = "Here is the audit:\nUNSUPPORTED 1\n1. Managed a 5M budget\nDone."
        self.assertEqual(
            self._check()._parse(raw), {"count": 1, "claims": ["Managed a 5M budget"]}
        )

    def test_positive_count_but_no_claims_is_not_checked(self):
        # truncated reply: count says 2 but no bullets parsed -> None, never a false 0.
        self.assertEqual(
            self._check()._parse("UNSUPPORTED 2"), {"count": None, "claims": []}
        )

    def test_garbage_is_not_checked(self):
        self.assertEqual(
            self._check()._parse("the letter looks fine to me"),
            {"count": None, "claims": []},
        )

    def test_critique_none_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._check().critique(), {"count": None, "claims": []})

    def test_critique_parses_live_reply(self):
        with patch("jac.llm_prompts.complete", return_value="UNSUPPORTED 1\n- Fake cert"):
            self.assertEqual(
                self._check().critique(), {"count": 1, "claims": ["Fake cert"]}
            )


class CoverLetterGroundingTests(TestCase):
    """build() surfaces grounding only when asked, and never lies on the off/fallback paths."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("cl_ground", first_name="Ada", last_name="L")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user, title="Achv", content="Shipped Y.", kind=K.achievement, job=cls.job
        )

    def _cv(self):
        cv = CV(user_pk=self.user.pk)
        cv.entries = {
            "jobs": [self.job],
            "projects": [],
            "skills": [],
            "educations": [],
            "certifications": [],
            "languages": [],
        }
        return cv

    def _jp(self):
        return JobPosting(
            user=self.user, title="Backend Engineer", posting_text="We need a dev.", language="en"
        )

    def _build(self, *, verify, complete_returns):
        with patch("jac.llm_prompts.complete", side_effect=complete_returns):
            return CoverLetter(
                self.user,
                self._jp(),
                self._cv(),
                address=JobPostAddress(company="Acme"),
                verify_grounding=verify,
            ).build()

    def test_grounding_not_checked_when_disabled(self):
        # Only the writer call happens; grounding is the 'not checked' sentinel.
        r = self._build(verify=False, complete_returns=["Woven body."])
        self.assertEqual(r["grounding"], {"count": None, "claims": []})

    def test_grounding_runs_and_surfaces_claims_when_enabled(self):
        # First complete() -> writer body; second -> the verifier reply.
        r = self._build(
            verify=True,
            complete_returns=["Woven body.", "UNSUPPORTED 1\n- Led a team of 10"],
        )
        self.assertEqual(r["grounding"]["count"], 1)
        self.assertEqual(r["grounding"]["claims"], ["Led a team of 10"])

    def test_grounding_clean_when_verifier_reports_zero(self):
        r = self._build(
            verify=True, complete_returns=["Woven body.", "UNSUPPORTED 0"]
        )
        self.assertEqual(r["grounding"], {"count": 0, "claims": []})

    def test_raw_fallback_is_grounded_without_calling_verifier(self):
        # Writer returns '' -> raw snippet fallback; body IS the snippets, so count 0 and the
        # verifier is never called (only the one writer complete() is consumed).
        r = self._build(verify=True, complete_returns=[""])
        self.assertEqual(r["grounding"], {"count": 0, "claims": []})
        self.assertIn("I build things.", r["body"])
```

> The `side_effect` list length matters: `test_raw_fallback_...` supplies **one** return (`""`).
> If `_grounding` wrongly called the verifier on the fallback path, `complete` would raise
> `StopIteration` and the test would fail — so the list length asserts "no second call".

---

## Verification

From `backend/` with the `jac` virtualenv active.

**1. Static + unit tests:**

```bash
python manage.py check
python manage.py test jac.tests.CoverLetterWriterPromptTests \
  jac.tests.FaithfulnessCheckParseTests jac.tests.CoverLetterGroundingTests -v 2
```

Re-run the existing cover-letter suite to confirm nothing regressed (the writer signature changed):

```bash
python manage.py test jac.tests.CoverLetterBuildTests jac.tests.SnippetSelectorTests \
  jac.tests.SnippetSelectorLanguageTests jac.tests.CoverLetterAiShareTests -v 2
```

**2. Live smoke test.** Point `--verifier-llm` at a strong model (a 1B can't fact-check). The writer
can stay on the default:

```bash
# no check (baseline, single call):
python manage.py cover_letter --user 1 --job-file data/test_job.md

# with the grounding audit, verifier on a strong alias:
python manage.py cover_letter --user 1 --job-file data/test_job.md --verify --verifier-llm reasoning
```

What "done" looks like:
- The generated body no longer pulls requirements straight from the posting as if they were yours
  (compare a before/after letter on a posting that bit you — the fabricated lines should be gone or
  reduced).
- With `--verify`, the console line ends with `Grounding: ✓ all claims supported` /
  `⚠ N unsupported claim(s)` / `not checked`, and the `.cover.md` header shows the same plus a
  `>   - <claim>` line per flagged claim.
- Without `--verify`, only the `> AI share: NN%` line is present and **no** second LLM call is made.
- Kill the verifier model (or use a bad `--verifier-llm`) and confirm the line reads
  **`not checked`**, never `✓` — a failed audit must not look clean.

## Out of scope / next

- **Frontend** (roadmap #2): render `grounding` next to `ai_share` — a green ✓ / amber "N claims"
  badge with the claim list on hover. The API already carries the dict.
- **Auto-revise loop**: feed flagged claims back to the writer for a second pass that drops/rewrites
  them. Deliberately omitted here — first *measure* honestly, then decide whether to auto-fix.
- **Tuning**: if a strong verifier proves noisy (flagging fair inferences), tighten `_INSTRUCTION`'s
  "clearly imply" latitude; the parse/result contract stays the same.
