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
    LetterCritic,
    ParagraphGroundingCheck,
    PersonalParagraphWriter,
    SnippetEmbed,
)
from jac.models import Mode, ResumeSnippet

logger = logging.getLogger(__name__)

# Visible placeholder when the machine can't research the company (light grade, a non-web-capable
# model, no research, or no personality). Deliberately jarring so it can't be sent by accident.
PERSONAL_STUB = "⚠️⚠️ WRITE A PERSONAL PARAGRAPH YOU LAZY PIECE OF SHIT ⚠️⚠️"

# Language-keyed letter furniture. English is the fallback for any unmapped code.
_SALUTATION_NAMED = {"en": "Dear {name},", "de": "Sehr geehrte/r {name},"}
_SALUTATION_GENERIC = {
    "en": "Dear Hiring Team,",
    "de": "Sehr geehrte Damen und Herren,",
}
_SUBJECT = {"en": "Application for {title}", "de": "Bewerbung als {title}"}
_CLOSING = {"en": "Kind regards,", "de": "Mit freundlichen Grüßen,"}


def editable_body(letter: dict) -> str:
    """The sendable middle of a built letter: personal paragraph (real or stub) first,
    then the body — the paragraph OPENS the letter (letter-quality decision,
    2026-07-12).

    This — not the fully furnished `text` — is what belongs in the editable
    `JobApplication.cover_letter`; subject/salutation/date/closing/addresses live in
    `letter_meta` and are re-assembled at render/export time.
    """
    parts = [letter.get("personal_paragraph") or "", letter.get("body", "")]
    return "\n\n".join(p for p in parts if p)


class SnippetSelector:
    """ """

    _BODY_KINDS = (
        ResumeSnippet.Kind.achievement,
        ResumeSnippet.Kind.value_statement,
        ResumeSnippet.Kind.other,
    )
    # MMR: relevance weight on the greedy body pick; (1-λ) penalises similarity to
    # what is already picked, so three tellings of one story can't sweep the top-3.
    _MMR_LAMBDA = 0.7

    def __init__(
        self,
        cv,
        user_pk: int,
        max_body: int = 3,
        posting_language: str = "en",
        *,
        posting_text: str = "",
        user=None,
        executor: Executor,
    ):
        self.cv = cv
        self.user_pk = user_pk
        self.max_body = max_body
        self._ctx = self._kept_context()
        self.lang = posting_language
        self.posting_text = posting_text
        self.user = user
        self.executor = executor

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

    def _sid(self, s: ResumeSnippet) -> str:
        return f"{s.kind}:{s.pk}"

    def _embed_scores(self, active: list) -> dict | None:
        """Embedding ranking — HirschAI runs only. A commercial run must not send
        snippet text to the tower (single-executor invariant), and commercial
        executors have no embed endpoint of their own; it degrades to the
        structural scorer instead. None -> structural."""
        if not active or not self.posting_text or not self.executor.is_hirschai:
            return None
        entries = [{"id": self._sid(s), "text": s.content} for s in active]
        try:
            ranked = SnippetEmbed(
                self.posting_text, entries, user=self.executor.user
            ).ranked_vectors()
        except Exception as exc:  # noqa: BLE001 — a letter never dies on a dead embedder
            logger.info("snippet embedding unavailable: %s", exc)
            return None
        return (
            {r["id"]: {"score": r["score"], "vec": r["vec"]} for r in ranked}
            if ranked
            else None
        )

    def _rel(self, scores: dict, s) -> float:
        e = scores.get(self._sid(s))
        return e["score"] if e else 0.0

    def _vec(self, scores: dict, s) -> list:
        e = scores.get(self._sid(s))
        return e["vec"] if e else []

    def _mmr_body(self, bodies: list, scores: dict) -> list:
        """Greedy maximal-marginal-relevance pick of the body snippets: the first pick
        is pure relevance; each next pick maximises relevance minus its worst cosine
        overlap with what is already picked. Kills the near-duplicate top-3 that made
        letters retell one experience three times. Native language stays the tiebreak."""
        pool = list(bodies)
        picked: list = []
        while pool and len(picked) < self.max_body:

            def mmr(s):
                overlap = max(
                    (
                        SnippetEmbed._cos(self._vec(scores, s), self._vec(scores, p))
                        for p in picked
                    ),
                    default=0.0,
                )
                return (
                    self._MMR_LAMBDA * self._rel(scores, s)
                    - (1 - self._MMR_LAMBDA) * overlap
                )

            best = max(pool, key=lambda s: (mmr(s), self._native(s)))
            picked.append(best)
            pool.remove(best)
        return picked

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
        # sorts ahead of an equally-relevant translated one — but never resurrects or
        # promotes an irrelevant snippet.
        scores = self._embed_scores(active)
        if scores is not None:
            ranking = "embedding"
            key = lambda s: (self._rel(scores, s), self._native(s))
            body = self._mmr_body(bodies, scores)
        else:
            ranking = "structural"
            key = lambda s: (self._score(s), self._native(s))
            scored = sorted(bodies, key=key, reverse=True)
            # The > 0 keep-gate stays on structural relevance alone.
            body = [s for s in scored if self._score(s) > 0][: self.max_body]
        intro = max(intros, key=key, default=None)
        closing = max(closings, key=key, default=None)

        ordered = [s for s in (intro, *body, closing) if s is not None]
        return {
            "intro": intro,
            "body": body,
            "closing": closing,
            "ordered": ordered,
            "ranking": ranking,
        }


