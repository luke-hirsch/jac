# [backend] letter-matrix-pipeline

> **QUEUED — do this after `[fullstack]-chat-assistant-rework` lands.** Guide 1 of 3 of the
> "gold-standard cover letter" rework (`[frontend]-letter-matrix-ui` = 2, `[backend]-letter-eval-judge`
> = 3). Volatile/dev phase — one clean break, no compat bridges, no dead code left behind
> ([[no-compat-clean-breaks]]). Branch: `backend/letter-matrix-pipeline` off an up-to-date `main`.
>
> **Tests land at activation (step 0), not now.** The full test code lives in the "Tests" section
> below; drop it to disk *skip-marked* when you start the branch, unskip as step 0, watch it go red,
> then implement. The suite is reshaped by the chat-rework landing first, so writing the files before
> that would just rot.

## Context / goal

Lukas produced a real, hand-made application today and, with it, a **gold standard**: the winning
cover letter came from a **tone × focus matrix** (persönlich/neutral/förmlich × Soft-Skill/ausgewogen/
technisch — he landed on *personal + balanced*) plus his own writing voice, grounded in the job
posting, the hand-picked CV, and "what matters to me." We now rebuild the letter pipeline around that
recipe instead of stale snippets.

**The recipe becomes the prompt:** *"Write a `{tone}` / `{focus}` cover letter in the candidate's
`{writing style}`, reflecting who they are (`{personality dossier}`), grounded only in `{tailored CV
facts}` (+ `{company research}` when available), to fit this role."*

Decisions (from Lukas, 2026-07-22):

1. **Matrix + writing style live on the personal dossier** (`PersonalityProfile`) as a **user-level
   default**, with an **optional per-run override** on the generation.
2. **Writing style = a distilled probe.** The user pastes a writing sample; a distiller turns it into
   a cached `style_dossier` (voice/rhythm/register), rebuilt only when the sample changes — the exact
   `answers → dossier` cache pattern already in `PersonalityProfile.ensure_dossier`.
3. **Snippets die — full removal.** Facts now come from the **tailored CV** (`cv._flatten_entries()`),
   not `ResumeSnippet`. The model, its API, admin, vector corpus, and commands go (frontend removal is
   guide 2).
4. **Company research folds into the one writer.** No separate "personal paragraph" pass — the writer
   composes the whole letter (opening company-fit included) from CV + personality + style + matrix
   (+ research when the executor can web-search). `CompanyResearcher` stays; `PersonalParagraphWriter`
   and `ParagraphGroundingCheck` go.
5. **Everything is AI-written now, so the "AI rating" goes.** Drop `_ai_share` (a snippet-provenance
   heuristic) and `LetterCritic` (advisory prose grader). **Keep grounding** — one
   `FaithfulnessCheck` retargeted from *snippets* to *CV facts + personality + research* — plus the
   single repair pass. This is what guide 3 evaluates.

Roadmap: this supersedes the "cover-letter refusal guard" (#2) partially (the writer still needs the
refusal check — kept in the live tests) and reshapes the [[cover-letter-grounding-metric]] /
[[cover-letter-language-strategy]] / [[project-purpose-cv-showcase]] mechanics. Update
CLAUDE.md "current state" + those memories at `/wrap-up`.

## Affected files

| path | change |
| --- | --- |
| `backend/spa/models.py` | `PersonalityProfile`: `Tone`/`Focus` TextChoices + `letter_tone`/`letter_focus`/`writing_sample`/`style_dossier`/`sample_updated_at`/`style_built_at` fields + `has_sample()`/`style_stale()`/`ensure_style_dossier()` (mirror of `ensure_dossier`) |
| `backend/spa/distill.py` | add `StyleDistiller` (writing sample → style dossier, 1 LLM call) |
| `backend/spa/serializers.py` | `PersonalityProfileSerializer`: expose the six new fields; stamp `sample_updated_at` on sample change |
| `backend/spa/migrations/000X_*.py` | `makemigrations spa` — 6 `AddField`s on `PersonalityProfile` |
| `backend/jac/llm_prompts.py` | rewrite `CoverLetterWriter` (matrix + dossiers + CV facts, no snippets); retarget `FaithfulnessCheck` to text `sources`; **delete** `SnippetEmbed`, `LetterCritic`, `PersonalParagraphWriter`, `ParagraphGroundingCheck` |
| `backend/jac/cover_letter.py` | rewrite `CoverLetter.build()`/`_repair`/`render_markdown`/`editable_body`; add `_cv_facts`/`_sources`/`_profile`; drop `SnippetSelector`, `_ai_share`, `_critique`, `_shrunk`, `_overlong`, `_personal_paragraph`, `_stub`, `PERSONAL_STUB`; add `LETTER_STUB` |
| `backend/jac/models.py` | `GenerationRun`: `letter_tone`/`letter_focus` blank CharFields; **delete** `ResumeSnippet` |
| `backend/jac/serializers.py` | `GenerationRunCreateSerializer`: accept + validate `letter_tone`/`letter_focus`; `GenerationRunSerializer`: expose them; **delete** `ResumeSnippetSerializer` |
| `backend/jac/tasks.py` | pass `tone=run.letter_tone, focus=run.letter_focus` into `CoverLetter`; drop snippet reconcile in `sync_user_vectors` |
| `backend/jac/{views,urls,admin,signals,vectors}.py` | remove every `ResumeSnippet` reference (viewset, route, admin, signal, `snippet_corpus`/`DOC_SNIPPET`) |
| `backend/jac/management/commands/{load_snippets,cv_import,cv_export,vector_sync}.py` | delete `load_snippets.py`; strip snippet handling from the other three |
| `backend/jac/migrations/000X_*.py` | `makemigrations jac` — `AddField` ×2 on `GenerationRun` + `DeleteModel` `ResumeSnippet` |

