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
_SALUTATION_GENERIC = {
    "en": "Dear Hiring Team,",
    "de": "Sehr geehrte Damen und Herren,",
}
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
        if s.job and s.job.pk and s.job.pk in self._ctx["jobs"]:
            sc += 10
        if s.project and s.project.pk and s.project.pk in self._ctx["projects"]:
            sc += 10
        sc += 2 * len({d.pk for d in s.domains.all()} & self._ctx["domains"])
        sc += 1 * len({sk.pk for sk in s.skills.all()} & self._ctx["skills"])
        return sc

    def select(self) -> dict:
        active = list(
            ResumeSnippet.objects.filter(
                user=self.user_pk, is_active=True
            ).prefetch_related("domains", "skills")
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
        if (
            not body
        ):  # LLM failed / no model — fall back to the raw boilerplate, no slop.
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
