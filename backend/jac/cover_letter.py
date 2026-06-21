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

from jac.llm_prompts import CoverLetterWriter, FaithfulnessCheck
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

    def __init__(
        self, cv, user_pk: int, max_body: int = 4, posting_language: str = "en"
    ):
        self.cv = cv
        self.user_pk = user_pk
        self.max_body = max_body
        self._ctx = self._kept_context()
        self.lang = posting_language

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

    def _native(self, s) -> bool:
        return getattr(s, "language", "en") == self.lang

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

        # Relevance dominates; posting-language breaks ties so an already-native snippet
        # sorts ahead of an equally-relevant translated one. The > 0 keep-gate stays on
        # relevance alone, so the tie-break reorders but never resurrects a 0-score snippet.
        intro = max(
            intros, key=lambda s: (self._score(s), self._native(s)), default=None
        )
        closing = max(
            closings, key=lambda s: (self._score(s), self._native(s)), default=None
        )
        scored = sorted(
            bodies, key=lambda s: (self._score(s), self._native(s)), reverse=True
        )
        body = [s for s in scored if self._score(s) > 0][: self.max_body]

        ordered = [s for s in (intro, *body, closing) if s is not None]
        return {"intro": intro, "body": body, "closing": closing, "ordered": ordered}


class CoverLetter:
    """Build a tailored cover letter from a filtered CV, a JobPosting, and the user's snippets.

    `cv` must already be filtered (CV.filter_cv + CV.apply_selection applied). `address` may be
    passed explicitly (the test command builds transient instances); otherwise it is read from
    `job_posting.address`. `build()` returns a dict of letter parts plus a rendered `text`.
    """

    _REWRITE_TAX = {"light": 0.05, "standard": 0.20, "strong": 0.45}

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

    def build(self) -> dict:
        language = (getattr(self.job_posting, "language", "") or "en").lower()[:2]
        title = getattr(self.job_posting, "title", "") or ""
        sel = SnippetSelector(
            self.cv,
            self.user.pk,
            max_body=self.max_body_snippets,
            posting_language=language,
        ).select()

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

    # How much the writer reshapes even same-language prose, by grade. Native words are

    def _ai_share(self, snippets, language, ai_fallback) -> float:
        """Fraction of the body attributable to the machine, 0.0–1.0.

        0.0  = every snippet authored in the posting language, lightly stitched.
        1.0  = no snippets (body fully AI-written) — or all snippets translated at strong grade.
        Heuristic, not exact: the writer melts snippets into prose, so we attribute by source
        provenance + a per-grade rewrite tax rather than diffing output text.
        """
        if ai_fallback or not snippets:
            return 1.0
        tax = self._REWRITE_TAX.get(self.grade, self._REWRITE_TAX["standard"])
        native_w = sum(
            len(s.content.split()) for s in snippets if s.language == language
        )
        trans_w = sum(
            len(s.content.split()) for s in snippets if s.language != language
        )
        total = native_w + trans_w
        if not total:
            return 1.0
        ai_w = trans_w + tax * native_w
        return round(ai_w / total, 2)

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
        if (
            weave_failed
        ):  # body is the verbatim snippets -> grounded by construction, no call
            return {"count": 0, "claims": []}
        return FaithfulnessCheck(
            body,
            snippets,
            alias=self.verifier_alias or self.alias,
            user=self.user,
        ).critique()