---

## The code

### 1. `backend/spa/models.py` — extend `PersonalityProfile`

Add the two nested choice enums at the top of the class, the six fields beside the existing dossier
cache, and the style-dossier cache methods (a line-for-line mirror of `ensure_dossier`):

```python
class PersonalityProfile(models.Model):
    """Per-user personality questionnaire + cached LLM-distilled dossier, the letter tone×focus
    default, and a cached writing-style dossier distilled from a pasted sample. All three feed the
    JAC cover-letter writer (and, later, the portfolio)."""

    class Tone(models.TextChoices):
        personal = "personal", _("Personal")   # persönlich
        neutral = "neutral", _("Neutral")
        formal = "formal", _("Formal")          # förmlich

    class Focus(models.TextChoices):
        soft_skill = "soft_skill", _("Soft-skill focus")   # Soft-Skill-Fokus
        balanced = "balanced", _("Balanced")               # ausgewogen
        technical = "technical", _("Technical focus")      # technischer Fokus

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="personality"
    )
    answers = models.JSONField(default=dict, blank=True)  # {question_id: text}
    dossier = models.TextField(blank=True)  # distilled, cached
    answers_updated_at = models.DateTimeField(null=True, blank=True)
    dossier_built_at = models.DateTimeField(null=True, blank=True)

    # Letter matrix — the tone×focus cell used unless a run overrides it. The clause *text* for
    # each value lives in jac's CoverLetterWriter; here we only store the choice.
    letter_tone = models.CharField(max_length=16, choices=Tone.choices, default=Tone.neutral)
    letter_focus = models.CharField(
        max_length=16, choices=Focus.choices, default=Focus.balanced
    )

    # Writing-style probe: the user pastes a sample of their own writing; StyleDistiller turns it
    # into a cached style dossier (voice/rhythm/register), rebuilt only when the sample changes.
    # Same cache shape as answers→dossier above.
    writing_sample = models.TextField(blank=True)
    style_dossier = models.TextField(blank=True)
    sample_updated_at = models.DateTimeField(null=True, blank=True)
    style_built_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Personality({self.user})"

    def has_answers(self) -> bool:
        return any((self.answers or {}).values())

    def dossier_stale(self) -> bool:
        if self.dossier_built_at is None:
            return True
        return bool(
            self.answers_updated_at and self.answers_updated_at > self.dossier_built_at
        )

    def ensure_dossier(self, executor) -> str:
        """Return the dossier, distilling (1 LLM call) if missing or stale. '' if no answers."""
        if not self.has_answers():
            return ""
        if self.dossier and not self.dossier_stale():
            return self.dossier
        from spa.distill import PersonalityDistiller

        labels = {
            q.slug: q.prompt for q in PersonalityQuestion.objects.for_user(self.user)
        }
        text = PersonalityDistiller(
            self.answers, labels=labels, executor=executor
        ).distill()
        if text:
            self.dossier = text
            self.dossier_built_at = timezone.now()
            self.save(update_fields=["dossier", "dossier_built_at", "updated_at"])
        return self.dossier or ""

    # --- writing-style cache (mirror of the dossier cache above) --------------------------

    def has_sample(self) -> bool:
        return bool((self.writing_sample or "").strip())

    def style_stale(self) -> bool:
        if self.style_built_at is None:
            return True
        return bool(
            self.sample_updated_at and self.sample_updated_at > self.style_built_at
        )

    def ensure_style_dossier(self, executor) -> str:
        """Return the style dossier, distilling (1 LLM call) if missing or stale. '' if no sample."""
        if not self.has_sample():
            return ""
        if self.style_dossier and not self.style_stale():
            return self.style_dossier
        from spa.distill import StyleDistiller

        text = StyleDistiller(self.writing_sample, executor=executor).distill()
        if text:
            self.style_dossier = text
            self.style_built_at = timezone.now()
            self.save(update_fields=["style_dossier", "style_built_at", "updated_at"])
        return self.style_dossier or ""
```

