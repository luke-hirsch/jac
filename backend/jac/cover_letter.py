"""snippet selection is embedding-ranked on HirschAI
runs and structural on commercial runs (the tower must not see commercial-run data);
the writer's licence scales with mode — standard polishes, high composes (and uniquely sees the posting);
proofread (critic) + fact-check (grounding audit) always run, on the run's executor;
one shared repair pass.
"""

from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.utils import timezone
from llm_connector.executor import Executor

from jac.llm_prompts import (
    CompanyResearcher,
    CoverLetterWriter,
    FaithfulnessCheck,
)
from jac.models import Mode

logger = logging.getLogger(__name__)

# Visible placeholder
LETTER_STUB = "⚠️⚠️ THE MODEL COULD NOT WRITE THIS LETTER — regenerate before sending ⚠️⚠️"

# soft placeholder, gets deleted during render if ignored
COMPANY_STUB = (
    "⟨ add one line on why THIS company — omitted from exports until you do ⟩"
)

# fallbacks for tone and focus
DEFAULT_TONE = "neutral"
DEFAULT_FOCUS = "balanced"

# Language-keyed letter furniture. English is the fallback for any unmapped code.
_SALUTATION_NAMED = {"en": "Dear {name},", "de": "Sehr geehrte/r {name},"}
_SALUTATION_GENERIC = {
    "en": "Dear Hiring Team,",
    "de": "Sehr geehrte Damen und Herren,",
}
_SUBJECT = {"en": "Application for {title}", "de": "Bewerbung als {title}"}
_CLOSING = {"en": "Kind regards,", "de": "Mit freundlichen Grüßen,"}


def editable_body(letter: dict) -> str:
    """The sendable middle of a built letter — the composed body (the company-fit opening is now
    folded into it). Subject/salutation/date/closing/addresses live in `letter_meta`, re-assembled
    at render/export time."""
    return letter.get("body", "")


class CoverLetter:
    """Build a tailored cover letter from a filtered CV, a JobPosting and users writting dossier.

    `cv` must already be filtered (CV.filter_cv + CV.apply_selection applied). `address` may be
    passed explicitly (the test command builds transient instances); otherwise it is read from
    `job_posting.address`. `build()` returns a dict of letter parts plus a rendered `text`.
    """

    def __init__(
        self,
        user,
        job_posting,
        cv,
        executor: Executor,
        address=None,
        mode: str = Mode.standard,
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
            body,
            sources,
            grounding,
            language,
            title,
            tone,
            focus,
            personality,
            style,
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

    def _posting_text(self) -> str:
        return getattr(self.job_posting, "posting_text", "") or ""

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

    def _grounding(self, body, sources, weave_failed) -> dict:
        if weave_failed:  # the stub is not a letter — nothing to audit, and not "clean"
            return {"count": None, "claims": []}
        return FaithfulnessCheck(body, sources, executor=self.executor).critique()

    def _repair(
        self,
        body,
        sources,
        grounding,
        language,
        title,
        tone,
        focus,
        personality,
        style,
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