class CoverLetter:
    """Build a tailored cover letter from a filtered CV, a JobPosting, and the user's snippets.

    `cv` must already be filtered (CV.filter_cv + CV.apply_selection applied). `address` may be
    passed explicitly (the test command builds transient instances); otherwise it is read from
    `job_posting.address`. `build()` returns a dict of letter parts plus a rendered `text`.
    """

    _REWRITE_TAX = {"standard": 0.20, "high": 0.60}

    _MIN_BODY_RATIO = 0.6
    _SHRINKAGE_NOTE = (
        "the body summarizes the snippets instead of writing them out — rewrite at "
        "full length, one paragraph per theme, keeping every concrete claim"
    )

    _MAX_BODY_WORDS = 320
    _OVERLENGTH_NOTE = (
        "the body is {words} words — cut it to at most {max} words so the letter fits "
        "one page: drop the weakest material, keep full sentences"
    )
    MAX_BODY_SNIPPETS = 4

    def __init__(
        self,
        user,
        job_posting,
        cv,
        *,
        address=None,
        mode: str = Mode.standard,
        executor: Executor,
        max_body_snippets: int | None = None,
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
        self.max_body_snippets = max_body_snippets or self.MAX_BODY_SNIPPETS

    def build(self) -> dict:
        language = (getattr(self.job_posting, "language", "") or "en").lower()[:2]
        title = getattr(self.job_posting, "title", "") or ""
        sel = SnippetSelector(
            cv=self.cv,
            user_pk=self.user.pk,
            max_body=self.max_body_snippets,
            posting_language=language,
            posting_text=self.job_posting,
            executor=self.executor,
        ).select()

        # Personal paragraph FIRST (letter-quality decision, 2026-07-12): it opens the
        # letter, so the writer gets it as context and can arc back to it. Only a real
        # paragraph enters a prompt — the stub is a UI artifact, not writer input.
        pp = self._personal_paragraph(language, title)
        opening = "" if pp["is_stub"] else pp["text"]

        woven = CoverLetterWriter(
            snippets=sel["ordered"],
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            mode=self.mode,
            executor=self.executor,
            posting_text=self.job_posting,
            opening_paragraph=opening,
        ).write()
        # The writer returns '' when the LLM failed OR there were no snippets to weave. Either
        # way fall back to the raw stitched snippets (no slop), and remember it for _grounding.
        weave_failed = not woven
        body = woven or "\n\n".join(s.content for s in sel["ordered"])
        body_is_ai_fallback = not sel["ordered"]

        # Strong composes freely (and sees the posting), so its audit is not optional.
        # The critic reviews prose quality on standard+strong; audit claims and critic
        # notes then share ONE repair rewrite (strong re-audits the rewrite; the
        # advisory critique is not re-run).

        grounding = self._grounding(body, sel["ordered"], weave_failed)
        critique = self._critique(body, sel["ordered"], weave_failed)
        body, grounding, critique = self._repair(
            body, sel["ordered"], grounding, critique, language, title, opening
        )

        result = {
            "language": language,
            "subject": self._subject(language, title),
            "salutation": self._salutation(language),
            "body": body,
            "sender": self._sender(),
            "recipient": self._recipient(),
            "date": timezone.localdate().isoformat(),
            "closing": _CLOSING.get(language, _CLOSING["en"]),
            "snippets_used": [f"{s.kind}:{s.pk}" for s in sel["ordered"]],
            "ai_share": self._ai_share(
                sel["ordered"],
                language,
                body_is_ai_fallback,
                personal_words=0 if pp["is_stub"] else len(pp["text"].split()),
            ),
            "snippet_provenance": {
                "native": [
                    f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language == language
                ],
                "translated": [
                    f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language != language
                ],
            },
            "grounding": grounding,
            "critique": critique,
            "snippet_ranking": sel["ranking"],
            "personal_paragraph": pp["text"],
            "personal_paragraph_is_stub": pp["is_stub"],
            "personal_paragraph_sources": pp["sources"],
            "personal_paragraph_grounding": pp["grounding"],
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
        if r.get("personal_paragraph"):
            out.append(r["personal_paragraph"])
            out.append("")
        out.append(r["body"])
        out.append("")
        out.append(_CLOSING.get(r["language"], _CLOSING["en"]))
        out.append("")
        out.append(snd["name"])
        out.append(r["closing"])
        return "\n".join(out).rstrip() + "\n"

    def _ai_share(self, snippets, language, ai_fallback, personal_words=0) -> float:
        """Fraction of the body attributable to the machine, 0.0–1.0.

        0.0  = every snippet authored in the posting language, lightly stitched.
        1.0  = no snippets (body fully AI-written) — or all snippets translated at strong grade.
        Heuristic, not exact: the writer melts snippets into prose, so we attribute by source
        provenance + a per-grade rewrite tax rather than diffing output text. A real personal
        paragraph is ~100% machine-authored, so its words count as AI in both numerator and
        denominator; a stub contributes 0 (the caller passes personal_words=0 for it).
        """
        if ai_fallback or not snippets:
            return 1.0
        tax = self._REWRITE_TAX.get(self.mode, self._REWRITE_TAX["instruct"])
        native_w = sum(
            len(s.content.split()) for s in snippets if s.language == language
        )
        trans_w = sum(
            len(s.content.split()) for s in snippets if s.language != language
        )
        total = native_w + trans_w + personal_words
        if not total:
            return 1.0
        ai_w = trans_w + tax * native_w + personal_words
        return round(ai_w / total, 2)

    def _grounding(self, body, snippets, weave_failed) -> dict:

        if (
            weave_failed
        ):  # body is the verbatim snippets -> grounded by construction, no call
            return {"count": 0, "claims": []}
        return FaithfulnessCheck(body, snippets, executor=self.executor).critique()

    def _critique(self, body, snippets, weave_failed) -> dict:
        """Advisory prose-quality review: {'count': int | None, 'claims': [str]}.

        Runs only on _CRITIC_GRADES with a real woven body (the raw-fallback body IS
        the snippets — nothing to review). Two deterministic length backstops ride the
        same channel, so they must not depend on the critic model being up: standard's
        shrinkage check (a polish that lost >40% of the snippet words is summarising)
        and the one-page ceiling on both grades."""
        if weave_failed or not snippets:
            return {"count": None, "claims": []}
        critique = LetterCritic(body, snippets, executor=self.executor).critique()
        backstops = []
        if self.mode == "instruct" and self._shrunk(body, snippets):
            backstops.append(self._SHRINKAGE_NOTE)
        if self._overlong(body):
            backstops.append(
                self._OVERLENGTH_NOTE.format(
                    words=len(body.split()), max=self._MAX_BODY_WORDS
                )
            )
        if backstops:
            claims = critique["claims"] + backstops
            critique = {"count": len(claims), "claims": claims}
        return critique

    def _shrunk(self, body, snippets) -> bool:
        snippet_words = sum(len(s.content.split()) for s in snippets)
        return snippet_words > 0 and len(body.split()) < (
            self._MIN_BODY_RATIO * snippet_words
        )

    def _overlong(self, body) -> bool:
        return len(body.split()) > self._MAX_BODY_WORDS

    def _repair(self, body, snippets, grounding, critique, language, title, opening):
        """ONE combined repair pass over draft one, never a loop: strong's unsupported
        claims and any critique notes ride the same rewrite. Afterwards the grounding
        is re-audited when auditing is on (safety stays honest about the shipped body);
        the critique is NOT re-run — advisory — its `repaired` flag means "the flagged
        draft was replaced", set only when the critique itself contributed notes.
        `grounding.repaired` keeps its v2 contract on conversational: True only when a rewrite
        actually replaced the body. `opening` travels along so the rewrite keeps the
        same arc context as draft one."""
        conversational = self.mode == "conversational"
        claims = (grounding.get("claims") or []) if conversational else []
        notes = critique.get("claims") or []
        if not claims and not notes:
            if conversational:
                return body, {**grounding, "repaired": False}, critique
            return body, grounding, critique
        rewritten = CoverLetterWriter(
            snippets=snippets,
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            mode=self.mode,
            executor=self.executor,
            posting_text=self.job_posting,
            unsupported_claims=claims,
            revision_notes=notes,
            opening_paragraph=opening,
        ).write()
        if not rewritten:
            out_g = {**grounding, "repaired": False} if conversational else grounding
            out_c = {**critique, "repaired": False} if notes else critique
            return body, out_g, out_c
        new_g = self._grounding(rewritten, snippets, weave_failed=False)
        if conversational:
            new_g = {**new_g, "repaired": True}
        out_c = {**critique, "repaired": True} if notes else critique
        return rewritten, new_g, out_c

    # --- personal paragraph (company research × personality) ----------------------------

    def _stub(self) -> dict:
        return {
            "text": PERSONAL_STUB,
            "is_stub": True,
            "sources": [],
            "grounding": {"count": None, "claims": []},
        }

    def _personal_paragraph(self, language, title) -> dict:
        """Real-or-stub opening paragraph. Real ONLY when the run's executor can
        web-search (commercial) AND a personality dossier exists; every HirschAI
        run stubs — loudly, never silently (the stub keeps the export blocker
        armed). Research, write, and audit all run on the run's executor."""
        if not self.executor.supports_web_search:
            return self._stub()
        personality = self._personality_dossier()
        if not personality:
            return self._stub()
        company = self._recipient()["company"]
        research = CompanyResearcher(
            company, self._posting_text(), executor=self.executor, language=language
        ).research()
        if not research["ok"]:
            return self._stub()
        text = PersonalParagraphWriter(
            posting_text=self._posting_text(),
            title=title,
            language=language,
            company_dossier=research["dossier"],
            personality_dossier=personality,
            executor=self.executor,
        ).write()
        if not text:
            return self._stub()
        grounding = ParagraphGroundingCheck(
            text, research["dossier"], personality, executor=self.executor
        ).critique()
        return {
            "text": text,
            "is_stub": False,
            "sources": research["sources"],
            "grounding": grounding,
        }

    def _personality_dossier(self) -> str:
        try:
            from spa.models import PersonalityProfile

            prof = PersonalityProfile.objects.filter(user=self.user).first()
        except Exception:
            return ""
        if not prof or not prof.has_answers():
            return ""
        return prof.ensure_dossier(executor=self.executor)