### 2. `backend/spa/distill.py` — `StyleDistiller`

Append after `PersonalityDistiller` (same `complete`/`logger` imports at the top of the file):

```python
class StyleDistiller:
    """Turn a pasted writing sample into a compact, reusable WRITING-STYLE dossier (1 LLM call).

    Describes HOW the person writes — sentence rhythm, register, vocabulary, characteristic
    constructions — never WHAT the sample was about, so no facts can leak from here into a letter.
    Free prose out; any failure -> '' so the writer just falls back to no style hint.
    """

    _INSTRUCTION = (
        "Below is a sample of a person's own writing. Describe their WRITING STYLE so another writer "
        "could imitate their voice: sentence length and rhythm, register (formal↔casual), vocabulary, "
        "punctuation habits, and any characteristic turns of phrase. 3-5 sentences, factual and "
        "instructional. Describe ONLY the style — never restate the sample's topic or any fact from "
        "it. No headers, no markdown, no preamble."
    )
    _MAX_SAMPLE_CHARS = 6000

    def __init__(self, sample: str, *, executor):
        self.sample = sample or ""
        self.executor = executor

    def distill(self) -> str:
        if not self.sample.strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("StyleDistiller: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\n\n"
            f"WRITING SAMPLE:\n{self.sample[: self._MAX_SAMPLE_CHARS]}\n\nSTYLE:"
        )
```

### 3. `backend/spa/serializers.py` — expose the new fields

In `PersonalityProfileSerializer.Meta`, extend `fields` and `read_only_fields`, and stamp
`sample_updated_at` in `update()` when the sample text changes (mirroring the `answers` stamp):

```python
    class Meta:
        model = PersonalityProfile
        fields = (
            "id",
            "user",
            "answers",
            "dossier",
            "questions",
            "answers_updated_at",
            "dossier_built_at",
            "letter_tone",
            "letter_focus",
            "writing_sample",
            "style_dossier",
            "sample_updated_at",
            "style_built_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "dossier",
            "questions",
            "answers_updated_at",
            "dossier_built_at",
            "style_dossier",
            "sample_updated_at",
            "style_built_at",
            "updated_at",
        )
```

```python
    def update(self, instance, validated_data):
        from django.utils import timezone

        if "answers" in validated_data:
            instance.answers_updated_at = timezone.now()
        if (
            "writing_sample" in validated_data
            and validated_data["writing_sample"] != instance.writing_sample
        ):
            instance.sample_updated_at = timezone.now()
        return super().update(instance, validated_data)
```

`letter_tone`/`letter_focus` are model fields *with* `choices=`, so `ModelSerializer` already
rejects unknown values — no extra validation needed. (Optional: cap `writing_sample` length with a
`validate_writing_sample` if you want a friendlier error than the DB.)

### 4. `backend/jac/llm_prompts.py` — new writer, retargeted audit, deletions

**Delete** these classes entirely: `SnippetEmbed`, `LetterCritic`, `PersonalParagraphWriter`,
`ParagraphGroundingCheck`. **Keep** `Embed`, `Conversational`, `Instruct`, `AddressExtract`,
`LetterChat`, `ParagraphRewrite`, `CompanyResearcher`, and the shared `_parse_unsupported` /
`_language_name` helpers.

**Replace `CoverLetterWriter`** with the matrix-driven, snippet-free version:

