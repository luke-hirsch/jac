# [backend] Cover-letter generation pipeline

## Context / goal

Roadmap item #1: **cover-letter generation**. With the CV ladder done, the next stage turns a
filtered CV + a job posting into a tailored cover letter.

Design (decided with Lukas):

- **Snippets are authoritative.** `ResumeSnippet` rows are real, human-written boilerplate. The
  pipeline *selects* the right snippets deterministically (driven by which CV entries survived
  filtering) and the chat model only *weaves* them — stitching + smoothing, never inventing
  facts. This is the anti-AI-slop guarantee.
- **Grade tunes the weave, not the selection.** `light` = glue only; `standard` = smooth
  transitions; `strong` = polished prose, reordered for impact. Same snippets either way.
- **Addresses.** The sender address lives on `spa.UserProfile` (already noted there as "used to
  pre-fill job applications in JAC"). The recipient address is extracted from the posting by an
  LLM into a new `JobPostAddress`, which hangs 1:1 off a new **`JobPosting`** parent (posting
  text, role title, language) — the future home for application tracking + the generated CV /
  letter (roadmap items #2/#3).
- **Email** is captured on `JobPostAddress` now (needed later for sending / tracking), even
  though nothing sends mail yet.

Scope of *this* guide: **backend pipeline + models + admin + a `cover_letter_test` management
command** (mirrors `cv_test` / `cv_eval`). No REST endpoint — roadmap item #2 (frontend render)
will define the API shape against a working pipeline.

### What the LLM does / doesn't do here

- **Selection of snippets** (which intro, which body, which closing): deterministic, in
  `cover_letter.py`. No LLM.
- **Address extraction** from the posting: LLM, line-format I/O (`AddressExtract`).
- **Weaving** the chosen snippets into body prose: LLM, free prose out (`CoverLetterWriter`).
  Free prose is the one place the `no-json-llm-io` line-format rule doesn't apply — the
  deliverable *is* prose. Structured extraction (`AddressExtract`) stays line-format.

## Affected files

| path | change |
| --- | --- |
| `backend/spa/models.py` | add postal-address fields to `UserProfile` (sender block) |
| `backend/spa/serializers.py` | surface the new address fields on the existing profile serializer |
| `backend/spa/admin.py` | (optional) show address fields in the profile admin |
| `backend/jac/models.py` | new `JobPosting` + `JobPostAddress` (1:1) models |
| `backend/jac/llm_prompts.py` | new `AddressExtract` (line-format) + `CoverLetterWriter` (prose) |
| `backend/jac/cover_letter.py` | **new** — `SnippetSelector` + `CoverLetter` orchestrator + render |
| `backend/jac/admin.py` | register `JobPosting` with a `JobPostAddress` inline |
| `backend/jac/management/commands/cover_letter_test.py` | **new** — smoke-test command |
| `backend/jac/tests.py` | append the test classes below |
| migrations | `python manage.py makemigrations spa jac` (generated, not hand-written) |

---

## The code

### 1. `backend/spa/models.py` — sender address on `UserProfile`

Add these fields to `UserProfile`, right after the `github_url` line in the
"Professional contact" block:

```python
    # Postal address — the sender block on JAC cover letters.
    street = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    zip = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
```

Name + email for the sender come from `auth.User` (`first_name`/`last_name`/`email`); the
pipeline falls back to `profile.display_name` then `username` for the name.

### 2. `backend/spa/serializers.py` — expose them

Add `"street", "address_line2", "zip", "city", "country"` to the `fields` tuple of
`UserProfileSerializer` (anywhere after `"github_url"`). No other change — the existing profile
endpoint then edits them.

### 3. `backend/spa/admin.py` — (optional) admin visibility

The default `ModelAdmin` already shows all editable fields on the change form, so no change is
strictly required. If you want them grouped, add a `fieldsets`; otherwise skip.

### 4. `backend/jac/models.py` — `JobPosting` + `JobPostAddress`

Append to the end of the file (after `ResumeSnippet`):

```python
class JobPosting(models.Model):
    """A job posting the user is tailoring an application to.

    Owns the raw posting text plus the role title and detected language. Parent for the
    extracted recipient address (`JobPostAddress`, 1:1) and — later — the generated CV and
    cover letter (roadmap items #2/#3). User-scoped like every other JAC record.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="job_postings"
    )
    title = models.CharField(max_length=200, blank=True)
    posting_text = models.TextField()
    # ISO-639-1 code of the posting language (e.g. "en", "de"); drives salutation/subject and
    # the weave-prompt language hint. Best-effort from AddressExtract; defaults to English.
    language = models.CharField(max_length=8, default="en")
    source_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title or 'Posting'} ({self.created_at:%Y-%m-%d})"


class JobPostAddress(models.Model):
    """Employer contact block extracted from a job posting.

    Every field is `blank=True` — extraction is lossy and a posting rarely states all of them;
    the cover-letter renderer simply omits empty lines. `email`/`phone` are captured for later
    application-sending / tracking.
    """

    job_posting = models.OneToOneField(
        JobPosting, on_delete=models.CASCADE, related_name="address"
    )
    company = models.CharField(max_length=200, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    street = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    zip = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)

    def __str__(self) -> str:
        return self.company or f"Address for posting {self.job_posting_id}"
```

> Note (deferred): `ResumeSnippet` keeps its `job`/`project` FKs only. Lukas flagged that
> certifications might be worth linking too — leave that for a follow-up if cert-driven body
> snippets prove useful.

### 5. `backend/jac/llm_prompts.py` — `AddressExtract` + `CoverLetterWriter`

`re` and `logging` are already imported at the top of the module. Append both classes:

```python
class AddressExtract:
    """Pull the employer's contact block out of a job posting with the chat/instruct model.

    Line-format I/O (`<field>: <value>` per line, parsed with `re`, unknown/blank/placeholder
    lines skipped) — never JSON (see the `no-json-llm-io` memory). Any failure or unparseable
    reply -> {} so the caller proceeds with blanks (the renderer just omits missing lines).
    """

    _FIELDS = (
        "company", "contact_name", "street", "address_line2", "zip", "city",
        "country", "email", "phone", "title", "language",
    )
    _INSTRUCTION = (
        "Extract the EMPLOYER's contact details from the job posting below. Output one\n"
        "'<field>: <value>' per line, using exactly these field names:\n"
        "  company, contact_name, street, address_line2, zip, city, country, email, phone,\n"
        "  title, language\n"
        "  - title = the role being advertised.\n"
        "  - language = ISO-639-1 code of the posting language (en, de, …).\n"
        "Omit a line entirely if the posting does not state that field — never guess.\n"
        "No prose, no markdown, no JSON."
    )
    _MAX_POST_CHARS = 12000
    _PLACEHOLDERS = {"none", "n/a", "na", "-", "—", "unknown", "null"}
    # `<field>: <value>` or `<field> - <value>`; value required (blank values are dropped).
    _LINE = re.compile(r"^\s*([a-zA-Z_]+)\s*[:\-]\s*(.+?)\s*$")

    def __init__(self, job_post_text: str, *, alias: str = "default", user=None):
        self.job_post_text = job_post_text
        self.alias = alias
        self.user = user

    def extract(self) -> dict:
        """Return {field: value} for the fields the posting states. {} on any failure."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("AddressExtract: LLM call failed")
            return {}
        return self._parse(raw)

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        return f"{self._INSTRUCTION}\n\nJOB POSTING:\n{post}\n\nFIELDS:"

    def _parse(self, raw: str) -> dict:
        allowed = set(self._FIELDS)
        out: dict[str, str] = {}
        for line in (raw or "").splitlines():
            m = self._LINE.match(line)
            if not m:
                continue
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            if key in allowed and val and val.lower() not in self._PLACEHOLDERS:
                out[key] = val[:200]
        return out


class CoverLetterWriter:
    """Weave selected `ResumeSnippet`s into cover-letter body prose with the chat model.

    Snippet content is authoritative: the model stitches and smooths, it does not invent facts.
    `grade` tunes only how much rewriting is allowed (light = glue; standard = smooth
    transitions; strong = polished, reordered for impact) — never the content. Output is free
    prose (the body), the one place structured line-format I/O does not apply. Any failure
    -> '' so the caller falls back to the raw stitched snippets.
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
            "against THIS posting. Improve prose and transitions freely, but every factual "
            "claim must come from the snippets — invent nothing."
        ),
    }
    _COMMON = (
        "Write ONLY the body paragraphs of a cover letter — no date, no addresses, no subject "
        "line, no salutation, no sign-off, no markdown, no placeholders. Write in {language}."
    )
    _MAX_POST_CHARS = 8000

    def __init__(
        self,
        job_post_text: str,
        snippets: list,
        *,
        candidate_name: str = "",
        title: str = "",
        language: str = "en",
        grade: str = "standard",
        alias: str = "default",
        user=None,
    ):
        self.job_post_text = job_post_text
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
        post = self.job_post_text[: self._MAX_POST_CHARS]
        blocks = "\n\n".join(
            f"[{s.get_kind_display()}] {s.title}\n{s.content}" for s in self.snippets
        )
        return (
            f"{clause}\n{common}\n\n"
            f"CANDIDATE: {self.candidate_name}\n"
            f"ROLE: {self.title}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"SNIPPETS (in order, use them all):\n{blocks}\n\nLETTER BODY:"
        )
```

### 6. `backend/jac/cover_letter.py` — orchestrator (new file)

```python
"""Cover-letter pipeline: pick the right ResumeSnippets for a filtered CV + posting, weave
them into a letter body with the chat model, and assemble the sender/recipient/subject around
that body.

Snippet *selection* is deterministic — driven by which CV entries survived filtering — so the
LLM only *weaves* (see CoverLetterWriter in llm_prompts.py). Grade tunes the weave's writing
quality, not the content: snippet text stays authoritative, which is the anti-AI-slop guard.
"""

from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.utils import timezone

from jac.llm_prompts import CoverLetterWriter
from jac.models import ResumeSnippet

logger = logging.getLogger(__name__)

# Language-keyed letter furniture. English is the fallback for any unmapped code.
_SALUTATION_NAMED = {"en": "Dear {name},", "de": "Sehr geehrte/r {name},"}
_SALUTATION_GENERIC = {"en": "Dear Hiring Team,", "de": "Sehr geehrte Damen und Herren,"}
_SUBJECT = {"en": "Application for {title}", "de": "Bewerbung als {title}"}
_CLOSING = {"en": "Kind regards,", "de": "Mit freundlichen Grüßen,"}


class SnippetSelector:
    """Pick 1 intro + 1 closing + up to `max_body` body snippets for a filtered CV.

    Scoring is relevance to what survived filtering: a snippet linked to a kept job/project
    scores high; domain/skill overlap with the kept set adds to it. Body snippets are only
    kept if they score > 0 (i.e. they actually connect to the tailored CV). Intro/closing are
    the best-scoring of their kind, or None when the user has none.
    """

    _BODY_KINDS = (
        ResumeSnippet.Kind.achievement,
        ResumeSnippet.Kind.value_statement,
        ResumeSnippet.Kind.other,
    )

    def __init__(self, cv, user_pk: int, max_body: int = 4):
        self.cv = cv
        self.user_pk = user_pk
        self.max_body = max_body
        self._ctx = self._kept_context()

    def _kept_context(self) -> dict:
        """Gather the pks/domains/skills of the entries that survived filtering."""
        e = self.cv.entries
        domains: set[int] = set()
        for section in ("jobs", "projects", "skills", "educations", "certifications"):
            for o in e.get(section, []):
                if hasattr(o, "domains"):
                    domains.update(d.pk for d in o.domains.all())
        return {
            "jobs": {o.pk for o in e.get("jobs", [])},
            "projects": {o.pk for o in e.get("projects", [])},
            "skills": {o.pk for o in e.get("skills", [])},
            "domains": domains,
        }

    def _score(self, s: ResumeSnippet) -> int:
        sc = 0
        if s.job_id and s.job_id in self._ctx["jobs"]:
            sc += 10
        if s.project_id and s.project_id in self._ctx["projects"]:
            sc += 10
        sc += 2 * len({d.pk for d in s.domains.all()} & self._ctx["domains"])
        sc += 1 * len({sk.pk for sk in s.skills.all()} & self._ctx["skills"])
        return sc

    def select(self) -> dict:
        active = list(
            ResumeSnippet.objects.filter(user=self.user_pk, is_active=True)
            .prefetch_related("domains", "skills")
        )
        intros = [s for s in active if s.kind == ResumeSnippet.Kind.intro]
        closings = [s for s in active if s.kind == ResumeSnippet.Kind.closing]
        bodies = [s for s in active if s.kind in self._BODY_KINDS]

        intro = max(intros, key=self._score, default=None)
        closing = max(closings, key=self._score, default=None)
        scored = sorted(
            ((self._score(s), s) for s in bodies), key=lambda t: t[0], reverse=True
        )
        body = [s for score, s in scored if score > 0][: self.max_body]

        ordered = [s for s in (intro, *body, closing) if s is not None]
        return {"intro": intro, "body": body, "closing": closing, "ordered": ordered}


class CoverLetter:
    """Build a tailored cover letter from a filtered CV, a JobPosting, and the user's snippets.

    `cv` must already be filtered (CV.filter_cv + CV.apply_selection applied). `address` may be
    passed explicitly (the test command builds transient instances); otherwise it is read from
    `job_posting.address`. `build()` returns a dict of letter parts plus a rendered `text`.
    """

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

    def build(self) -> dict:
        language = (getattr(self.job_posting, "language", "") or "en").lower()[:2]
        title = getattr(self.job_posting, "title", "") or ""
        sel = SnippetSelector(
            self.cv, self.user.pk, max_body=self.max_body_snippets
        ).select()

        body = CoverLetterWriter(
            getattr(self.job_posting, "posting_text", "") or "",
            sel["ordered"],
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            grade=self.grade,
            alias=self.alias,
            user=self.user,
        ).write()
        if not body:  # LLM failed / no model — fall back to the raw boilerplate, no slop.
            body = "\n\n".join(s.content for s in sel["ordered"])

        result = {
            "language": language,
            "subject": self._subject(language, title),
            "salutation": self._salutation(language),
            "body": body,
            "sender": self._sender(),
            "recipient": self._recipient(),
            "date": timezone.localdate().isoformat(),
            "snippets_used": [f"{s.kind}:{s.pk}" for s in sel["ordered"]],
        }
        result["text"] = self.render_markdown(result)
        return result

    # --- assembly helpers --------------------------------------------------------------

    def _candidate_name(self) -> str:
        profile = getattr(self.user, "profile", None)
        if profile and profile.display_name:
            return profile.display_name
        full = f"{self.user.first_name} {self.user.last_name}".strip()
        return full or self.user.username

    def _sender(self) -> dict:
        p = getattr(self.user, "profile", None)
        g = lambda attr: (getattr(p, attr, "") or "") if p else ""
        return {
            "name": self._candidate_name(),
            "email": self.user.email or "",
            "phone": g("phone"),
            "street": g("street"),
            "address_line2": g("address_line2"),
            "zip": g("zip"),
            "city": g("city"),
            "country": g("country"),
            "website": g("website"),
            "linkedin": g("linkedin_url"),
        }

    def _recipient(self) -> dict:
        a = self.address
        g = lambda attr: (getattr(a, attr, "") or "") if a else ""
        return {
            "company": g("company"),
            "contact_name": g("contact_name"),
            "street": g("street"),
            "address_line2": g("address_line2"),
            "zip": g("zip"),
            "city": g("city"),
            "country": g("country"),
            "email": g("email"),
            "phone": g("phone"),
        }

    def _subject(self, language: str, title: str) -> str:
        title = title or "the advertised position"
        return _SUBJECT.get(language, _SUBJECT["en"]).format(title=title)

    def _salutation(self, language: str) -> str:
        name = self._recipient()["contact_name"]
        if name:
            return _SALUTATION_NAMED.get(language, _SALUTATION_NAMED["en"]).format(
                name=name
            )
        return _SALUTATION_GENERIC.get(language, _SALUTATION_GENERIC["en"])

    def render_markdown(self, r: dict) -> str:
        """Assemble the letter as plain text. Empty address lines are omitted."""
        snd, rcp = r["sender"], r["recipient"]
        out: list[str] = []

        # Sender block.
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

        # Recipient block.
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
        return "\n".join(out).rstrip() + "\n"
```

### 7. `backend/jac/admin.py` — register the new models

Add the import and an inline + admin:

```python
from .models import (
    Certification,
    Domain,
    Education,
    Job,
    JobPostAddress,   # add
    JobPosting,       # add
    Language,
    Location,
    Project,
    ResumeSnippet,
    Skill,
)


class JobPostAddressInline(admin.StackedInline):
    model = JobPostAddress
    extra = 0


@admin.register(JobPosting)
class JobPostingAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "language", "created_at")
    list_filter = ("language",)
    search_fields = ("title", "posting_text")
    inlines = [JobPostAddressInline]
```

### 8. `backend/jac/management/commands/cover_letter_test.py` — smoke test (new file)

```python
"""Smoke-test the cover-letter pipeline over one or more postings.

Per posting: runs the CV filter, extracts the recipient address from the posting text, builds
a cover letter, and writes a `<alias>__<slug>.cover.md` artifact. Transient by default (no DB
rows for JobPosting/JobPostAddress); pass --persist to save them for inspection in admin.

Grade & model selection mirror cv_eval: --llm picks the LLMConfig alias (default "default"),
--grade forces a grade (else auto-detected from the model's strength).

Usage:
    python manage.py cover_letter_test --user 1 --job-file data/test_job.md
    python manage.py cover_letter_test --user 1 --jobs-dir data/postings --grade standard
    python manage.py cover_letter_test --user 1 --job-file p.md --llm reasoning --persist
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from llm_connector.conf import get_alias_strength

from jac.cover_letter import CoverLetter
from jac.cv import CV
from jac.llm_prompts import AddressExtract
from jac.models import JobPostAddress, JobPosting

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ADDRESS_FIELDS = (
    "company", "contact_name", "street", "address_line2", "zip", "city",
    "country", "email", "phone",
)


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned or "posting"


class Command(BaseCommand):
    help = "Build cover letters for a postings corpus and write the artifacts."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="User pk")
        parser.add_argument("--jobs-dir", type=str, help="Directory of *.txt / *.md postings")
        parser.add_argument("--job-file", type=str, help="A single posting file")
        parser.add_argument("--grade", type=str, default=None,
                            choices=["light", "standard", "strong"],
                            help="Force a weave grade. Omit to auto-detect from the model.")
        parser.add_argument("--llm", type=str, default="default",
                            help="LLMConfig alias to use (default 'default').")
        parser.add_argument("--out-dir", type=str, default=None,
                            help="Output dir (default: data/cover_letters/<UTC-timestamp>)")
        parser.add_argument("--persist", action="store_true",
                            help="Save JobPosting + JobPostAddress rows instead of transient.")

    def handle(self, *args, **opts):
        write = self.stdout.write
        user = User.objects.filter(pk=opts["user"]).first()
        if not user:
            raise CommandError(f"No user with pk={opts['user']}")

        postings: list[tuple[str, str]] = []
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

        alias = opts["llm"]
        grade = opts["grade"] or get_alias_strength(alias, user=user)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = (
            Path(opts["out_dir"]) if opts["out_dir"]
            else _REPO_ROOT / "data" / "cover_letters" / stamp
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        write(f"cover_letter_test — {len(postings)} posting(s)  alias={alias} grade={grade}")
        write(f"  user={user.pk}  → {out_dir}\n")

        for slug, text in postings:
            self._one(user, slug, text, alias, grade, opts["persist"], out_dir, write)

    def _one(self, user, slug, text, alias, grade, persist, out_dir, write):
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
            user, jp, cv, address=addr, grade=grade, alias=alias
        ).build()

        stem = f"{_safe(alias)}__{slug}"
        (out_dir / f"{stem}.cover.md").write_text(result["text"], encoding="utf-8")
        write(
            f"  {slug:<28} {len(result['snippets_used'])} snippet(s), "
            f"recipient={result['recipient']['company'] or '—'}"
            + ("  [persisted]" if persist else "")
        )
```

---

## Tests

`jac/tests.py` already imports `from unittest.mock import patch`, `from datetime import date`,
`from django.contrib.auth.models import User`, and `from jac.cv import CV`. Add these imports
(if not already present) and append the classes below.

```python
from jac.cover_letter import CoverLetter, SnippetSelector
from jac.llm_prompts import AddressExtract
from jac.models import (
    Domain,
    Job,
    JobPostAddress,
    JobPosting,
    ResumeSnippet,
)


class AddressExtractParseTests(TestCase):
    def setUp(self):
        self.x = AddressExtract("posting")

    def test_parses_known_fields(self):
        raw = (
            "company: Acme GmbH\n"
            "contact_name: Jane Doe\n"
            "email: jobs@acme.com\n"
            "title: Backend Engineer\n"
            "language: de"
        )
        out = self.x._parse(raw)
        self.assertEqual(out["company"], "Acme GmbH")
        self.assertEqual(out["email"], "jobs@acme.com")
        self.assertEqual(out["language"], "de")

    def test_skips_unknown_blank_and_placeholder(self):
        raw = "company: Acme\nfoo: bar\ncity:\nemail: none\nphone: n/a"
        self.assertEqual(self.x._parse(raw), {"company": "Acme"})

    def test_tolerates_surrounding_prose(self):
        raw = "Here are the details:\ncompany: Acme\nThanks!"
        self.assertEqual(self.x._parse(raw), {"company": "Acme"})

    def test_extract_empty_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(AddressExtract("p").extract(), {})


class SnippetSelectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("snipuser")
        cls.domain = Domain.objects.create(user=cls.user, name="Backend")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        cls.job.domains.add(cls.domain)
        cls.other_job = Job.objects.create(
            user=cls.user, title="Old", company="Y", started=date(2010, 1, 1)
        )
        K = ResumeSnippet.Kind
        cls.intro = ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="Hi", kind=K.intro
        )
        cls.closing = ResumeSnippet.objects.create(
            user=cls.user, title="Bye", content="Thanks", kind=K.closing
        )
        cls.body_kept = ResumeSnippet.objects.create(
            user=cls.user, title="Achv", content="Did X", kind=K.achievement, job=cls.job
        )
        cls.body_other = ResumeSnippet.objects.create(
            user=cls.user, title="Other", content="Did Y", kind=K.achievement,
            job=cls.other_job,
        )
        cls.inactive_intro = ResumeSnippet.objects.create(
            user=cls.user, title="Off", content="z", kind=K.intro, is_active=False
        )

    def _cv(self):
        cv = CV(user_pk=self.user.pk)
        cv.entries = {
            "jobs": [self.job], "projects": [], "skills": [],
            "educations": [], "certifications": [], "languages": [],
        }
        return cv

    def test_picks_one_intro_one_closing(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertEqual(sel["intro"], self.intro)
        self.assertEqual(sel["closing"], self.closing)

    def test_body_includes_kept_job_snippet_only(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertIn(self.body_kept, sel["body"])
        self.assertNotIn(self.body_other, sel["body"])

    def test_ordered_runs_intro_first_closing_last(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertEqual(sel["ordered"][0], self.intro)
        self.assertEqual(sel["ordered"][-1], self.closing)

    def test_inactive_snippet_excluded(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertNotEqual(sel["intro"], self.inactive_intro)


class CoverLetterBuildTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "cluser", email="me@example.com", first_name="Ada", last_name="Lovelace"
        )
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user, title="Achv", content="Shipped Y.", kind=K.achievement,
            job=cls.job,
        )
        ResumeSnippet.objects.create(
            user=cls.user, title="Bye", content="Thanks.", kind=K.closing
        )

    def _cv(self):
        cv = CV(user_pk=self.user.pk)
        cv.entries = {
            "jobs": [self.job], "projects": [], "skills": [],
            "educations": [], "certifications": [], "languages": [],
        }
        return cv

    def _jp(self, language="en", title="Backend Engineer"):
        return JobPosting(
            user=self.user, title=title, posting_text="We need a dev.", language=language
        )

    def test_build_uses_woven_body(self):
        with patch("jac.llm_prompts.complete", return_value="Woven letter body."):
            r = CoverLetter(
                self.user, self._jp(), self._cv(),
                address=JobPostAddress(company="Acme"),
            ).build()
        self.assertEqual(r["body"], "Woven letter body.")
        self.assertIn("Woven letter body.", r["text"])
        self.assertIn("Acme", r["text"])
        self.assertEqual(r["subject"], "Application for Backend Engineer")

    def test_falls_back_to_raw_snippets_when_llm_empty(self):
        with patch("jac.llm_prompts.complete", return_value=""):
            r = CoverLetter(
                self.user, self._jp(), self._cv(), address=JobPostAddress()
            ).build()
        self.assertIn("I build things.", r["body"])
        self.assertIn("Thanks.", r["body"])

    def test_salutation_named_when_contact_present(self):
        with patch("jac.llm_prompts.complete", return_value="x"):
            r = CoverLetter(
                self.user, self._jp(), self._cv(),
                address=JobPostAddress(contact_name="Jane Doe"),
            ).build()
        self.assertEqual(r["salutation"], "Dear Jane Doe,")

    def test_german_subject_and_generic_salutation(self):
        with patch("jac.llm_prompts.complete", return_value="x"):
            r = CoverLetter(
                self.user, self._jp(language="de"), self._cv(),
                address=JobPostAddress(),
            ).build()
        self.assertEqual(r["subject"], "Bewerbung als Backend Engineer")
        self.assertEqual(r["salutation"], "Sehr geehrte Damen und Herren,")
```

> The build tests patch `jac.llm_prompts.complete` (the symbol `CoverLetterWriter` actually
> calls), and pass **unsaved** `JobPosting` / `JobPostAddress` instances — `CoverLetter` only
> reads their attributes, so nothing hits the DB for them. `SnippetSelector` does query the DB,
> hence the real `ResumeSnippet` rows.

---

## Verification

1. **Migrate.**
   ```
   cd backend
   python manage.py makemigrations spa jac
   python manage.py migrate
   ```
   Expect two new migrations: `spa` (5 address fields on `UserProfile`) and `jac`
   (`JobPosting` + `JobPostAddress`).

2. **Run the tests.**
   ```
   python manage.py test jac
   ```
   The four new classes should pass alongside the existing suite.

3. **Seed data** (admin, http://localhost:8000/admin/):
   - On your `UserProfile`: fill in street / zip / city / country.
   - Create a few `ResumeSnippet`s: at least one `intro`, one `closing`, and an `achievement`
     linked to a `Job` you know your CV filter keeps for the test posting.

4. **Run the pipeline** against a posting (any of the files you used for `cv_eval`):
   ```
   python manage.py cover_letter_test --user <pk> --job-file data/test_job.md
   ```
   Expect: a line per posting reporting snippet count + extracted company, and a
   `data/cover_letters/<timestamp>/default__<slug>.cover.md` artifact. Open it — it should
   read as a real letter: your sender block, the recipient block (whatever the model pulled
   from the posting), date, subject, salutation, the woven body, sign-off.

5. **Check the grade knob.** Re-run with `--grade strong` (or `--llm <a-bigger-model>`):
   ```
   python manage.py cover_letter_test --user <pk> --job-file data/test_job.md --grade strong
   ```
   The body should be more polished but make the *same* claims as the snippets — no invented
   facts. With Ollama `llama3.2:1b` and `--grade light`, expect closer-to-verbatim stitching.

6. **Address extraction sanity.** Use a posting that actually contains a company address /
   email. Confirm the recipient block in the artifact reflects it. Add `--persist` and verify a
   `JobPosting` + inline `JobPostAddress` appear in admin.

7. **Slop guard.** Temporarily point `--llm` at a misconfigured alias (or stop Ollama) and
   re-run: the body should fall back to the raw snippet contents stitched with blank lines —
   never empty, never hallucinated.

"done" = tests green, a real-looking letter artifact for a seeded posting, the grade knob
visibly changes prose polish without changing claims, and the LLM-down path degrades to raw
boilerplate.
