"""CV builder: loads a user's career DB entries and applies deterministic or LLM-based
filtering and ranking to produce a job-tailored CV snapshot.

Stop-word sets live in stopwords.py to keep this module focused on pipeline logic.
"""

import logging
import re
import time
from datetime import date

from django.db.models import Q

from jac import llm as jac_llm
from jac.models import Certification, Education, Job, Language, Project, Skill
from jac.stopwords import get_stopwords

logger = logging.getLogger(__name__)


class CV:
    """In-memory CV snapshot for a single user.

    Loads all matching career entries from the DB on construction, then lets
    callers narrow the snapshot progressively via filter/rank methods. Every
    mutating method (deterministic_filter, ai_filter_entries, ai_rank_entries,
    agentic_tailor) operates on self.entries in-place.

    Constructor kwargs allow pre-filtering by domain, date window, and minimum
    skill proficiency before any pipeline steps run.
    """

    PROFICIENCY_ORDER = ["beginner", "intermediate", "advanced", "expert"]

    # Minimum entries to retain per section after threshold filtering. If
    # fewer survive, fall back to top-K from the ranked list. None means
    # "keep above-threshold; if none, keep the single top-ranked entry".
    _MIN_PER_SECTION = {
        "skills": 5,
        "jobs": 3,
        "educations": 2,
        "certifications": None,
        "projects": None,
        "languages": None,
    }

    def __init__(
        self,
        user_pk: int,
        domains: list[str] | None = None,
        started: date | None = None,
        ended: date | None = None,
        min_skill_proficiency: str | None = None,
    ):
        """Load career entries for `user_pk`.

        Args:
            user_pk: Primary key of the owning user.
            domains: If set, restrict to entries tagged with any of these domain names.
            started: Exclude jobs/projects/educations that ended before this date.
            ended: Exclude jobs/projects/educations that started after this date.
            min_skill_proficiency: One of PROFICIENCY_ORDER; filters out skills below it.
        """
        self.user = user_pk
        self.domains = domains
        self.started = started
        self.ended = ended
        self.min_skill_proficiency = min_skill_proficiency
        self.entries = self.get_cv_entries()

    def get_cv_entries(self) -> dict:
        """Return a fresh {section: [model instances]} dict from the DB."""
        return {
            "skills": self._get_skills(),
            "jobs": self._get_jobs(),
            "educations": self._get_educations(),
            "certifications": self._get_certifications(),
            "projects": self._get_projects(),
            "languages": self._get_languages(),
        }

    def _get_skills(self) -> list[Skill]:
        qs = (
            Skill.objects.filter(user=self.user)
            .prefetch_related("domains")
            .select_related("certification")
        )
        if self.domains:
            qs = qs.filter(domains__name__in=self.domains)
        if self.min_skill_proficiency in self.PROFICIENCY_ORDER:
            idx = self.PROFICIENCY_ORDER.index(self.min_skill_proficiency)
            qs = qs.filter(proficiency__in=self.PROFICIENCY_ORDER[idx:])
        return list(qs.distinct())

    def _get_jobs(self) -> list[Job]:
        qs = (
            Job.objects.filter(user=self.user)
            .prefetch_related("skills", "domains")
            .select_related("location")
        )
        if self.domains:
            qs = qs.filter(domains__name__in=self.domains)
        if self.started:
            # include jobs that were still active after the window start
            qs = qs.filter(Q(ended__gte=self.started) | Q(ended__isnull=True))
        if self.ended:
            qs = qs.filter(started__lte=self.ended)
        return list(qs.distinct().order_by("-started"))

    def _get_educations(self) -> list[Education]:
        qs = Education.objects.filter(user=self.user).select_related("location")
        if self.started:
            qs = qs.filter(Q(ended__gte=self.started) | Q(ended__isnull=True))
        if self.ended:
            qs = qs.filter(started__lte=self.ended)
        return list(qs.order_by("-started"))

    def _get_certifications(self) -> list[Certification]:
        return list(Certification.objects.filter(user=self.user).order_by("-issued_on"))

    def _get_projects(self) -> list[Project]:
        qs = (
            Project.objects.filter(user=self.user)
            .prefetch_related("skills", "domains")
            .select_related("location")
        )
        if self.domains:
            qs = qs.filter(domains__name__in=self.domains)
        if self.started:
            qs = qs.filter(Q(ended__gte=self.started) | Q(ended__isnull=True))
        if self.ended:
            qs = qs.filter(started__lte=self.ended)
        return list(qs.distinct().order_by("-started"))

    def _get_languages(self) -> list[Language]:
        return list(
            Language.objects.filter(user=self.user)
            .select_related("certification")
            .order_by("name")
        )

    # filter

    def extract_keywords(
        self, text: str, *, loose: bool = False, language: str | None = None
    ) -> list[str]:
        """Significant tokens from text — for use as deterministic_filter input.

        Returns lowercase words of length >= 3 with stopwords removed. Sorted
        longest-first so multi-character matches are tried before fragments.

        Args:
            text: Raw job posting or any free text to tokenise.
            loose: True → keep ubiquitous job-posting filler ("team", "role",
                "experience"). Useful as a fallback when the strict pass is too sparse.
            language: ISO 639-1 code of the posting language ("en", "de", …).
                None applies stop words from all registered languages.
        """
        if not text:
            return []
        stop = get_stopwords(language, loose=loose)
        words = {
            w
            for w in re.findall(r"[\w#+]+", text.lower())
            if len(w) >= 3 and w not in stop
        }
        return sorted(words, key=len, reverse=True)

    def deterministic_filter(
        self,
        keywords_or_text: list[str] | str,
        *,
        min_kept: int = 5,
        language: str | None = None,
    ) -> list[str]:
        """Substring-match entries against keywords. Returns the keywords used.

        Pass a list[str] to filter once with no retry.
        Pass the raw job text to extract keywords automatically with strict
        stopwords; if fewer than `min_kept` entries survive, retry with loose
        stopwords. Useful so the filter works for any user's vocabulary, not
        just one where the posting happens to overlap with the CV.

        Args:
            keywords_or_text: Pre-extracted keyword list, or raw job posting text.
            min_kept: When text is passed, minimum surviving entries before the
                filter retries with loose stop words.
            language: ISO 639-1 code forwarded to extract_keywords when text is
                passed ("en", "de", …). None uses all registered languages.
        """
        if isinstance(keywords_or_text, str):
            text = keywords_or_text
            snapshot = {k: list(v) for k, v in self.entries.items()}
            keywords = self.extract_keywords(text, loose=False, language=language)
            self._apply_keyword_filter(keywords)
            kept = sum(len(v) for v in self.entries.values())
            total = sum(len(v) for v in snapshot.values())
            if kept < min_kept and kept < total:
                self.entries = snapshot
                keywords = self.extract_keywords(text, loose=True, language=language)
                self._apply_keyword_filter(keywords)
            return keywords

        keywords = list(keywords_or_text)
        self._apply_keyword_filter(keywords)
        return keywords

    def _apply_keyword_filter(self, keywords: list[str]) -> None:
        """Mutate self.entries to keep only entries that substring-match at least one keyword.

        Short keywords (< 5 chars) use word-boundary regex to avoid false positives
        (e.g. "sql" should not match "mysql" by accident). Long keywords use plain
        substring matching, which is faster and sufficient at that length.
        """
        if not keywords:
            return
        long_needles: list[str] = []
        short_needles: list[str] = []
        for kw in keywords:
            if not kw:
                continue
            low = kw.lower()

            (long_needles if len(low) >= 5 else short_needles).append(low)
        if not (long_needles or short_needles):
            return
        short_re = (
            re.compile(
                r"(?<!\w)(?:"
                + "|".join(re.escape(n) for n in short_needles)
                + r")(?!\w)"
            )
            if short_needles
            else None
        )

        def matches(*parts: str | None) -> bool:
            haystack = " ".join(p.lower() for p in parts if p)
            if any(needle in haystack for needle in long_needles):
                return True
            return bool(short_re and short_re.search(haystack))

        self.entries["skills"] = [
            s
            for s in self.entries["skills"]
            if matches(s.name, s.description, *(d.name for d in s.domains.all()))
        ]
        self.entries["jobs"] = [
            j
            for j in self.entries["jobs"]
            if matches(
                j.title,
                j.company,
                j.description,
                j.location.city if j.location else None,
                *(s.name for s in j.skills.all()),
                *(d.name for d in j.domains.all()),
            )
        ]
        self.entries["educations"] = [
            e
            for e in self.entries["educations"]
            if matches(
                e.institution,
                e.field_of_study,
                e.degree,
                e.description,
                e.location.city if e.location else None,
            )
        ]
        self.entries["certifications"] = [
            c
            for c in self.entries["certifications"]
            if matches(c.name, c.issuer, c.description)
        ]
        self.entries["projects"] = [
            p
            for p in self.entries["projects"]
            if matches(
                p.name,
                p.description,
                p.location.city if p.location else None,
                *(s.name for s in p.skills.all()),
                *(d.name for d in p.domains.all()),
            )
        ]
        self.entries["languages"] = [
            la for la in self.entries["languages"] if matches(la.name)
        ]

    # AI methods (single LLM call) -----------------------------------------

    def _entries_for_llm(self) -> list[dict]:
        """Flatten self.entries into [{id, type, text}, ...] for LLM scoring."""
        out: list[dict] = []

        for s in self.entries["skills"]:
            domains = ", ".join(d.name for d in s.domains.all())
            text = f"{s.name} ({s.proficiency}, {s.category})"
            if domains:
                text += f" | domains: {domains}"
            if s.description:
                text += f" — {s.description[:200]}"
            out.append({"id": f"skill:{s.pk}", "type": "skill", "text": text})

        for j in self.entries["jobs"]:
            window = f"{j.started or '?'}–{j.ended or 'present'}"
            skills = ", ".join(sk.name for sk in j.skills.all())
            text = f"{j.title} at {j.company} ({window})"
            if skills:
                text += f" | skills: {skills}"
            if j.description:
                text += f" — {j.description[:300]}"
            out.append({"id": f"job:{j.pk}", "type": "job", "text": text})

        for e in self.entries["educations"]:
            window = f"{e.started or '?'}–{e.ended or 'present'}"
            text = f"{e.degree or ''} {e.field_of_study or ''}".strip()
            text = (
                f"{text} @ {e.institution} ({window})"
                if text
                else f"{e.institution} ({window})"
            )
            if e.description:
                text += f" — {e.description[:200]}"
            out.append({"id": f"education:{e.pk}", "type": "education", "text": text})

        for c in self.entries["certifications"]:
            text = f"{c.name} — {c.issuer}"
            if c.issued_on:
                text += f" ({c.issued_on})"
            if c.description:
                text += f" — {c.description[:200]}"
            out.append(
                {"id": f"certification:{c.pk}", "type": "certification", "text": text}
            )

        for p in self.entries["projects"]:
            window = f"{p.started or '?'}–{p.ended or 'present'}"
            skills = ", ".join(sk.name for sk in p.skills.all())
            text = f"{p.name} ({window})"
            if skills:
                text += f" | skills: {skills}"
            if p.description:
                text += f" — {p.description[:300]}"
            out.append({"id": f"project:{p.pk}", "type": "project", "text": text})

        for la in self.entries["languages"]:
            out.append(
                {
                    "id": f"language:{la.pk}",
                    "type": "language",
                    "text": f"{la.name} ({la.fluency})",
                }
            )

        return out

    def _apply_scores(self, scores: list[dict]) -> dict[str, tuple[float, str]]:
        """Index scored results by id and attach attrs to model instances."""
        by_id: dict[str, tuple[float, str]] = {}
        for s in scores:
            sid = s.get("id")
            if not sid:
                continue
            try:
                score = float(s.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            by_id[sid] = (score, str(s.get("reason", "")))

        type_key = {
            "skill": "skills",
            "job": "jobs",
            "education": "educations",
            "certification": "certifications",
            "project": "projects",
            "language": "languages",
        }
        for type_name, key in type_key.items():
            for obj in self.entries[key]:
                sid = f"{type_name}:{obj.pk}"
                score, reason = by_id.get(sid, (0.0, ""))
                obj.relevance_score = score
                obj.relevance_reason = reason
        return by_id

    def _filter_with_floor(self, threshold: float) -> None:
        """Drop entries below `threshold`; if a section falls below its floor,
        fall back to that section's top-K by relevance_score from the full
        pre-threshold list. Assumes _apply_scores has already attached scores.
        Each section list is left in descending-score order.
        """
        for key, items in self.entries.items():
            ranked = sorted(
                items,
                key=lambda o: getattr(o, "relevance_score", 0.0),
                reverse=True,
            )
            above = [
                o for o in ranked if getattr(o, "relevance_score", 0.0) >= threshold
            ]
            floor = self._MIN_PER_SECTION.get(key)
            if floor is None:
                # Keep above-threshold; if none, keep the top-1 so small-but-real
                # sections (languages, certifications) don't get wiped entirely.
                self.entries[key] = above if above else ranked[:1] if ranked else []
                continue
            if len(above) >= floor:
                self.entries[key] = above
            else:
                k = min(floor, len(ranked))
                self.entries[key] = ranked[:k]

    def ai_extract_keywords(self, job_text: str, llm: str = "default") -> list[str]:
        """Free-form keyword extraction via LLM. Complements extract_keywords()."""
        logger.debug("ai_extract_keywords: calling llm=%s (%d chars)", llm, len(job_text))
        t = time.monotonic()
        result = jac_llm.extract_job_keywords(job_text, llm=llm, user=self.user)
        logger.debug(
            "ai_extract_keywords: got %d keywords in %.1fs",
            len(result),
            time.monotonic() - t,
        )
        return result

    def ai_keyword_filter(self, job_text: str, llm: str = "default") -> list[str]:
        """LLM keyword extraction → deterministic filter. SLM-friendly alternative to
        ai_filter_entries: extraction is a simple list-in/list-out task a small model
        handles well; full entry scoring in one shot is not. Returns keywords used."""
        keywords = self.ai_extract_keywords(job_text, llm=llm)
        logger.debug(
            "ai_keyword_filter: applying deterministic filter with %d keywords",
            len(keywords),
        )
        self.deterministic_filter(keywords)
        total = sum(len(v) for v in self.entries.values())
        logger.debug("ai_keyword_filter: %d entries remaining", total)
        return keywords

    def ai_filter_entries(
        self, job_text: str, threshold: float = 0.25, llm: str = "default"
    ) -> None:
        """Score entries against the job and drop those below threshold. Mutates self.entries.

        Sections that fall below their _MIN_PER_SECTION floor get the top-K
        ranked entries instead, so output is never empty when scored entries exist.
        """
        flat = self._entries_for_llm()
        if not flat:
            return
        logger.debug(
            "ai_filter_entries: scoring %d entries (llm=%s, threshold=%.2f)",
            len(flat),
            llm,
            threshold,
        )
        t = time.monotonic()
        scores = jac_llm.score_entries_for_job(job_text, flat, llm=llm, user=self.user)
        logger.debug(
            "ai_filter_entries: scored in %.1fs → filtering", time.monotonic() - t
        )
        self._apply_scores(scores)
        self._filter_with_floor(threshold)
        total = sum(len(v) for v in self.entries.values())
        logger.debug("ai_filter_entries: %d entries remaining after threshold", total)

    def ai_rank_entries(self, job_text: str, llm: str = "default") -> None:
        """Score entries, attach .relevance_score/.relevance_reason, sort each list desc. Mutates."""
        flat = self._entries_for_llm()
        if not flat:
            return
        logger.debug("ai_rank_entries: scoring %d entries (llm=%s)", len(flat), llm)
        t = time.monotonic()
        scores = jac_llm.score_entries_for_job(job_text, flat, llm=llm, user=self.user)
        logger.debug("ai_rank_entries: scored in %.1fs → sorting", time.monotonic() - t)
        self._apply_scores(scores)
        for key in self.entries:
            self.entries[key].sort(
                key=lambda o: getattr(o, "relevance_score", 0.0), reverse=True
            )

    # Agentic methods (multi-step, reasoning-model friendly) --------------

    def ai_analyze_job(self, job_text: str, llm: str = "default") -> dict:
        """Return structured analysis of the job posting."""
        logger.debug("ai_analyze_job: calling llm=%s (%d chars)", llm, len(job_text))
        t = time.monotonic()
        result = jac_llm.analyze_job(job_text, llm=llm, user=self.user)
        logger.debug("ai_analyze_job: done in %.1fs", time.monotonic() - t)
        return result

    def agentic_tailor(
        self, job_text: str, llm: str = "default", threshold: float = 0.25
    ) -> dict:
        """Full pipeline: analyze job, then filter+rank entries using the analysis.

        Returns the analysis dict; mutates self.entries (filtered + sorted, annotated).
        Sections that fall below their _MIN_PER_SECTION floor get the top-K
        ranked entries instead, so output is never empty when scored entries exist.
        """
        logger.debug("agentic_tailor: step 1/2 — analyze job (llm=%s)", llm)
        t = time.monotonic()
        analysis = jac_llm.analyze_job(job_text, llm=llm, user=self.user)
        logger.debug("agentic_tailor: job analyzed in %.1fs", time.monotonic() - t)

        flat = self._entries_for_llm()
        if not flat:
            return analysis

        logger.debug(
            "agentic_tailor: step 2/2 — score %d entries with analysis (llm=%s)",
            len(flat),
            llm,
        )
        t = time.monotonic()
        scores = jac_llm.score_entries_with_analysis(
            job_text, analysis, flat, llm=llm, user=self.user
        )
        logger.debug(
            "agentic_tailor: scored in %.1fs → filtering at threshold=%.2f",
            time.monotonic() - t,
            threshold,
        )

        self._apply_scores(scores)
        self._filter_with_floor(threshold)
        total = sum(len(v) for v in self.entries.values())
        logger.debug("agentic_tailor: done — %d entries remaining", total)
        return analysis

    def ai_conversational_tailor(
        self, job_text: str, llm: str = "default"
    ) -> list[dict]:
        """Reasoning-grade tailoring: loose prompt, selection contract, ranked output.

        Hands the model the full career database and the posting, asks it to
        return the entries that belong on the CV in descending relevance order.
        Mutates self.entries in place: only selected entries survive, ordered
        as the model returned them, with `relevance_reason` attached. Entries
        not in the selection are dropped (sections may end up empty).

        Returns the validated `[{"id", "reason"}, ...]` selection so callers
        (views, management commands) can surface the reasoning. Propagates the
        ValueError raised by `tailor_cv_conversationally` when too few valid
        ids survive — that's the fallback signal for `ai_tailor_with_fallback`.
        """
        flat = self._entries_for_llm()
        if not flat:
            return []
        logger.debug(
            "ai_conversational_tailor: tailoring %d entries (llm=%s)", len(flat), llm
        )
        t = time.monotonic()
        selection = jac_llm.tailor_cv_conversationally(
            job_text, flat, llm=llm, user=self.user
        )
        logger.debug(
            "ai_conversational_tailor: %d picks in %.1fs",
            len(selection),
            time.monotonic() - t,
        )

        type_key = {
            "skill": "skills",
            "job": "jobs",
            "education": "educations",
            "certification": "certifications",
            "project": "projects",
            "language": "languages",
        }
        by_id: dict[str, tuple[str, object]] = {}
        for type_name, key in type_key.items():
            for obj in self.entries[key]:
                by_id[f"{type_name}:{obj.pk}"] = (key, obj)

        new_entries: dict[str, list] = {k: [] for k in self.entries}
        for pick in selection:
            sid = pick["id"]
            entry = by_id.get(sid)
            if entry is None:
                continue
            key, obj = entry
            obj.relevance_reason = pick.get("reason", "")
            new_entries[key].append(obj)
        self.entries = new_entries
        return selection

    # Tiered fallback pipeline --------------------------------------------

    def ai_tailor_with_fallback(
        self,
        job_text: str,
        llm: str = "default",
        threshold: float = 0.25,
        language: str | None = None,
    ) -> dict:
        """Tier the tailoring pipeline so the user's LLM is used to its limit.

        Each rung's prompt style is matched to a model capability tier. We fall
        through on any exception (timeout, parse error, context overflow,
        hallucinated ids that fail id-validation) or on an empty result:

          1. ai_conversational_tailor — loose prompt, selection contract (reasoning model)
          2. ai_filter_entries        — strict rubric, per-entry scoring  (mid-tier LLM)
          3. ai_keyword_filter        — LLM keyword extraction + det. match (SLM)
          4. deterministic_filter     — pure keyword extraction (internal loose retry)
          5. unfiltered               — restore the original snapshot

        Each failed tier restores the pre-call snapshot so partial mutations
        don't leak into the next attempt. Returns:
          {"tier":      <which tier won>,
           "selection": [{id, reason}, ...] | None,   # tier 1 only
           "keywords":  [str, ...] | None}            # tiers 3 / 4 only
        """
        snapshot = {k: list(v) for k, v in self.entries.items()}

        def restore() -> None:
            self.entries = {k: list(v) for k, v in snapshot.items()}

        try:
            selection = self.ai_conversational_tailor(job_text, llm=llm)
            if any(self.entries.values()):
                return {
                    "tier": "conversational",
                    "selection": selection,
                    "keywords": None,
                }
        except Exception:
            logger.warning(
                "ai_tailor_with_fallback: ai_conversational_tailor failed",
                exc_info=True,
            )
        restore()

        try:
            self.ai_filter_entries(job_text, threshold=threshold, llm=llm)
            if any(self.entries.values()):
                return {"tier": "filter", "selection": None, "keywords": None}
        except Exception:
            logger.warning(
                "ai_tailor_with_fallback: ai_filter_entries failed", exc_info=True
            )
        restore()

        try:
            keywords = self.ai_keyword_filter(job_text, llm=llm)
            if any(self.entries.values()):
                return {"tier": "keyword", "selection": None, "keywords": keywords}
        except Exception:
            logger.warning(
                "ai_tailor_with_fallback: ai_keyword_filter failed", exc_info=True
            )
        restore()

        try:
            keywords = self.deterministic_filter(job_text, language=language)
            if any(self.entries.values()):
                return {
                    "tier": "deterministic",
                    "selection": None,
                    "keywords": keywords,
                }
        except Exception:
            logger.warning(
                "ai_tailor_with_fallback: deterministic_filter failed", exc_info=True
            )
        restore()

        logger.info(
            "ai_tailor_with_fallback: every tier filtered too strictly — returning unfiltered CV"
        )
        return {"tier": "unfiltered", "selection": None, "keywords": None}