```python
class CoverLetterWriter:
    """Compose a tailored cover-letter body from the candidate's tailored CV facts, their
    personality + writing-style dossiers, and (when available) a company-research dossier —
    shaped by the tone × focus matrix. There are no snippets: the CV entries are the ONLY source
    of facts about the candidate, at every mode.

    The posting is role context on `high` only (the classic fabrication vector — never a source of
    facts). STYLE guides voice, never facts. `unsupported_claims` is the repair channel: the
    grounding audit's findings feed exactly one rewrite. Free prose out; any failure -> '' so the
    caller surfaces a loud stub.
    """

    _TARGET_WORDS = (200, 320)

    # Keys MUST match spa PersonalityProfile.Tone / .Focus values.
    _TONE = {
        "personal": (
            "Write in a warm, personable, first-person voice — genuine and direct, as if speaking "
            "to the reader."
        ),
        "neutral": (
            "Write in a professional voice with measured warmth — neither stiff nor familiar."
        ),
        "formal": (
            "Write in a formal, reserved business register — traditional and restrained."
        ),
    }
    _FOCUS = {
        "soft_skill": (
            "Lead with working style, collaboration, values and motivation; use technical facts as "
            "supporting evidence."
        ),
        "balanced": (
            "Give technical achievements and working style / motivation roughly equal weight."
        ),
        "technical": (
            "Lead with concrete technical achievements, tools, and measurable outcomes; keep "
            "soft-skill framing brief."
        ),
    }

    _COMMON = (
        "Write ONLY the body paragraphs of a cover letter — no date, no addresses, no subject line, "
        "no salutation, no sign-off, no markdown, no placeholders. Write in {language}. Every "
        "factual claim about the candidate — skills, employers, job titles, numbers, dates, "
        "achievements — must come from the CV FACTS below; invent nothing, and state each experience "
        "at most once. Aim for {lo}-{hi} words and fit one page. Open with why the candidate fits "
        "THIS role (use RESEARCH for company specifics when present), give the strongest evidence "
        "next, then a brief close with a call to action and genuine thanks."
    )

    def __init__(
        self,
        executor,
        *,
        candidate_name: str = "",
        title: str = "",
        language: str = "en",
        tone: str = "neutral",
        focus: str = "balanced",
        cv_facts: str = "",
        personality_dossier: str = "",
        style_dossier: str = "",
        company_dossier: str = "",
        mode: str = "standard",
        posting_text: str = "",
        unsupported_claims: list[str] | None = None,
    ):
        self.executor = executor
        self.candidate_name = candidate_name
        self.title = title
        self.language = language
        self.tone = tone
        self.focus = focus
        self.cv_facts = cv_facts
        self.personality_dossier = personality_dossier
        self.style_dossier = style_dossier
        self.company_dossier = company_dossier
        self.mode = mode
        self.posting_text = posting_text
        self.unsupported_claims = unsupported_claims or []

    def write(self) -> str:
        """Return the composed body prose. '' when there are no CV facts or the LLM fails."""
        if not (self.cv_facts or "").strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("CoverLetterWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        lo, hi = self._TARGET_WORDS
        tone = self._TONE.get(self.tone, self._TONE["neutral"])
        focus = self._FOCUS.get(self.focus, self._FOCUS["balanced"])
        common = self._COMMON.format(language=_language_name(self.language), lo=lo, hi=hi)

        style = (
            f"STYLE (imitate this voice; it carries NO facts):\n{self.style_dossier}\n\n"
            if self.style_dossier
            else ""
        )
        personality = (
            "PERSONALITY (who the candidate is — shape emphasis and framing, not a source of hard "
            f"facts):\n{self.personality_dossier}\n\n"
            if self.personality_dossier
            else ""
        )
        research = (
            "RESEARCH (company facts — the ONLY source for claims about the company):\n"
            f"{self.company_dossier}\n\n"
            if self.company_dossier
            else ""
        )
        posting = ""
        if self.mode == "high" and self.posting_text:
            posting = (
                "JOB POSTING (context only, never a source of facts about the candidate):\n"
                f"{self.posting_text}\n\n"
            )
        repair = ""
        if self.unsupported_claims:
            claims = "\n".join(f"- {c}" for c in self.unsupported_claims)
            repair = (
                "A previous draft made these unsupported claims — remove them or replace them with "
                f"claims the CV FACTS actually state:\n{claims}\n\n"
            )
        return (
            f"{tone} {focus}\n{common}\n\n"
            f"CANDIDATE: {self.candidate_name}\nROLE: {self.title}\n\n"
            f"{style}{personality}{research}{posting}{repair}"
            f"CV FACTS (the only source of facts about the candidate):\n{self.cv_facts}\n\n"
            f"LETTER BODY:"
        )
```

**Replace `FaithfulnessCheck`** so its source of truth is a text `sources` blob (CV facts +
personality + research), not snippet objects — the `count=None`/`0`/`>0` honesty contract is
unchanged:

```python
class FaithfulnessCheck:
    """Grounding auditor for a generated cover-letter body: reads the body plus the SOURCES it was
    written from (the tailored CV facts + personality dossier + any company research) and lists
    every claim the sources do not support. The posting is deliberately NOT a source — a requirement
    in a posting must never be treated as a fact about the candidate.

    Line-format I/O (never JSON — see [[no-json-llm-io]]): 'UNSUPPORTED <n>' anchors the count, each
    bullet is one claim. On ANY failure it returns count=None ('not checked'), NEVER 0 — a failed
    audit must not read as a clean letter.
    """

    _INSTRUCTION = (
        "You are fact-checking a COVER LETTER BODY against the SOURCES it was written from.\n"
        "The sources are the ONLY permitted basis for factual claims about the candidate or the "
        "company — skills, employers, titles, numbers, dates, achievements, company facts. A claim "
        "is UNSUPPORTED if the sources do not state or clearly imply it. A personality trait rendered "
        "as a professional strength is supported by the trait — reframing is not fabrication.\n"
        "List every unsupported factual claim in the letter body.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'UNSUPPORTED <n>' — the number of unsupported claims (0 if none);\n"
        "  - then ONE line per claim, '- <claim, quoted or paraphrased>' (<=20 words), worst first;\n"
        "  - if every claim is grounded, write 'UNSUPPORTED 0' and nothing else.\n"
        "Do not flag style, tone, opinion, or first-person framing — only checkable facts. No JSON."
    )

    _COUNT_RE = re.compile(r"\bUNSUPPORTED\s+(\d+)\b", re.IGNORECASE)
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, body: str, sources: str, executor: Executor):
        self.body = body
        self.sources = sources
        self.executor = executor

    def critique(self) -> dict:
        """Return {'count': int | None, 'claims': [str]}.
        None = audit failed / unreadable ('not checked', NOT clean); 0 = clean; >0 = that many."""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("FaithfulnessCheck: LLM call failed")
            return {"count": None, "claims": []}
        return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\n\n"
            f"SOURCES (the only source of truth):\n{self.sources or '(none)'}\n\n"
            f"LETTER BODY:\n{self.body}\n\nAUDIT:"
        )
```

