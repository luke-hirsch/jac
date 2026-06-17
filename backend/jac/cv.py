"""CV builder: loads a user's career DB entries and applies deterministic or LLM-based
filtering and ranking to produce a job-tailored CV snapshot.

Stop-word sets live in stopwords.py to keep this module focused on pipeline logic.
"""

import logging

# import re
# import time
from datetime import date

# from typing import Any
from django.db.models import Q

from jac.llm_prompts import Embed
from jac.models import Certification, Education, Job, Language, Project, Skill

logger = logging.getLogger(__name__)


class CV:
    PROFICIENCY_ORDER = ["beginner", "intermediate", "advanced", "expert"]
    FILTER_GRADE = ["strong", "standard", "light"]

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
        filter_grade: str = "light",
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
        if filter_grade in self.FILTER_GRADE:
            self.filter_grade = filter_grade
        else:
            self.filter_grade = "light"

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
            .prefetch_related("skills", "domains", "projects")
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
        qs = (
            Education.objects.filter(user=self.user)
            .prefetch_related("skills")
            .select_related("location")
        )
        if self.started:
            qs = qs.filter(Q(ended__gte=self.started) | Q(ended__isnull=True))
        if self.ended:
            qs = qs.filter(started__lte=self.ended)
        return list(qs.order_by("-started"))

    def _get_certifications(self) -> list[Certification]:
        return list(
            Certification.objects.filter(user=self.user)
            .prefetch_related("skills")
            .order_by("-issued_on")
        )

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

    def _flatten_entries(self) -> list[dict]:
        """Flatten self.entries into [{id, type, text, refs}, ...] for LLM scoring.

        `refs` holds the ids of related entries (via FK / M2M) that are also present in this
        flattened set. The selection layer uses them to propagate relevance across the graph.
        """
        out: list[dict] = []

        for s in self.entries["skills"]:
            domains = ", ".join(d.name for d in s.domains.all())
            text = f"{s.name} ({s.proficiency}, {s.category})"
            if domains:
                text += f" | domains: {domains}"
            if s.description:
                text += f" — {s.description[:200]}"
            refs = []
            if s.certification_id:
                refs.append(f"certification:{s.certification_id}")
            out.append(
                {
                    "id": f"skill:{s.pk}",
                    "type": "skill",
                    "text": text,
                    "refs": refs,
                    "favourite": s.favourite,
                }
            )

        for j in self.entries["jobs"]:
            window = f"{j.started or '?'}–{j.ended or 'present'}"
            skills = ", ".join(sk.name for sk in j.skills.all())
            text = f"{j.title} at {j.company} ({window})"
            if skills:
                text += f" | skills: {skills}"
            if j.description:
                text += f" — {j.description[:300]}"
            refs = [f"skill:{sk.pk}" for sk in j.skills.all()]
            refs += [f"project:{p.pk}" for p in j.projects.all()]
            out.append(
                {
                    "id": f"job:{j.pk}",
                    "type": "job",
                    "text": text,
                    "refs": refs,
                    "favourite": j.favourite,
                }
            )

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
            refs = [f"skill:{sk.pk}" for sk in e.skills.all()]
            out.append(
                {
                    "id": f"education:{e.pk}",
                    "type": "education",
                    "text": text,
                    "refs": refs,
                    "favourite": e.favourite,
                }
            )

        for c in self.entries["certifications"]:
            text = f"{c.name} — {c.issuer}"
            if c.issued_on:
                text += f" ({c.issued_on})"
            if c.description:
                text += f" — {c.description[:200]}"
            refs = [f"skill:{sk.pk}" for sk in c.skills.all()]
            out.append(
                {
                    "id": f"certification:{c.pk}",
                    "type": "certification",
                    "text": text,
                    "refs": refs,
                    "favourite": c.favourite,
                }
            )

        for p in self.entries["projects"]:
            window = f"{p.started or '?'}–{p.ended or 'present'}"
            skills = ", ".join(sk.name for sk in p.skills.all())
            text = f"{p.name} ({window})"
            if skills:
                text += f" | skills: {skills}"
            if p.description:
                text += f" — {p.description[:300]}"
            refs = [f"skill:{sk.pk}" for sk in p.skills.all()]
            if p.job_id:
                refs.append(f"job:{p.job_id}")
            out.append(
                {
                    "id": f"project:{p.pk}",
                    "type": "project",
                    "text": text,
                    "refs": refs,
                    "favourite": p.favourite,
                }
            )

        for la in self.entries["languages"]:
            refs = []
            if la.certification_id:
                refs.append(f"certification:{la.certification_id}")
            out.append(
                {
                    "id": f"language:{la.pk}",
                    "type": "language",
                    "text": f"{la.name} ({la.fluency})",
                    "refs": refs,
                    "favourite": la.favourite,
                }
            )

        # Prune refs to ids that actually exist in this set (domain/date/proficiency
        # filters may have dropped a referenced entry) and drop self-references.
        valid = {e["id"] for e in out}
        for e in out:
            e["refs"] = [r for r in e["refs"] if r in valid and r != e["id"]]

        return out

    # filter
    def filter_cv(self, job_post_text: str, grade: str | None):
        cv_filter = CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            grade=grade
            if grade and grade in ["light", "standard", "strong"]
            else "light",
            user=self.user,
        )
        return cv_filter.output()

    def apply_selection(self, selection: dict) -> None:
        """Prune self.entries to the entries chosen by CVFilter, in ranked order.

        `selection` is CVFilter.output(): {type: [{id, score, ...}, ...]} where `type` is the
        singular entry type ("job", "skill", …) and each list is already ranked descending.

        Each surviving model instance gets a `relevance_score` attribute for downstream rendering
        / inspection. Sections absent from `selection` are emptied. self.entries section keys are
        the plural form ("jobs", "skills", …); the flat ids are "<singular>:<pk>".
        """
        by_id = {
            f"{section[:-1]}:{obj.pk}": obj
            for section, items in self.entries.items()
            for obj in items
        }
        pruned = {section: [] for section in self.entries}
        for ftype, chosen in selection.items():
            section = f"{ftype}s"
            if section not in pruned:
                continue
            for item in chosen:
                obj = by_id.get(item.get("id"))
                if obj is None:
                    continue
                obj.relevance_score = item.get("score")
                pruned[section].append(obj)
        self.entries = pruned


