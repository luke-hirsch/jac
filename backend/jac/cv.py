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

from jac.filter import CVFilter
from jac.models import (
    Certification,
    Education,
    Job,
    Language,
    Project,
    Skill,
    normalize_mode,
)

logger = logging.getLogger(__name__)


class CV:
    PROFICIENCY_ORDER = ["beginner", "intermediate", "advanced", "expert"]

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
            text += (
                f" [degree: {e.get_degree_level_display()}]"
                if e.is_degree
                else " [no degree]"
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

    def highest_degree_id(self) -> str | None:
        """Flat id of the highest COMPLETED degree in this CV's education set, or None.

        German public service grades pay on the highest formal qualification, so it belongs
        on every CV regardless of how it scores against the posting. Ties (two Masters) go to
        the most recently finished one; an unfinished study period is ordinary content the
        selection may rank and drop like anything else.
        """
        best = None
        for e in self.entries.get("educations", []):
            if not e.is_degree:
                continue
            key = (e.degree_level, e.ended or date.min)
            if best is None or key > (best.degree_level, best.ended or date.min):
                best = e
        return f"education:{best.pk}" if best else None

    def filter_cv(self, job_post_text: str, mode: str | None, executor, pinned=None):

        pins = set(pinned or ())
        top = self.highest_degree_id()
        if top:
            pins.add(top)  # type:ignore
        return CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            mode=normalize_mode(mode),
            executor=executor,
            pinned=pins,  # type:ignore
        ).output()

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
                obj.pinned = bool(item.get("pinned"))
                obj.selection_warning = item.get("warning", "")
                pruned[section].append(obj)
        self.entries = pruned