### 5. `backend/jac/cover_letter.py` — rewrite the orchestrator

The whole snippet apparatus goes. New top-of-file imports and the `LETTER_STUB` sentinel:

```python
from jac.llm_prompts import CompanyResearcher, CoverLetterWriter, FaithfulnessCheck

# (drop: SnippetEmbed, LetterCritic, ParagraphGroundingCheck, PersonalParagraphWriter, ResumeSnippet)

# Loud placeholder when the writer produced nothing at all (LLM down / empty reply). Deliberately
# jarring so it can't be sent by accident; the frontend export blocker refuses on it (guide 2).
LETTER_STUB = "⚠️⚠️ THE MODEL COULD NOT WRITE THIS LETTER — regenerate before sending ⚠️⚠️"

# SOFT company-hook stub. Appended when a run has no company research (self-hosted standard, or a
# failed commercial lookup) so the letter carries no company specifics. Unlike LETTER_STUB it does
# NOT block export — the frontend strips this line from pdf/md, so a right-away export is clean; it
# just nudges the user in the editor to add a company-specific line (Lukas, 2026-07-23).
COMPANY_STUB = "⟨ add one line on why THIS company — omitted from exports until you do ⟩"

# Default matrix cell when neither the run nor the profile specifies one (mirrors the
# PersonalityProfile.Tone/Focus defaults; kept as literals to avoid an app-load spa import).
DEFAULT_TONE = "neutral"
DEFAULT_FOCUS = "balanced"
```

The language furniture (`_SALUTATION_*`, `_SUBJECT`, `_CLOSING`) stays as-is. **Delete
`SnippetSelector` entirely.** Simplify `editable_body`:

```python
def editable_body(letter: dict) -> str:
    """The sendable middle of a built letter — the composed body (the company-fit opening is now
    folded into it). Subject/salutation/date/closing/addresses live in `letter_meta`, re-assembled
    at render/export time."""
    return letter.get("body", "")
```

`CoverLetter.__init__` — drop `max_body_snippets`, add the per-run matrix override (`tone`/`focus`
default `""` → resolved against the profile in `build`):

```python
    def __init__(
        self,
        user,
        job_posting,
        cv,
        *,
        address=None,
        mode: str = Mode.standard,
        executor: Executor,
        tone: str = "",
        focus: str = "",
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
        self.mode = mode
        self.executor = executor
        self.tone = tone
        self.focus = focus
```

Drop the `_REWRITE_TAX`/`_MIN_BODY_RATIO`/`_SHRINKAGE_NOTE`/`_MAX_BODY_WORDS`/`_OVERLENGTH_NOTE`/
`MAX_BODY_SNIPPETS` class constants. New `build()`:

