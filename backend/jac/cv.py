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

from jac.llm_prompts import Conversational, Embed, Instruct
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

    def _flatten_entries(self) -> list[dict]:
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


class CVFilter:
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

    def output(self):
        if self.grade == "strong":
            return self.strong()
        elif self.grade == "standard":
            return self.standard()
        else:
            return self.light()

    def strong(self):
        entries = self.ai_conversational_filter()
        if not entries:
            entries = self.standard()
        return entries

    def standard(self):
        entries = self.ai_filter()
        if not entries:
            entries = self.light()
        return entries

    def light(self):
        return self.embed_filter()

    def embed_filter(self):
        # request to model vie Embed class
        llm = Embed(self.job_post_text, self.entries)
        scores = llm.ranked_entries()
        if not scores:
            return self.entries
        print(llm)

    def ai_filter(self):
        llm = Instruct
        print(llm)

    def ai_conversational_filter(self):
        llm = Conversational
        print(llm)