class CVFilter:
    """Turns per-entry relevance scores into a ranked, weakly-filtered CV.

    Scoring is pluggable (embeddings / instruct LLM / conversational LLM); everything below the
    score map — directional propagation over entry edges, then per-section drop — is shared.
    """

    # Tier: a node is lifted only by neighbours of a strictly lower tier number.
    _TIER = {
        "job": 0,
        "project": 0,
        "education": 0,
        "skill": 1,
        "certification": 2,
        "language": 3,
    }
    # Damping applied to an anchor's score when it lifts a lower-tier neighbour.
    _ANCHOR_W = 0.85

    # Additive nudge for user-flagged favourites, applied to the effective score after
    # propagation. Kept below the smallest non-zero section floor (education's 0.15) so a
    # favourite the scorer rates ~0 still can't cross its drop threshold — favourites tilt
    # close calls, they don't resurrect irrelevant entries.
    _FAVOURITE_BONUS = 0.05

    # Per-section drop rule. `drop_below`: absolute effective-score floor (cosine-scaled).
    # `min_keep`: always keep at least this many top-ranked, even below the floor;
    # None = never drop any; 0 = no floor guarantee (section may empty out if irrelevant).
    _SECTION_POLICY = {
        "job": {"drop_below": 0.20, "min_keep": 3},
        "education": {"drop_below": 0.15, "min_keep": 2},
        "skill": {"drop_below": 0.35, "min_keep": 5},
        "project": {"drop_below": 0.30, "min_keep": 0},
        "certification": {"drop_below": 0.30, "min_keep": 0},
        "language": {"drop_below": 0.00, "min_keep": None},
    }

    def __init__(
        self,
        job_post_text: str,
        entries: list[dict],
        grade: str = "light",
        user=None,
    ):
        assert isinstance(job_post_text, str)
        self.job_post_text = job_post_text
        self.entries = entries
        self.grade = grade
        self.user = user

    def output(self) -> dict:
        """Return {section: [entry dicts + score], ...}, each section ranked desc."""
        if self.grade == "strong":
            base = (
                self._strong_scores() or self._standard_scores() or self._light_scores()
            )
        elif self.grade == "standard":
            base = self._standard_scores() or self._light_scores()
        else:
            base = self._light_scores()
        return self._select(base)

    # --- score sources (each returns {id: float} or {} on failure) ---------------------

    def _light_scores(self) -> dict:
        ranked = Embed(self.job_post_text, self.entries).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}

    def _standard_scores(self) -> dict:
        # TODO: Instruct LLM ranking. Returns {} until implemented -> falls back to light.
        return {}

    def _strong_scores(self) -> dict:
        # TODO: Conversational LLM ranking. Returns {} until implemented -> falls back.
        return {}

    # --- shared selection layer --------------------------------------------------------

    def _propagate(self, base: dict) -> dict:
        """Single ascending-tier sweep: lift each node by its best higher-tier neighbour."""
        eff = {e["id"]: base.get(e["id"], 0.0) for e in self.entries}
        type_of = {e["id"]: e["type"] for e in self.entries}

        adj: dict[str, set[str]] = {}
        for e in self.entries:
            for r in e.get("refs", []):
                adj.setdefault(e["id"], set()).add(r)
                adj.setdefault(r, set()).add(e["id"])

        for tier in (1, 2, 3):
            for e in self.entries:
                if self._TIER.get(e["type"]) != tier:
                    continue
                eid = e["id"]
                higher = [
                    eff[n]
                    for n in adj.get(eid, ())
                    if self._TIER.get(type_of.get(n), 99) < tier
                ]
                if higher:
                    eff[eid] = max(eff[eid], self._ANCHOR_W * max(higher))
        return eff

    def _select(self, base: dict) -> dict:
        """Apply propagation + per-section drop. Empty base -> keep everything unscored."""
        if not base:
            return self._group_all()

        eff = self._propagate(base)

        # Favourite nudge: small, post-propagation, so it tilts close calls without
        # lifting a ~0-scored entry over its section floor (see _FAVOURITE_BONUS).
        for e in self.entries:
            if e.get("favourite"):
                eid = e["id"]
                eff[eid] = eff.get(eid, 0.0) + self._FAVOURITE_BONUS

        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(
                section, {"drop_below": 0.0, "min_keep": 0}
            )
            items.sort(key=lambda e: eff.get(e["id"], 0.0), reverse=True)

            min_keep = policy["min_keep"]
            if min_keep is None:
                keep = items
            else:
                keep = [
                    e for e in items if eff.get(e["id"], 0.0) >= policy["drop_below"]
                ]
                if len(keep) < min_keep:
                    keep = items[:min_keep]

            out[section] = [
                {**e, "score": round(eff.get(e["id"], 0.0), 4)} for e in keep
            ]
        return out

    def _group_all(self) -> dict:
        """Fallback when scoring fails: every entry kept, score 0.0."""
        out: dict[str, list[dict]] = {}
        for e in self.entries:
            out.setdefault(e["type"], []).append({**e, "score": 0.0})
        return out