```python
    def build(self) -> dict:
        language = (getattr(self.job_posting, "language", "") or "en").lower()[:2]
        title = getattr(self.job_posting, "title", "") or ""

        profile = self._profile()
        personality = (
            profile.ensure_dossier(executor=self.executor)
            if profile and profile.has_answers()
            else ""
        )
        style = (
            profile.ensure_style_dossier(executor=self.executor)
            if profile and profile.has_sample()
            else ""
        )
        tone = self.tone or (profile.letter_tone if profile else "") or DEFAULT_TONE
        focus = self.focus or (profile.letter_focus if profile else "") or DEFAULT_FOCUS
        cv_facts = self._cv_facts()

        # Company research is capability-driven, not mode-gated: any web-search-capable executor
        # (commercial) folds a company dossier into the letter; HirschAI simply skips it.
        research = {"ok": False, "dossier": "", "sources": []}
        if self.executor.supports_web_search:
            research = CompanyResearcher(
                self._recipient()["company"],
                self._posting_text(),
                executor=self.executor,
                language=language,
            ).research()

        body = CoverLetterWriter(
            executor=self.executor,
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            tone=tone,
            focus=focus,
            cv_facts=cv_facts,
            personality_dossier=personality,
            style_dossier=style,
            company_dossier=research["dossier"],
            mode=self.mode,
            posting_text=self.job_posting,
        ).write()

        weave_failed = not body
        if weave_failed:
            body = LETTER_STUB

        sources = self._sources(cv_facts, personality, research["dossier"])
        grounding = self._grounding(body, sources, weave_failed)
        body, grounding = self._repair(
            body, sources, grounding, language, title, tone, focus, personality, style,
            research["dossier"],
        )

        # Soft company-hook stub — appended AFTER repair (so the audit/rewrite don't touch it) when
        # there are no company specifics to weave in. The frontend strips it from exports; a real
        # letter that DID get research gets nothing appended.
        if not weave_failed and not research["ok"]:
            body = f"{body}\n\n{COMPANY_STUB}"

        result = {
            "language": language,
            "subject": self._subject(language, title),
            "salutation": self._salutation(language),
            "body": body,
            "sender": self._sender(),
            "recipient": self._recipient(),
            "date": timezone.localdate().isoformat(),
            "closing": _CLOSING.get(language, _CLOSING["en"]),
            "tone": tone,
            "focus": focus,
            "grounding": grounding,
            "sources": research["sources"],
            "is_stub": weave_failed,
        }
        result["text"] = self.render_markdown(result)
        return result
```

Add the new helpers (near the other assembly helpers); keep `_candidate_name`, `_sender`,
`_recipient`, `_posting_text`, `_subject`, `_salutation`:

```python
    def _profile(self):
        """The user's PersonalityProfile row, or None. The only cross-app (spa) dependency."""
        try:
            from spa.models import PersonalityProfile

            return PersonalityProfile.objects.filter(user=self.user).first()
        except Exception:  # noqa: BLE001
            return None

    def _cv_facts(self) -> str:
        """The tailored CV rendered as fact lines — the writer's only source of candidate facts.
        Reuses cv._flatten_entries() (the same id—text shape the selectors already score)."""
        try:
            entries = self.cv._flatten_entries()
        except Exception:  # noqa: BLE001
            return ""
        return "\n".join(f"- {e['text']}" for e in entries if e.get("text"))

    def _sources(self, cv_facts: str, personality: str, company_dossier: str) -> str:
        """The grounding audit's source-of-truth blob: CV facts + personality + research. STYLE and
        the posting are deliberately excluded (voice is not fact; the posting is the fabrication
        vector)."""
        parts = [f"CV FACTS:\n{cv_facts}"]
        if personality:
            parts.append(f"PERSONALITY:\n{personality}")
        if company_dossier:
            parts.append(f"RESEARCH:\n{company_dossier}")
        return "\n\n".join(parts)
```

New `render_markdown` — same as today minus the `personal_paragraph` block:

```python
    def render_markdown(self, r: dict) -> str:
        """Assemble the letter as plain text. Empty address lines are omitted."""
        snd, rcp = r["sender"], r["recipient"]
        out: list[str] = []

        out.append(snd["name"])
        for line in (
            snd["street"],
            snd["address_line2"],
            " ".join(p for p in (snd["zip"], snd["city"]) if p),
            snd["country"],
        ):
            if line:
                out.append(line)
        contact = " · ".join(p for p in (snd["email"], snd["phone"]) if p)
        if contact:
            out.append(contact)
        out.append("")

        for line in (
            rcp["company"],
            rcp["contact_name"],
            rcp["street"],
            rcp["address_line2"],
            " ".join(p for p in (rcp["zip"], rcp["city"]) if p),
            rcp["country"],
        ):
            if line:
                out.append(line)
        out.append("")

        out.append(r["date"])
        out.append("")
        out.append(f"**{r['subject']}**")
        out.append("")
        out.append(r["salutation"])
        out.append("")
        out.append(r["body"])
        out.append("")
        out.append(_CLOSING.get(r["language"], _CLOSING["en"]))
        out.append("")
        out.append(snd["name"])
        out.append(r["closing"])
        return "\n".join(out).rstrip() + "\n"
```

Grounding + repair — one audit, one rewrite, re-audited; `repaired` now applies to every mode
(grounding always runs). **Delete** `_ai_share`, `_critique`, `_shrunk`, `_overlong`, `_stub`,
`_personal_paragraph`, `_personality_dossier`:

```python
    def _grounding(self, body, sources, weave_failed) -> dict:
        if weave_failed:  # the stub is not a letter — nothing to audit, and not "clean"
            return {"count": None, "claims": []}
        return FaithfulnessCheck(body, sources, executor=self.executor).critique()

    def _repair(
        self, body, sources, grounding, language, title, tone, focus, personality, style,
        company_dossier,
    ):
        """ONE combined repair pass, never a loop: the grounding audit's unsupported claims feed a
        single rewrite, which is then re-audited (safety stays honest about the shipped body).
        `repaired` = True only when a rewrite actually replaced the body."""
        claims = grounding.get("claims") or []
        if not claims:
            return body, {**grounding, "repaired": False}
        rewritten = CoverLetterWriter(
            executor=self.executor,
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            tone=tone,
            focus=focus,
            cv_facts=self._cv_facts(),
            personality_dossier=personality,
            style_dossier=style,
            company_dossier=company_dossier,
            mode=self.mode,
            posting_text=self.job_posting,
            unsupported_claims=claims,
        ).write()
        if not rewritten:
            return body, {**grounding, "repaired": False}
        new_g = FaithfulnessCheck(rewritten, sources, executor=self.executor).critique()
        return rewritten, {**new_g, "repaired": True}
```

### 6. `backend/jac/models.py` — run override + delete `ResumeSnippet`

On `GenerationRun`, beside `params`, add the per-run matrix override (kept choice-less to avoid an
app-load spa import; the serializer validates against `PersonalityProfile.Tone/Focus`):

```python
    # Optional per-run override of the user's default letter matrix cell (PersonalityProfile). Blank
    # = use the profile default. Validated against spa PersonalityProfile.Tone/.Focus in the
    # create serializer.
    letter_tone = models.CharField(max_length=16, blank=True)
    letter_focus = models.CharField(max_length=16, blank=True)
```

**Delete the entire `ResumeSnippet` class.** Then grep the app and remove every reference (see the
removal checklist below) before running `makemigrations`.

### 7. `backend/jac/serializers.py` — run serializers + delete snippet serializer

`GenerationRunCreateSerializer`: declare the two override fields, add them to `Meta.fields`, and
validate in `validate()` (append before `return attrs`):

```python
    letter_tone = serializers.CharField(required=False, allow_blank=True, default="")
    letter_focus = serializers.CharField(required=False, allow_blank=True, default="")
```

```python
        # fields = [..., "min_skill_proficiency", "letter_tone", "letter_focus"]

        from spa.models import PersonalityProfile

        tone = (attrs.get("letter_tone") or "").strip()
        if tone and tone not in PersonalityProfile.Tone.values:
            raise serializers.ValidationError({"letter_tone": ["unknown tone"]})
        focus = (attrs.get("letter_focus") or "").strip()
        if focus and focus not in PersonalityProfile.Focus.values:
            raise serializers.ValidationError({"letter_focus": ["unknown focus"]})
        attrs["letter_tone"] = tone
        attrs["letter_focus"] = focus
        return attrs
```

`GenerationRunSerializer.Meta.fields`: add `"letter_tone", "letter_focus"` (read-only, already in
the blanket `read_only_fields = fields`). **Delete `ResumeSnippetSerializer`** and drop
`ResumeSnippet` from the `from jac.models import (...)` block.

### 8. `backend/jac/tasks.py` — thread tone/focus, drop snippet vectors

In `generate_run`, pass the override through to the letter:

```python
            letter = CoverLetter(
                user, jp, cv, address=addr, mode=mode, executor=executor,
                tone=run.letter_tone, focus=run.letter_focus,
            ).build()
```

In `sync_user_vectors`, remove the snippet `reconcile` block (keep the CV one); drop the
`vectors.DOC_SNIPPET` / `snippet_corpus` references.

### 9. Removal checklist — `ResumeSnippet` (grep-driven)

Full removal. Work through each and confirm with `rg -n "ResumeSnippet|snippet_corpus|DOC_SNIPPET|load_snippets" backend` coming back empty (test files excepted — they're rewritten in the Tests section):

- `jac/views.py` — delete the snippet viewset/queryset.
- `jac/urls.py` — delete the snippet router registration.
- `jac/admin.py` — delete the `ResumeSnippet` admin registration.
- `jac/signals.py` — delete the snippet post_save/delete → vector-sync signal.
- `jac/vectors.py` — delete `snippet_corpus`, `DOC_SNIPPET`, and any snippet branch in `reconcile`.
- `jac/management/commands/load_snippets.py` — delete the file.
- `jac/management/commands/cv_import.py`, `cv_export.py`, `vector_sync.py` — strip the snippet
  handling; leave the CV paths intact.

### 10. Migrations

After every reference is gone:

```bash
cd backend
python manage.py makemigrations spa jac
```

Expect: `spa` — 6 `AddField`s on `PersonalityProfile`; `jac` — 2 `AddField`s on `GenerationRun`
plus `DeleteModel` `ResumeSnippet` (and a `RemoveField`/M2M table drop for its `domains`/`skills`).
Review the generated `jac` migration includes the delete before `migrate`.

---

## Tests

Land these to disk **skip-marked** when you cut the branch; step 0 = unskip. They start red against
the not-yet-written code. All are mocked/pure (no live LLM) except where noted — the live quality
gate is guide 3.

- `backend/spa/tests/test_personality.py` — extend:
  - `StyleDistillerTests` — `distill()` returns `''` on blank sample and on LLM error (patch
    `spa.distill.complete`); `_prompt()` contains the sample and the "only the style" instruction.
  - `EnsureStyleDossierTests` — `ensure_style_dossier` returns `''` with no sample; distils + caches
    on first call (patch `StyleDistiller`); returns cache when fresh; re-distils when
    `sample_updated_at > style_built_at` (`style_stale`).
  - `PersonalityProfileApiTests` — PATCH `letter_tone`/`letter_focus`/`writing_sample` round-trips;
    an unknown `letter_tone` 400s; `style_dossier`/`sample_updated_at` are read-only; saving a new
    `writing_sample` stamps `sample_updated_at`.
- `backend/jac/tests/test_pipeline.py` — replace the snippet-era `CoverLetter` tests:
  - `CoverLetterWriterPromptShapeTests` — `_prompt()` carries the tone + focus clauses, the CV-facts
    block, the style/personality/research blocks when supplied, and the posting block **only** on
    `mode="high"`; `write()` returns `''` with empty `cv_facts`.
  - `FaithfulnessSourcesTests` — `_prompt()` embeds the `sources` blob and withholds the posting;
    `critique()` returns `count=None` on LLM error (patch `jac.llm_prompts.complete`).
  - `CoverLetterBuildTests` — patch `CoverLetterWriter.write`/`FaithfulnessCheck.critique` and a
    fake filtered `cv`; assert `build()` returns `tone`/`focus`/`grounding`/`sources`/`is_stub`, no
    `ai_share`/`snippets_used`/`personal_paragraph*` keys, `LETTER_STUB` body + `is_stub=True` when
    the writer returns `''`, and that a claim in the first audit triggers exactly one rewrite with
    `grounding.repaired=True`. Also: with no research (`supports_web_search` False) the body **ends
    with `COMPANY_STUB`**; on a web-search run whose research succeeds it does **not**; a
    `weave_failed` body is `LETTER_STUB` with **no** `COMPANY_STUB` appended.
  - `GenerationRunMatrixOverrideTests` — `POST /api/jac/generations/` accepts
    `letter_tone`/`letter_focus`, persists them, rejects an unknown value; `tasks.generate_run`
    passes them to `CoverLetter` (assert via a patched `CoverLetter`).
  - Delete the `SNIPPETS` fixture + every snippet-based assertion; delete `test_prompts.py`'s
    `SNIPPETS`/`CoverLetterWriterPromptTests`/`FaithfulnessPromptTests` snippet wiring (guide 3
    re-adds the live writer/faithfulness checks against CV facts).
- `backend/jac/tests/test_api.py` — drop the `ResumeSnippet` CRUD tests (the endpoint is gone).

Run: `cd backend && python manage.py test spa.tests.test_personality jac.tests.test_pipeline jac.tests.test_api`

---

## Verification

1. `python manage.py makemigrations spa jac && python manage.py migrate` — clean, no `ResumeSnippet`
   table left; `PersonalityProfile`/`GenerationRun` gain their columns.
2. `rg -n "ResumeSnippet|snippet_corpus|DOC_SNIPPET" backend --glob '!**/tests/**'` — empty.
3. `python manage.py test spa jac llm_connector` — green (the mocked suite).
4. **Live smoke (HirschAI up):** in `manage.py shell`, build a letter for a real user with a filled
   personality profile (answers + a writing sample + tone=personal, focus=balanced):
   ```python
   from jac.cover_letter import CoverLetter
   from jac.cv import CV
   from llm_connector.executor import Executor
   # ... build a filtered cv + a JobPosting jp for user u ...
   letter = CoverLetter(u, jp, cv, mode="standard", executor=Executor("ollama")).build()
   print(letter["tone"], letter["focus"], letter["grounding"])
   print(letter["body"])
   ```
   Expect a real body in the chosen voice, no snippet stitching, `grounding["count"]` an int (0 =
   clean) or None (audit down), `is_stub=False`, and no `ai_share`/`personal_paragraph` keys.
5. Kill ollama, rebuild → `is_stub=True`, body = `LETTER_STUB`, `grounding["count"] is None`.
6. A working HirschAI standard rebuild → the body **ends with `COMPANY_STUB`** (no research), which
   the frontend will strip on export (guide 2).

**Done looks like:** a run produces a matrix-shaped, style-matched letter grounded in the tailored
CV + dossiers, with one grounding audit + repair pass and no snippet/ai_share/personal-paragraph
machinery anywhere in `backend/jac` or `backend/spa`.

## Results

<!-- Human fills this in after testing: raw test output, observed issues, what works. -->
