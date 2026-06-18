import io
import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from jac.cv import CV
from jac.filter import CVFilter
from jac.llm_prompts import Conversational, Embed, Instruct
from jac.management.commands.cv_eval import _resolve_runs
from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    ResumeSnippet,
    Skill,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class DomainModelTests(TestCase):
    def test_str_returns_name(self):
        user = User.objects.create(username="lukas")
        self.assertEqual(
            str(Domain.objects.create(user=user, name="Fintech")), "Fintech"
        )


class SkillYearsOfExperienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="alice")

    def test_returns_none_when_no_dates_available(self):
        skill = Skill.objects.create(user=self.user, name="Python")
        self.assertIsNone(skill.years_of_experience)

    def test_uses_first_used_when_only_date(self):
        skill = Skill.objects.create(
            user=self.user, name="Python", first_used=date(2015, 1, 1)
        )
        # At least 10 years between 2015-01-01 and 2026-05-27.
        self.assertIsNotNone(skill.years_of_experience)
        if skill.years_of_experience is not None:
            self.assertGreaterEqual(int(skill.years_of_experience), 10)

    def test_picks_earliest_across_jobs_and_projects(self):
        skill = Skill.objects.create(
            user=self.user, name="Python", first_used=date(2020, 1, 1)
        )
        job = Job.objects.create(
            user=self.user,
            title="Eng",
            company="Acme",
            started=date(2012, 6, 1),
        )
        job.skills.add(skill)
        project = Project.objects.create(
            user=self.user, name="Side", started=date(2018, 1, 1)
        )
        project.skills.add(skill)
        # Refetch so SkillManager attaches the earliest-job/project annotations.
        skill = Skill.objects.get(pk=skill.pk)
        # Earliest is the 2012 job.
        self.assertGreaterEqual(int(skill.years_of_experience), 13)


# ---------------------------------------------------------------------------
# CV query/filter tests
# ---------------------------------------------------------------------------


class CVQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        cls.other = User.objects.create(username="someone_else")

        cls.dom_python = Domain.objects.create(user=cls.user, name="Python")
        cls.dom_data = Domain.objects.create(user=cls.user, name="Data")
        cls.dom_unused = Domain.objects.create(user=cls.user, name="Robotics")

        cls.location = Location.objects.create(user=cls.user, city="Berlin")

        # Skills — for self.user
        cls.skill_py = Skill.objects.create(
            user=cls.user, name="Python", proficiency=Skill.Proficiency_Choices.expert
        )
        cls.skill_py.domains.add(cls.dom_python)

        cls.skill_sql = Skill.objects.create(
            user=cls.user,
            name="SQL",
            proficiency=Skill.Proficiency_Choices.intermediate,
        )
        cls.skill_sql.domains.add(cls.dom_data)

        cls.skill_basic = Skill.objects.create(
            user=cls.user,
            name="Excel",
            proficiency=Skill.Proficiency_Choices.beginner,
        )

        # Skill belonging to a different user — must never leak in
        cls.skill_other = Skill.objects.create(user=cls.other, name="Rust")

        # Jobs
        cls.job_recent = Job.objects.create(
            user=cls.user,
            title="Senior Engineer",
            company="Acme",
            started=date(2022, 1, 1),
        )
        cls.job_recent.skills.add(cls.skill_py)
        cls.job_recent.domains.add(cls.dom_python)

        cls.job_old = Job.objects.create(
            user=cls.user,
            title="Analyst",
            company="OldCo",
            started=date(2010, 1, 1),
            ended=date(2012, 12, 31),
        )
        cls.job_old.domains.add(cls.dom_data)

        # Education
        cls.edu = Education.objects.create(
            user=cls.user,
            institution="TU Berlin",
            field_of_study="Computer Science",
            degree="MSc",
            started=date(2014, 9, 1),
            ended=date(2017, 7, 1),
        )

        # Certification
        cls.cert_old = Certification.objects.create(
            user=cls.user,
            name="AWS Solutions Architect",
            issuer="Amazon",
            issued_on=date(2020, 1, 1),
        )
        cls.cert_new = Certification.objects.create(
            user=cls.user,
            name="GCP Professional",
            issuer="Google",
            issued_on=date(2023, 5, 1),
        )

        # Projects
        cls.proj = Project.objects.create(
            user=cls.user,
            name="Open-source CLI",
            started=date(2021, 6, 1),
        )
        cls.proj.skills.add(cls.skill_py)
        cls.proj.domains.add(cls.dom_python)

        # Languages
        cls.lang_en = Language.objects.create(
            user=cls.user, name="English", fluency=Language.Fluency.fluent
        )
        cls.lang_de = Language.objects.create(
            user=cls.user, name="German", fluency=Language.Fluency.native
        )

    def test_skills_isolated_per_user(self):
        cv = CV(user_pk=self.user.pk)
        skill_names = {s.name for s in cv.entries["skills"]}
        self.assertEqual(skill_names, {"Python", "SQL", "Excel"})

    def test_skills_filtered_by_domain(self):
        cv = CV(user_pk=self.user.pk, domains=["Python"])
        names = {s.name for s in cv.entries["skills"]}
        self.assertEqual(names, {"Python"})

    def test_skills_filtered_by_min_proficiency(self):
        cv = CV(user_pk=self.user.pk, min_skill_proficiency="advanced")
        names = {s.name for s in cv.entries["skills"]}
        self.assertEqual(names, {"Python"})

    def test_skills_min_proficiency_invalid_value_ignored(self):
        cv = CV(user_pk=self.user.pk, min_skill_proficiency="grandmaster")
        names = {s.name for s in cv.entries["skills"]}
        self.assertEqual(names, {"Python", "SQL", "Excel"})

    def test_jobs_ordered_desc_by_start(self):
        cv = CV(user_pk=self.user.pk)
        starts = [j.started for j in cv.entries["jobs"]]
        self.assertEqual(starts, sorted(starts, reverse=True))

    def test_jobs_filtered_by_date_window(self):
        cv = CV(user_pk=self.user.pk, started=date(2015, 1, 1))
        titles = {j.title for j in cv.entries["jobs"]}
        # job_old ended 2012 — excluded; job_recent ongoing — included.
        self.assertEqual(titles, {"Senior Engineer"})

    def test_jobs_ended_clause_excludes_jobs_started_after_window(self):
        cv = CV(user_pk=self.user.pk, ended=date(2015, 1, 1))
        titles = {j.title for j in cv.entries["jobs"]}
        self.assertEqual(titles, {"Analyst"})

    def test_certifications_ordered_by_issued_desc(self):
        cv = CV(user_pk=self.user.pk)
        names = [c.name for c in cv.entries["certifications"]]
        self.assertEqual(names, ["GCP Professional", "AWS Solutions Architect"])

    def test_projects_filtered_by_domain(self):
        cv = CV(user_pk=self.user.pk, domains=["Python"])
        names = {p.name for p in cv.entries["projects"]}
        self.assertEqual(names, {"Open-source CLI"})

    def test_languages_ordered_by_name(self):
        cv = CV(user_pk=self.user.pk)
        names = [la.name for la in cv.entries["languages"]]
        self.assertEqual(names, ["English", "German"])

    def test_entries_dict_has_all_sections(self):
        cv = CV(user_pk=self.user.pk)
        self.assertEqual(
            set(cv.entries.keys()),
            {"skills", "jobs", "educations", "certifications", "projects", "languages"},
        )


class CVSelectRankedTests(TestCase):
    """CVFilter._select_ranked: keep-by-label, favourites pinned, min_keep honoured."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="standard")

    def test_keeps_relevant_drops_zero_label(self):
        # 4 jobs (min_keep 3): two rated relevant, two rated 0. min_keep forces a 3rd back.
        entries = [
            {
                "id": f"job:{i}",
                "type": "job",
                "text": "",
                "refs": [],
                "favourite": False,
            }
            for i in range(1, 5)
        ]
        labels = {"job:1": 3, "job:2": 2, "job:3": 0, "job:4": 0}
        out = self._filter(entries)._select_ranked(labels)
        kept = [e["id"] for e in out["job"]]
        # two relevant kept + one zero-rated topped up to satisfy min_keep(3); ranked desc.
        self.assertEqual(kept[:2], ["job:1", "job:2"])
        self.assertEqual(len(kept), 3)

    def test_skills_count_varies_with_fit(self):
        # 8 skills (min_keep 5): 6 rated relevant -> all 6 kept (count tracks fit, no clamp).
        entries = [
            {
                "id": f"skill:{i}",
                "type": "skill",
                "text": "",
                "refs": [],
                "favourite": False,
            }
            for i in range(1, 9)
        ]
        labels = {f"skill:{i}": (2 if i <= 6 else 0) for i in range(1, 9)}
        out = self._filter(entries)._select_ranked(labels)
        self.assertEqual(len(out["skill"]), 6)

    def test_favourite_pinned_despite_zero_label(self):
        # project min_keep 0; a 0-rated favourite is still kept (pinned), a 0-rated non-fav isn't.
        entries = [
            {
                "id": "project:1",
                "type": "project",
                "text": "",
                "refs": [],
                "favourite": True,
            },
            {
                "id": "project:2",
                "type": "project",
                "text": "",
                "refs": [],
                "favourite": False,
            },
        ]
        out = self._filter(entries)._select_ranked({"project:1": 0, "project:2": 0})
        kept = {e["id"] for e in out["project"]}
        self.assertEqual(kept, {"project:1"})

    def test_languages_never_dropped(self):
        entries = [
            {
                "id": "language:1",
                "type": "language",
                "text": "",
                "refs": [],
                "favourite": False,
            },
        ]
        out = self._filter(entries)._select_ranked({"language:1": 0})
        self.assertEqual([e["id"] for e in out["language"]], ["language:1"])

    def test_ranked_descending_by_label(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:3", "type": "job", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_ranked({"job:1": 1, "job:2": 3, "job:3": 2})
        self.assertEqual([e["id"] for e in out["job"]], ["job:2", "job:3", "job:1"])

    def test_score_is_the_label(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select_ranked({"job:1": 2})
        self.assertEqual(out["job"][0]["score"], 2)


class InstructScorerParseTests(TestCase):
    """Instruct._parse: tolerant line parsing, validating, clamping — no network."""

    def _scorer(self):
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:1", "type": "job", "text": "Dev at X"},
        ]
        return Instruct("posting", entries)

    def test_parses_clean_lines(self):
        self.assertEqual(
            self._scorer()._parse("skill:1 3\njob:1 1"),
            {"skill:1": 3, "job:1": 1},
        )

    def test_tolerates_markdown_and_separator_drift(self):
        # bullets, em-dash, colon, code fences, blank lines — all survive.
        raw = "```\n- skill:1: 2\n1. job:1 — 0\n```"
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_extracts_lines_amid_prose(self):
        raw = "Sure! Here are the ratings:\nskill:1 2\njob:1 0\nHope that helps."
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_partial_reply_keeps_complete_lines(self):
        # truncated mid-reply: skill:1 parses, the dangling line is ignored.
        self.assertEqual(self._scorer()._parse("skill:1 3\njob"), {"skill:1": 3})

    def test_unknown_ids_dropped_and_labels_clamped(self):
        raw = "skill:1 9\njob:1 0\nskill:999 2"
        # 9 -> clamped to _LABEL_MAX(3); unknown id dropped.
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 3, "job:1": 0})

    def test_parses_single_line_json(self):
        # Regression: a model that ignores the line format and emits compact one-line
        # JSON must still yield EVERY pair. The old per-line parser grabbed only the first,
        # leaving a truthy-but-near-empty label map that masked the light fallback and
        # collapsed selection to the min_keep skeleton for every posting.
        raw = '{"skill:1": 2, "job:1": 0}'
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_parses_multiple_pairs_on_one_line(self):
        self.assertEqual(
            self._scorer()._parse("skill:1 3 job:1 1"), {"skill:1": 3, "job:1": 1}
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(self._scorer()._parse("no ratings here at all"), {})

    def test_ranked_entries_empty_on_parse_failure(self):
        with patch("jac.llm_prompts.complete", return_value="garbage"):
            self.assertEqual(self._scorer().ranked_entries(), [])

    def test_ranked_entries_empty_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._scorer().ranked_entries(), [])

    def test_ranked_entries_maps_labels(self):
        with patch("jac.llm_prompts.complete", return_value="skill:1 3\njob:1 1"):
            ranked = self._scorer().ranked_entries()
        self.assertEqual(
            {r["id"]: r["score"] for r in ranked}, {"skill:1": 3, "job:1": 1}
        )


class CVFilterRoutingTests(TestCase):
    """output() picks the right scorer + selection per grade, with fallback."""

    def _entries(self):
        return [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
        ]

    def test_standard_uses_ranked_selection(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="standard")
        with patch.object(
            CVFilter, "_standard_scores", return_value={"job:1": 3, "job:2": 1}
        ):
            out = f.output()
        # ranked by label desc; scores are the labels (not cosine).
        self.assertEqual([e["id"] for e in out["job"]], ["job:1", "job:2"])
        self.assertEqual(out["job"][0]["score"], 3)

    def test_standard_falls_back_to_light_when_scorer_empty(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="standard")
        with (
            patch.object(CVFilter, "_standard_scores", return_value={}),
            patch.object(
                CVFilter, "_light_scores", return_value={"job:1": 0.9, "job:2": 0.2}
            ),
        ):
            out = f.output()
        # light path: floored selection, cosine scores preserved.
        self.assertEqual(out["job"][0]["score"], 0.9)

    def test_strong_currently_routes_through_standard_scorer(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(
            CVFilter, "_standard_scores", return_value={"job:1": 2, "job:2": 0}
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:1")


class CVSelectHolisticTests(TestCase):
    """CVFilter._select_holistic: model's selection + guardrails (favourites, min_keep, langs)."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="strong")

    def _sel(self, *ids):
        return [{"id": i, "why": f"why {i}"} for i in ids]

    def test_keeps_selected_in_order_drops_rest(self):
        # projects: min_keep 0 -> unselected are genuinely dropped.
        entries = [
            {
                "id": f"project:{i}",
                "type": "project",
                "text": "",
                "refs": [],
                "favourite": False,
            }
            for i in range(1, 4)
        ]
        out = self._filter(entries)._select_holistic(
            self._sel("project:3", "project:1")
        )
        self.assertEqual([e["id"] for e in out["project"]], ["project:3", "project:1"])

    def test_reason_carried_and_score_none(self):
        entries = [
            {
                "id": "project:1",
                "type": "project",
                "text": "",
                "refs": [],
                "favourite": False,
            },
        ]
        out = self._filter(entries)._select_holistic(self._sel("project:1"))
        self.assertEqual(out["project"][0]["reason"], "why project:1")
        self.assertIsNone(out["project"][0]["score"])

    def test_favourite_pinned_when_model_omits_it(self):
        entries = [
            {
                "id": "project:1",
                "type": "project",
                "text": "",
                "refs": [],
                "favourite": False,
            },
            {
                "id": "project:2",
                "type": "project",
                "text": "",
                "refs": [],
                "favourite": True,
            },
        ]
        out = self._filter(entries)._select_holistic(self._sel("project:1"))
        kept = {e["id"] for e in out["project"]}
        self.assertEqual(kept, {"project:1", "project:2"})

    def test_min_keep_tops_up_from_remainder(self):
        # jobs min_keep 3; model picks only 1 -> two more topped up from natural order.
        entries = [
            {
                "id": f"job:{i}",
                "type": "job",
                "text": "",
                "refs": [],
                "favourite": False,
            }
            for i in range(1, 5)
        ]
        out = self._filter(entries)._select_holistic(self._sel("job:2"))
        kept = [e["id"] for e in out["job"]]
        self.assertEqual(kept[0], "job:2")  # model's pick stays first
        self.assertEqual(len(kept), 3)  # topped up to min_keep

    def test_count_varies_with_fit_no_clamp(self):
        # skills min_keep 5; model picks 7 -> all 7 kept (never clamped to a target).
        entries = [
            {
                "id": f"skill:{i}",
                "type": "skill",
                "text": "",
                "refs": [],
                "favourite": False,
            }
            for i in range(1, 9)
        ]
        out = self._filter(entries)._select_holistic(
            self._sel(*[f"skill:{i}" for i in range(1, 8)])
        )
        self.assertEqual(len(out["skill"]), 7)

    def test_languages_never_dropped(self):
        entries = [
            {
                "id": f"language:{i}",
                "type": "language",
                "text": "",
                "refs": [],
                "favourite": False,
            }
            for i in range(1, 3)
        ]
        out = self._filter(entries)._select_holistic(self._sel("language:1"))
        self.assertEqual(
            {e["id"] for e in out["language"]}, {"language:1", "language:2"}
        )


class ConversationalSelectorTests(TestCase):
    """Conversational._parse / selection(): tolerant, validating, ordered — no network."""

    def _selector(self):
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:1", "type": "job", "text": "Dev at X"},
        ]
        return Conversational("posting", entries)

    def test_parses_ordered_selection(self):
        raw = "job:1 — core\nskill:1 — req"
        self.assertEqual(
            self._selector()._parse(raw),
            [{"id": "job:1", "why": "core"}, {"id": "skill:1", "why": "req"}],
        )

    def test_tolerates_markdown_and_extracts_amid_prose(self):
        # bullets, code fences, a reasonless pick, and an unknown id -> only valid kept.
        raw = "Here is my pick:\n```\n- skill:1\n2. skill:999 — x\n```"
        self.assertEqual(self._selector()._parse(raw), [{"id": "skill:1", "why": ""}])

    def test_dedupes_preserving_order(self):
        raw = "job:1 — a\njob:1 — b\nskill:1 — c"
        self.assertEqual(
            [s["id"] for s in self._selector()._parse(raw)], ["job:1", "skill:1"]
        )

    def test_partial_reply_keeps_complete_picks(self):
        # truncated mid-reply: job:1 parses in order, dangling line ignored.
        self.assertEqual(
            self._selector()._parse("job:1 — core\nski"),
            [{"id": "job:1", "why": "core"}],
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(self._selector()._parse("no picks here"), [])

    def test_selection_empty_on_llm_error(self):
        with patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._selector().selection(), [])


class CVFilterStrongRoutingTests(TestCase):
    """output() strong path: holistic when available, else standard, else light."""

    def _entries(self):
        return [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": False},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
        ]

    def test_strong_uses_holistic_selection(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(
            CVFilter, "_strong_selection", return_value=[{"id": "job:2", "why": "best"}]
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:2")
        self.assertEqual(out["job"][0]["reason"], "best")

    def test_strong_falls_back_to_standard(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with (
            patch.object(CVFilter, "_strong_selection", return_value=[]),
            patch.object(
                CVFilter, "_standard_scores", return_value={"job:1": 3, "job:2": 1}
            ),
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:1")
        self.assertEqual(out["job"][0]["score"], 3)  # standard labels, not holistic

    def test_strong_falls_back_to_light_when_both_empty(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with (
            patch.object(CVFilter, "_strong_selection", return_value=[]),
            patch.object(CVFilter, "_standard_scores", return_value={}),
            patch.object(
                CVFilter, "_light_scores", return_value={"job:1": 0.9, "job:2": 0.2}
            ),
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["score"], 0.9)  # cosine -> light path


# ---------------------------------------------------------------------------
# Viewset user-scoping tests
# ---------------------------------------------------------------------------


class JobViewSetScopingTests(APITestCase):
    """JobViewSet never leaks user A's rows to user B — not in list,
    retrieve, update, or delete. Tests the pattern used by every scoped jac
    viewset (all delegate scoping to get_queryset, so one representative
    viewset is sufficient).
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_jac", password="pass")
        cls.bob = User.objects.create_user(username="bob_jac", password="pass")
        cls.alice_job = Job.objects.create(
            user=cls.alice,
            title="Alice Engineer",
            company="AliceCo",
            started=date(2022, 1, 1),
        )

    def test_list_returns_only_own_jobs(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/jac/jobs/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.alice_job.pk, [row["id"] for row in r.data["results"]])

        self.client.force_login(self.bob)
        r = self.client.get("/api/jac/jobs/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)

    def test_retrieve_other_users_job_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/jac/jobs/{self.alice_job.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_patch_other_users_job_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.patch(
            f"/api/jac/jobs/{self.alice_job.pk}/", {"title": "Hacked"}, format="json"
        )
        self.assertEqual(r.status_code, 404)

    def test_delete_other_users_job_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.delete(f"/api/jac/jobs/{self.alice_job.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_unauthenticated_list_is_403(self):
        r = self.client.get("/api/jac/jobs/")
        self.assertIn(r.status_code, (401, 403))


# ---------------------------------------------------------------------------
# Phase 3a — Skill.years_of_experience_override (model-level)
# ---------------------------------------------------------------------------


class SkillYearsOverrideModelTests(TestCase):
    """The override is the escape hatch for intermittently-used skills the
    automatic recogniser over-counts: when set, the property returns it
    verbatim; when cleared, it falls back to the computed delta.
    """

    def setUp(self):
        self.user = User.objects.create(username="override_user")

    def test_property_uses_computed_delta_without_override(self):
        skill = Skill.objects.create(
            user=self.user, name="C/C++", first_used=date(2010, 1, 1)
        )
        self.assertIsNone(skill.years_of_experience_override)
        self.assertGreaterEqual(int(skill.years_of_experience), 14)

    def test_override_wins_over_computed(self):
        skill = Skill.objects.create(
            user=self.user, name="C/C++", first_used=date(2010, 1, 1)
        )
        skill.years_of_experience_override = 2
        self.assertEqual(skill.years_of_experience, 2)

    def test_clearing_override_falls_back_to_computed(self):
        skill = Skill.objects.create(
            user=self.user,
            name="C/C++",
            first_used=date(2010, 1, 1),
            years_of_experience_override=2,
        )
        self.assertEqual(skill.years_of_experience, 2)
        skill.years_of_experience_override = None
        self.assertGreaterEqual(int(skill.years_of_experience), 14)

    def test_override_of_zero_is_respected(self):
        # 0 is a legitimate override (and not None), so it must win.
        skill = Skill.objects.create(
            user=self.user,
            name="COBOL",
            first_used=date(2010, 1, 1),
            years_of_experience_override=0,
        )
        self.assertEqual(skill.years_of_experience, 0)


# ---------------------------------------------------------------------------
# Phase 3a — Skill API: override round-trip + related_skills
# ---------------------------------------------------------------------------


class SkillOverrideAPITests(APITestCase):
    """`years_of_experience_override` is writable and `years_of_experience`
    transparently reflects it; the effective field itself stays read-only.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="skill_api", password="pass")
        cls.skill = Skill.objects.create(
            user=cls.user, name="C/C++", first_used=date(2010, 1, 1)
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_patch_override_changes_effective_years(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.skill.pk}/",
            {"years_of_experience_override": 4},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["years_of_experience_override"], 4)
        self.assertEqual(r.data["years_of_experience"], 4)

    def test_clearing_override_reverts_to_computed(self):
        self.skill.years_of_experience_override = 4
        self.skill.save()
        r = self.client.patch(
            f"/api/jac/skills/{self.skill.pk}/",
            {"years_of_experience_override": None},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["years_of_experience_override"])
        self.assertGreaterEqual(int(r.data["years_of_experience"]), 14)

    def test_years_of_experience_is_read_only(self):
        # Writing the effective field is silently ignored; the stored value
        # stays computed.
        r = self.client.patch(
            f"/api/jac/skills/{self.skill.pk}/",
            {"years_of_experience": 99},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data["years_of_experience"], 99)
        self.assertIsNone(r.data["years_of_experience_override"])


class SkillRelatedSkillsAPITests(APITestCase):
    """The symmetric M2M ties skills together both ways, guards against
    self-reference, and refuses to point at another user's skill.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="rel_api", password="pass")
        cls.other = User.objects.create_user(username="rel_other", password="pass")
        cls.accounting = Skill.objects.create(user=cls.user, name="Accounting")
        cls.sevdesk = Skill.objects.create(user=cls.user, name="SevDesk")
        cls.foreign = Skill.objects.create(user=cls.other, name="Foreign")

    def setUp(self):
        self.client.force_login(self.user)

    def test_relation_is_symmetric(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.accounting.pk}/",
            {"related_skills": [self.sevdesk.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["related_skills"], [self.sevdesk.pk])

        # Symmetry: SevDesk now lists Accounting without us touching it.
        r = self.client.get(f"/api/jac/skills/{self.sevdesk.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.accounting.pk, r.data["related_skills"])

    def test_self_reference_is_rejected(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.accounting.pk}/",
            {"related_skills": [self.accounting.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_relate_to_another_users_skill(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.accounting.pk}/",
            {"related_skills": [self.foreign.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.accounting.refresh_from_db()
        self.assertEqual(self.accounting.related_skills.count(), 0)


class SkillBuildsOnAPITests(APITestCase):
    """`builds_on` is directed (unlike `related_skills`): setting it on A does
    not make B build on A; B instead lists A under the read-only `enables`.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bo_api", password="pass")
        cls.other = User.objects.create_user(username="bo_other", password="pass")
        cls.drf = Skill.objects.create(user=cls.user, name="DRF")
        cls.django = Skill.objects.create(user=cls.user, name="Django")
        cls.foreign = Skill.objects.create(user=cls.other, name="Foreign")

    def setUp(self):
        self.client.force_login(self.user)

    def test_relation_is_directed_not_symmetric(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"builds_on": [self.django.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["builds_on"], [self.django.pk])

        # Django does NOT build on DRF, but lists it under `enables`.
        r = self.client.get(f"/api/jac/skills/{self.django.pk}/")
        self.assertEqual(r.data["builds_on"], [])
        self.assertIn(self.drf.pk, r.data["enables"])

    def test_self_reference_is_rejected(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"builds_on": [self.drf.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_build_on_another_users_skill(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"builds_on": [self.foreign.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.drf.refresh_from_db()
        self.assertEqual(self.drf.builds_on.count(), 0)

    def test_enables_is_read_only(self):
        # Writing `enables` directly is silently ignored (read-only field).
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"enables": [self.django.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.drf.refresh_from_db()
        self.assertEqual(self.drf.enables.count(), 0)


# ---------------------------------------------------------------------------
# Phase 3a — ResumeSnippet CRUD + scoping
# ---------------------------------------------------------------------------


class ResumeSnippetAPITests(APITestCase):
    """Snippets are user-scoped on create, list, and relation fields; `user`
    is never trusted from the body and choices/ownership are validated.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="snip_user", password="pass")
        cls.other = User.objects.create_user(username="snip_other", password="pass")
        cls.domain = Domain.objects.create(user=cls.user, name="Finance")
        cls.skill = Skill.objects.create(user=cls.user, name="Accounting")
        cls.foreign_skill = Skill.objects.create(user=cls.other, name="Foreign")
        cls.foreign_domain = Domain.objects.create(user=cls.other, name="ForeignDom")

    def setUp(self):
        self.client.force_login(self.user)

    def test_create_sets_user_from_request(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "Opening line",
                "content": "I build things people rely on.",
                "kind": "intro",
                "domains": [self.domain.pk],
                "skills": [self.skill.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        snippet = ResumeSnippet.objects.get(pk=r.data["id"])
        self.assertEqual(snippet.user, self.user)

    def test_user_cannot_be_spoofed_via_body(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "Spoof",
                "content": "nope",
                "kind": "other",
                "user": self.other.pk,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        snippet = ResumeSnippet.objects.get(pk=r.data["id"])
        self.assertEqual(snippet.user, self.user)

    def test_list_is_user_scoped(self):
        ResumeSnippet.objects.create(
            user=self.other, title="Theirs", content="x", kind="intro"
        )
        mine = ResumeSnippet.objects.create(
            user=self.user, title="Mine", content="y", kind="intro"
        )
        r = self.client.get("/api/jac/resume-snippets/")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertIn(mine.pk, ids)
        self.assertEqual(len(ids), 1)

    def test_kind_filter(self):
        intro = ResumeSnippet.objects.create(
            user=self.user, title="i", content="x", kind="intro"
        )
        ResumeSnippet.objects.create(
            user=self.user, title="c", content="y", kind="closing"
        )
        r = self.client.get("/api/jac/resume-snippets/?kind=intro")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [intro.pk])

    def test_invalid_kind_is_rejected(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {"title": "x", "content": "y", "kind": "not_a_kind"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_reference_another_users_skill(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "x",
                "content": "y",
                "kind": "other",
                "skills": [self.foreign_skill.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_reference_another_users_domain(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "x",
                "content": "y",
                "kind": "other",
                "domains": [self.foreign_domain.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)


class BulkDeleteAPITests(APITestCase):
    """`POST <resource>/bulk/ {"action":"delete"}` removes the user's own rows
    in one request, and refuses the whole batch if any id isn't theirs.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="bulk_alice", password="pass")
        cls.bob = User.objects.create_user(username="bulk_bob", password="pass")
        cls.s1 = Skill.objects.create(user=cls.alice, name="Python")
        cls.s2 = Skill.objects.create(user=cls.alice, name="Django")
        cls.bob_skill = Skill.objects.create(user=cls.bob, name="Rust")

    def setUp(self):
        self.client.force_login(self.alice)

    def test_bulk_delete_removes_own_rows(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, self.s2.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"deleted": 2})
        self.assertFalse(Skill.objects.filter(pk__in=[self.s1.pk, self.s2.pk]).exists())

    def test_nonexistent_id_aborts_whole_batch(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, 999999]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("ids", r.data)
        self.assertTrue(Skill.objects.filter(pk=self.s1.pk).exists())  # nothing deleted

    def test_cannot_delete_another_users_row(self):
        # bob's id is "missing" from alice's get_queryset() → 400, his row intact.
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, self.bob_skill.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Skill.objects.filter(pk=self.bob_skill.pk).exists())

    def test_unknown_action_is_400(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "nope", "ids": [self.s1.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_non_integer_ids_is_400(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": ["not-an-int"]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)


class BulkPatchDomainsAPITests(APITestCase):
    """`patch_domains` merges domains onto the user's rows (add/remove, not
    replace), only accepts domains the user may see, and only exists on
    resources that actually carry a `domains` M2M.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bpd_user", password="pass")
        cls.other = User.objects.create_user(username="bpd_other", password="pass")
        cls.system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        cls.d_keep = Domain.objects.create(user=cls.user, name="Keep")
        cls.d_remove = Domain.objects.create(user=cls.user, name="Remove")
        cls.d_add = Domain.objects.create(user=cls.user, name="Add")
        cls.d_default = Domain.objects.create(user=cls.system, name="Backend")
        cls.foreign = Domain.objects.create(user=cls.other, name="Foreign")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Co", started=date(2022, 1, 1)
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.job.domains.set([self.d_keep, self.d_remove])

    def test_add_and_remove_preserve_the_rest(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.d_add.pk],
                "remove": [self.d_remove.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"updated": 1})
        self.assertEqual(
            set(self.job.domains.values_list("pk", flat=True)),
            {self.d_keep.pk, self.d_add.pk},  # kept Keep, gained Add, lost Remove
        )

    def test_system_default_domain_is_allowed(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.d_default.pk],
                "remove": [],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.d_default.pk, self.job.domains.values_list("pk", flat=True))

    def test_foreign_domain_is_rejected(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.foreign.pk],
                "remove": [],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertNotIn(self.foreign.pk, self.job.domains.values_list("pk", flat=True))

    def test_patch_domains_unsupported_on_domainless_resource(self):
        lang = Language.objects.create(user=self.user, name="German")
        r = self.client.post(
            "/api/jac/languages/bulk/",
            {"action": "patch_domains", "ids": [lang.pk], "add": [], "remove": []},
            format="json",
        )
        self.assertEqual(r.status_code, 400)


class OrderingFieldsAPITests(APITestCase):
    """`updated_at` is now an allowed ordering (the `/cv` dashboard relies on
    it); a field outside the allow-list is ignored, not honoured.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ord_user", password="pass")
        cls.a = Job.objects.create(
            user=cls.user, title="Older", company="Co", started=date(2020, 1, 1)
        )
        cls.b = Job.objects.create(
            user=cls.user, title="Newer", company="Co", started=date(2023, 1, 1)
        )
        now = timezone.now()
        Job.objects.filter(pk=cls.a.pk).update(updated_at=now)
        Job.objects.filter(pk=cls.b.pk).update(updated_at=now - timedelta(days=1))

    def setUp(self):
        self.client.force_login(self.user)

    def test_ordering_by_updated_at_is_honoured(self):
        r = self.client.get("/api/jac/jobs/?ordering=-updated_at")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [self.a.pk, self.b.pk])  # most-recently-updated first

    def test_disallowed_ordering_field_falls_back_to_default(self):

        r = self.client.get("/api/jac/jobs/?ordering=title")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [self.b.pk, self.a.pk])


class DomainIsDefaultAPITests(APITestCase):
    """`is_default` is a read-only flag: true for the sentinel-owned shared
    taxonomy, false for the user's own tags, and never writable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="def_user", password="pass")
        cls.system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        cls.own = Domain.objects.create(user=cls.user, name="Mine")
        cls.default = Domain.objects.create(user=cls.system, name="Backend")

    def setUp(self):
        self.client.force_login(self.user)

    def test_flag_distinguishes_default_from_own(self):
        r = self.client.get("/api/jac/domains/")
        self.assertEqual(r.status_code, 200)
        flags = {row["id"]: row["is_default"] for row in r.data["results"]}
        self.assertFalse(flags[self.own.pk])
        self.assertTrue(flags[self.default.pk])

    def test_is_default_is_read_only(self):
        r = self.client.patch(
            f"/api/jac/domains/{self.own.pk}/",
            {"is_default": True},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_default"])


# ---------------------------------------------------------------------------
# Phase 3e — cv_export / cv_import round-trip
# ---------------------------------------------------------------------------


class DomainFilterAPITests(APITestCase):
    """`?domains=<id>` narrows list endpoints to entries carrying that domain —
    including Education and Certification, which gained the filter in 3a-bis.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="df_user", password="pass")
        cls.backend = Domain.objects.create(user=cls.user, name="Backend")
        cls.design = Domain.objects.create(user=cls.user, name="Design")

        edu_b = Education.objects.create(
            user=cls.user, institution="TU", started=date(2015, 1, 1)
        )
        edu_b.domains.add(cls.backend)
        edu_d = Education.objects.create(
            user=cls.user, institution="Arts", started=date(2016, 1, 1)
        )
        edu_d.domains.add(cls.design)

        cert_b = Certification.objects.create(
            user=cls.user, name="AWS", issuer="Amazon"
        )
        cert_b.domains.add(cls.backend)
        cert_d = Certification.objects.create(
            user=cls.user, name="Figma", issuer="Figma"
        )
        cert_d.domains.add(cls.design)

    def setUp(self):
        self.client.force_login(self.user)

    def test_education_filtered_by_domain(self):
        r = self.client.get(f"/api/jac/education/?domains={self.backend.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([e["institution"] for e in r.data["results"]], ["TU"])

    def test_certification_filtered_by_domain(self):
        r = self.client.get(f"/api/jac/certifications/?domains={self.backend.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["name"] for c in r.data["results"]], ["AWS"])


class CvExportImportRoundTripTests(TestCase):
    """`cv_export` of user A, imported into a fresh user B, reproduces the CV —
    including related_skills symmetry, the years override, certification +
    location references, resume snippets, and per-user domain scoping.
    """

    @classmethod
    def setUpTestData(cls):
        cls.a = User.objects.create_user(username="rt_a", password="pass")
        cls.b = User.objects.create_user(username="rt_b", password="pass")

        domain = Domain.objects.create(user=cls.a, name="Backend")
        loc = Location.objects.create(user=cls.a, city="Berlin", country="DE")
        cert = Certification.objects.create(
            user=cls.a, name="AWS SAA", issuer="Amazon", issued_on=date(2022, 5, 1)
        )

        py = Skill.objects.create(
            user=cls.a,
            name="Python",
            proficiency="expert",
            category="technical",
            years_of_experience_override=5,
            certification=cert,
        )
        sev = Skill.objects.create(user=cls.a, name="SevDesk")
        py.related_skills.add(sev)
        py.domains.add(domain)

        # Directed prerequisite chain: DRF builds on Django builds on Python.
        django = Skill.objects.create(user=cls.a, name="Django")
        drf = Skill.objects.create(user=cls.a, name="DRF")
        django.builds_on.add(py)
        drf.builds_on.add(django)

        # Certification evidences a skill + sits in a domain.
        cert.skills.add(py)
        cert.domains.add(domain)

        job = Job.objects.create(
            user=cls.a,
            title="Engineer",
            company="Co",
            location=loc,
            started=date(2021, 1, 1),
        )
        job.skills.add(py)
        job.domains.add(domain)

        # Education with its own skills + domains.
        edu = Education.objects.create(
            user=cls.a,
            institution="TU Berlin",
            field_of_study="CS",
            started=date(2015, 10, 1),
        )
        edu.skills.add(py)
        edu.domains.add(domain)

        # Project tied to the job it was built at.
        project = Project.objects.create(
            user=cls.a, name="Pipeline", started=date(2021, 3, 1), job=job
        )

        Language.objects.create(user=cls.a, name="German", fluency="native")

        snippet = ResumeSnippet.objects.create(
            user=cls.a,
            title="Intro",
            content="Hi.",
            kind="intro",
            job=job,
            project=project,
        )
        snippet.skills.add(py)
        snippet.domains.add(domain)

    def _round_trip(self):
        """Export A to a temp file and import it into B."""
        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(buf.getvalue())
            path = fh.name
        try:
            call_command("cv_import", "--username", "rt_b", "--file", path)
        finally:
            os.remove(path)

    def test_skill_fields_and_relation_survive(self):
        self._round_trip()
        py = Skill.objects.get(user=self.b, name="Python")
        self.assertEqual(py.proficiency, "expert")
        self.assertEqual(py.years_of_experience_override, 5)
        self.assertEqual(py.certification.name, "AWS SAA")
        # symmetric relation resolved by name
        self.assertIn("SevDesk", py.related_skills.values_list("name", flat=True))
        sev = Skill.objects.get(user=self.b, name="SevDesk")
        self.assertIn("Python", sev.related_skills.values_list("name", flat=True))

    def test_builds_on_direction_survives(self):
        self._round_trip()
        drf = Skill.objects.get(user=self.b, name="DRF")
        django = Skill.objects.get(user=self.b, name="Django")
        py = Skill.objects.get(user=self.b, name="Python")
        # Forward edges preserved …
        self.assertIn("Django", drf.builds_on.values_list("name", flat=True))
        self.assertIn("Python", django.builds_on.values_list("name", flat=True))
        # … and the direction does NOT leak back (asymmetry holds).
        self.assertNotIn("DRF", django.builds_on.values_list("name", flat=True))
        self.assertEqual(py.builds_on.count(), 0)
        self.assertIn("Django", py.enables.values_list("name", flat=True))

    def test_education_certification_project_relations_survive(self):
        self._round_trip()
        edu = Education.objects.get(user=self.b, institution="TU Berlin")
        self.assertIn("Python", edu.skills.values_list("name", flat=True))
        self.assertIn("Backend", edu.domains.values_list("name", flat=True))

        cert = Certification.objects.get(user=self.b, name="AWS SAA")
        self.assertIn("Python", cert.skills.values_list("name", flat=True))
        self.assertIn("Backend", cert.domains.values_list("name", flat=True))

        project = Project.objects.get(user=self.b, name="Pipeline")
        self.assertEqual(project.job.title, "Engineer")
        self.assertEqual(project.job.user, self.b)

        snippet = ResumeSnippet.objects.get(user=self.b, title="Intro")
        self.assertEqual(snippet.job.title, "Engineer")
        self.assertEqual(snippet.project.name, "Pipeline")

    def test_domains_are_scoped_to_importing_user(self):
        self._round_trip()
        py = Skill.objects.get(user=self.b, name="Python")
        b_domain = Domain.objects.get(user=self.b, name="Backend")
        self.assertIn(b_domain, py.domains.all())
        # A's original domain is untouched / not cross-linked to B.
        self.assertEqual(Domain.objects.filter(name="Backend").count(), 2)
        self.assertEqual(Domain.objects.filter(user=self.a, name="Backend").count(), 1)

    def test_job_location_and_snippet_round_trip(self):
        self._round_trip()
        job = Job.objects.get(user=self.b, title="Engineer")
        self.assertEqual(job.location.city, "Berlin")
        self.assertEqual(job.location.user, self.b)
        self.assertIn("Python", job.skills.values_list("name", flat=True))

        snippet = ResumeSnippet.objects.get(user=self.b, title="Intro")
        self.assertEqual(snippet.kind, "intro")
        self.assertIn("Python", snippet.skills.values_list("name", flat=True))
        self.assertIn("Backend", snippet.domains.values_list("name", flat=True))

    def test_computed_years_not_exported(self):
        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        # The computed read-only property must not leak into the dump.
        self.assertNotIn('years_of_experience"', buf.getvalue())
        self.assertIn("years_of_experience_override", buf.getvalue())

    def test_system_default_domain_reused_not_duplicated(self):
        # A tags a skill with a shared system-default domain.
        system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        sysdom = Domain.objects.create(user=system, name="finance")
        Skill.objects.get(user=self.a, name="Python").domains.add(sysdom)

        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        dump = buf.getvalue()
        data = json.loads(dump)
        # The shared default is NOT written as one of A's own domains …
        self.assertNotIn("finance", [d["name"] for d in data["domains"]])
        # … but the skill still references it by name.
        py_dump = next(s for s in data["skills"] if s["name"] == "Python")
        self.assertIn("finance", py_dump["domains"])

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(dump)
            path = fh.name
        try:
            call_command("cv_import", "--username", "rt_b", "--file", path)
        finally:
            os.remove(path)

        # Import reused the existing system default — no user-owned duplicate.
        self.assertEqual(Domain.objects.filter(name="finance").count(), 1)
        self.assertFalse(Domain.objects.filter(user=self.b, name="finance").exists())
        b_py = Skill.objects.get(user=self.b, name="Python")
        self.assertIn(sysdom, b_py.domains.all())


# ---------------------------------------------------------------------------
# CV edge / selection tests
# ---------------------------------------------------------------------------


class CVEdgeTests(TestCase):
    """_flatten_entries emits correct relationship edges."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="edgeuser")
        cls.cert = Certification.objects.create(
            user=cls.user, name="AWS SA", issuer="Amazon"
        )
        cls.skill = Skill.objects.create(
            user=cls.user, name="Python", certification=cls.cert
        )
        cls.cert.skills.add(cls.skill)
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )
        cls.job.skills.add(cls.skill)
        cls.project = Project.objects.create(
            user=cls.user, name="Side", started=date(2023, 1, 1), job=cls.job
        )
        cls.project.skills.add(cls.skill)

    def _by_id(self):
        return {e["id"]: e for e in CV(user_pk=self.user.pk)._flatten_entries()}

    def test_job_refs_skill_and_project(self):
        flat = self._by_id()
        refs = set(flat[f"job:{self.job.pk}"]["refs"])
        self.assertIn(f"skill:{self.skill.pk}", refs)
        self.assertIn(f"project:{self.project.pk}", refs)

    def test_project_refs_skill_and_job(self):
        refs = set(self._by_id()[f"project:{self.project.pk}"]["refs"])
        self.assertIn(f"skill:{self.skill.pk}", refs)
        self.assertIn(f"job:{self.job.pk}", refs)

    def test_skill_refs_certification(self):
        refs = self._by_id()[f"skill:{self.skill.pk}"]["refs"]
        self.assertIn(f"certification:{self.cert.pk}", refs)

    def test_refs_pruned_to_existing_ids(self):
        # Skill filtered out by proficiency -> job must not ref a missing skill.
        cv = CV(user_pk=self.user.pk, min_skill_proficiency="expert")
        flat = {e["id"]: e for e in cv._flatten_entries()}
        if f"skill:{self.skill.pk}" not in flat:  # intermediate skill dropped
            self.assertNotIn(
                f"skill:{self.skill.pk}", flat[f"job:{self.job.pk}"]["refs"]
            )


class CVSelectionTests(TestCase):
    """CVFilter propagation + per-section drop, with injected fake scores."""

    def _entries(self):
        return [
            {"id": "job:1", "type": "job", "text": "", "refs": ["skill:1"]},
            {"id": "skill:1", "type": "skill", "text": "", "refs": []},
            {"id": "skill:2", "type": "skill", "text": "", "refs": []},
            {
                "id": "certification:1",
                "type": "certification",
                "text": "",
                "refs": ["skill:1"],
            },
            {"id": "language:1", "type": "language", "text": "", "refs": []},
        ]

    def _filter(self):
        return CVFilter(job_post_text="x", entries=self._entries(), grade="light")

    def test_propagation_lifts_low_skill_under_strong_job(self):
        f = self._filter()
        base = {"job:1": 0.9, "skill:1": 0.05, "skill:2": 0.05}
        eff = f._propagate(base)
        # skill:1 is anchored by job:1 -> lifted to 0.85 * 0.9.
        self.assertAlmostEqual(eff["skill:1"], 0.765, places=3)
        # skill:2 has no high-tier neighbour -> untouched.
        self.assertAlmostEqual(eff["skill:2"], 0.05, places=3)

    def test_propagation_chains_job_to_skill_to_cert(self):
        f = self._filter()
        eff = f._propagate({"job:1": 1.0, "skill:1": 0.0, "certification:1": 0.0})
        # job (0.85) -> skill:1, then skill:1 (0.85) -> cert.
        self.assertAlmostEqual(eff["skill:1"], 0.85, places=3)
        self.assertAlmostEqual(eff["certification:1"], 0.7225, places=3)

    def test_low_skill_dropped_below_floor(self):
        f = self._filter()
        # All scores low, no anchoring; skill floor 0.35, min_keep 5 but only 2 skills exist.
        out = f._select({"job:1": 0.9, "skill:1": 0.10, "skill:2": 0.10})
        kept = {e["id"] for e in out.get("skill", [])}
        # min_keep(5) > available(2) -> both skills kept despite being below floor.
        self.assertEqual(kept, {"skill:1", "skill:2"})

    def test_skill_floor_drops_when_above_min_keep(self):
        entries = [
            {"id": f"skill:{i}", "type": "skill", "text": "", "refs": []}
            for i in range(1, 8)
        ]
        f = CVFilter(job_post_text="x", entries=entries, grade="light")
        base = {f"skill:{i}": (0.9 if i <= 5 else 0.10) for i in range(1, 8)}
        out = f._select(base)
        kept = {e["id"] for e in out["skill"]}
        # 5 above floor kept; the 2 below floor dropped (min_keep already satisfied).
        self.assertEqual(kept, {f"skill:{i}" for i in range(1, 6)})

    def test_languages_never_dropped(self):
        f = self._filter()
        out = f._select({"language:1": 0.0})
        self.assertEqual([e["id"] for e in out["language"]], ["language:1"])

    def test_empty_base_keeps_everything(self):
        f = self._filter()
        out = f._select({})
        kept = {e["id"] for sect in out.values() for e in sect}
        self.assertEqual(kept, {e["id"] for e in self._entries()})

    def test_sections_ranked_descending(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": []},
            {"id": "job:2", "type": "job", "text": "", "refs": []},
        ]
        f = CVFilter(job_post_text="x", entries=entries, grade="light")
        out = f._select({"job:1": 0.3, "job:2": 0.8})
        self.assertEqual([e["id"] for e in out["job"]], ["job:2", "job:1"])


class EmbedAliasPassthroughTests(TestCase):
    """Embed forwards alias + user to embed() so the light rung honours --llm."""

    def _entries(self):
        return [{"id": "skill:1", "type": "skill", "text": "Python"}]

    def test_query_passes_alias_and_user(self):
        with patch("jac.llm_prompts.embed", return_value=[[0.1]]) as m:
            Embed("posting", self._entries(), user=7, alias="reasoning")._query()
        _, kwargs = m.call_args
        self.assertEqual(kwargs["alias"], "reasoning")
        self.assertEqual(kwargs["user"], 7)

    def test_defaults_to_default_alias_no_user(self):
        with patch("jac.llm_prompts.embed", return_value=[[0.1]]) as m:
            Embed("posting", self._entries())._query()
        _, kwargs = m.call_args
        self.assertEqual(kwargs["alias"], "default")
        self.assertIsNone(kwargs["user"])


class CVFilterFloorsTests(TestCase):
    """CVFilter._floors merges config embed_floors over _SECTION_POLICY defaults,
    and _select drops by the resolved floor."""

    def _entries(self):
        return [
            {"id": f"skill:{i}", "type": "skill", "text": "", "refs": []}
            for i in range(1, 8)
        ]

    def test_floors_merge_config_over_defaults(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="light")
        with patch("jac.filter.get_embed_floors", return_value={"skill": 0.55}):
            floors = f._floors()
        self.assertEqual(floors["skill"], 0.55)  # overridden by config
        self.assertEqual(floors["job"], 0.20)  # default kept

    def test_select_uses_overridden_floor(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="light")
        # 3 skills clear the default 0.35 floor; the other 4 sit at 0.20 (below default).
        base = {f"skill:{i}": (0.5 if i <= 3 else 0.20) for i in range(1, 8)}
        # Default would keep 3 + min_keep top-up to 5; lower the floor and all 7 clear it.
        with patch("jac.filter.get_embed_floors", return_value={"skill": 0.15}):
            out = f._select(base)
        self.assertEqual(len(out["skill"]), 7)

    def test_default_floor_when_no_override(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="light")
        base = {f"skill:{i}": (0.5 if i <= 3 else 0.20) for i in range(1, 8)}
        with patch("jac.filter.get_embed_floors", return_value={}):
            out = f._select(base)
        # 3 above the 0.35 default + min_keep(5) tops up to 5.
        self.assertEqual(len(out["skill"]), 5)


class ResolveRunsTests(TestCase):
    """cv_eval._resolve_runs: the grade×llm selection matrix."""

    def _strength(self, alias):
        return {"default": "light", "reasoning": "standard"}.get(alias, "strong")

    def test_neither_uses_default_at_autodetected_grade(self):
        self.assertEqual(
            _resolve_runs(None, None, ["a", "b"], self._strength),
            [("default", "light")],
        )

    def test_llm_only_autodetects_grade(self):
        self.assertEqual(
            _resolve_runs(None, "reasoning", ["a", "b"], self._strength),
            [("reasoning", "standard")],
        )

    def test_grade_only_fans_out_over_all_models(self):
        self.assertEqual(
            _resolve_runs("standard", None, ["a", "b"], self._strength),
            [("a", "standard"), ("b", "standard")],
        )

    def test_both_uses_the_exact_pair(self):
        self.assertEqual(
            _resolve_runs("light", "reasoning", ["a", "b"], self._strength),
            [("reasoning", "light")],
        )


class CVApplySelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="applyuser")
        cls.s1 = Skill.objects.create(user=cls.user, name="Python")
        cls.s2 = Skill.objects.create(user=cls.user, name="SQL")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )

    def test_prunes_and_orders_and_scores(self):
        cv = CV(user_pk=self.user.pk)
        selection = {
            "skill": [
                {"id": f"skill:{self.s2.pk}", "score": 0.9},
                {"id": f"skill:{self.s1.pk}", "score": 0.4},
            ],
            "job": [{"id": f"job:{self.job.pk}", "score": 0.7}],
        }
        cv.apply_selection(selection)
        # skills kept in the selection's (ranked) order, not DB order.
        self.assertEqual([s.pk for s in cv.entries["skills"]], [self.s2.pk, self.s1.pk])
        self.assertEqual(cv.entries["skills"][0].relevance_score, 0.9)
        self.assertEqual([j.pk for j in cv.entries["jobs"]], [self.job.pk])

    def test_section_absent_from_selection_is_emptied(self):
        cv = CV(user_pk=self.user.pk)
        cv.apply_selection({"job": [{"id": f"job:{self.job.pk}", "score": 1.0}]})
        self.assertEqual(cv.entries["skills"], [])
        self.assertEqual([j.pk for j in cv.entries["jobs"]], [self.job.pk])

    def test_unknown_ids_are_ignored(self):
        cv = CV(user_pk=self.user.pk)
        cv.apply_selection({"skill": [{"id": "skill:999999", "score": 1.0}]})
        self.assertEqual(cv.entries["skills"], [])


class FavouriteLimitModelTests(TestCase):
    """CvEntry.clean() enforces the per-type favourite cap (Education limit = 2)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="favmodel")

    def _edu(self, favourite, institution):
        return Education.objects.create(
            user=self.user,
            institution=institution,
            started=date(2020, 1, 1),
            favourite=favourite,
        )

    def test_clean_blocks_over_limit(self):
        self._edu(True, "A")
        self._edu(True, "B")  # at the limit of 2
        extra = Education(
            user=self.user,
            institution="C",
            started=date(2020, 1, 1),
            favourite=True,
        )
        with self.assertRaises(DjangoValidationError):
            extra.clean()

    def test_clean_allows_within_limit(self):
        self._edu(True, "A")
        ok = Education(
            user=self.user,
            institution="B",
            started=date(2020, 1, 1),
            favourite=True,
        )
        ok.clean()  # second favourite is still within the limit -> no raise

    def test_clean_excludes_self_on_update(self):
        edu = self._edu(True, "A")
        self._edu(True, "B")
        edu.description = "edited"
        edu.clean()  # re-saving an existing favourite must not count itself out

    def test_non_favourite_unconstrained(self):
        for i in range(5):
            self._edu(False, f"U{i}")  # no cap on non-favourites


class FavouriteLimitAPITests(APITestCase):
    """The API enforces the same cap via FavouriteLimitMixin (Job limit = 4)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="favapi", password="pass")

    def setUp(self):
        self.client.force_login(self.user)
        for i in range(4):
            Job.objects.create(
                user=self.user,
                title=f"J{i}",
                company="Acme",
                started=date(2022, 1, 1),
                favourite=True,
            )

    def test_create_over_limit_rejected(self):
        r = self.client.post(
            "/api/jac/jobs/",
            {
                "title": "Over",
                "company": "Acme",
                "started": "2022-01-01",
                "favourite": True,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("favourite", r.data)

    def test_create_non_favourite_allowed(self):
        r = self.client.post(
            "/api/jac/jobs/",
            {
                "title": "Plain",
                "company": "Acme",
                "started": "2022-01-01",
                "favourite": False,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)


class FavouriteOrderingAPITests(APITestCase):
    """`ordering=-favourite,...` floats flagged entries to the top (the table star sort)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="favorder", password="pass")
        Job.objects.create(
            user=cls.user, title="Plain", company="Acme", started=date(2024, 1, 1)
        )
        Job.objects.create(
            user=cls.user,
            title="Pinned",
            company="Acme",
            started=date(2019, 1, 1),
            favourite=True,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_favourites_first(self):
        r = self.client.get("/api/jac/jobs/?ordering=-favourite,-started")
        self.assertEqual(r.status_code, 200)
        titles = [row["title"] for row in r.data["results"]]
        # Pinned floats above the more-recent Plain job despite the -started secondary.
        self.assertEqual(titles[0], "Pinned")


class CVFavouriteBonusTests(TestCase):
    """CVFilter applies a small post-propagation nudge to favourites."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="light")

    def test_bonus_added_and_reranks(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": [], "favourite": True},
            {"id": "job:2", "type": "job", "text": "", "refs": [], "favourite": False},
        ]
        out = self._filter(entries)._select({"job:1": 0.40, "job:2": 0.40})
        scores = {e["id"]: e["score"] for e in out["job"]}
        self.assertAlmostEqual(scores["job:1"], 0.45, places=4)
        self.assertAlmostEqual(scores["job:2"], 0.40, places=4)
        # tie broken in the favourite's favour.
        self.assertEqual(out["job"][0]["id"], "job:1")

    def _edus(self, fav_score):
        # Two strong educations + one favourite at `fav_score`; education floor 0.15,
        # min_keep 2 (already satisfied by the two strong ones).
        return [
            {
                "id": "education:1",
                "type": "education",
                "text": "",
                "refs": [],
                "favourite": False,
            },
            {
                "id": "education:2",
                "type": "education",
                "text": "",
                "refs": [],
                "favourite": False,
            },
            {
                "id": "education:3",
                "type": "education",
                "text": "",
                "refs": [],
                "favourite": True,
            },
        ], {"education:1": 0.9, "education:2": 0.9, "education:3": fav_score}

    def test_bonus_cannot_resurrect_zero_scored_favourite(self):
        entries, base = self._edus(0.0)
        out = self._filter(entries)._select(base)
        kept = {e["id"] for e in out["education"]}
        # 0.0 + 0.05 = 0.05 < 0.15 floor -> stays dropped.
        self.assertNotIn("education:3", kept)

    def test_bonus_lifts_borderline_favourite(self):
        entries, base = self._edus(0.12)
        out = self._filter(entries)._select(base)
        kept = {e["id"] for e in out["education"]}
        # 0.12 + 0.05 = 0.17 >= 0.15 floor -> crosses.
        self.assertIn("education:3", kept)


def _keep_all(self, job_post_text, grade=None):
    """Stand-in for CV.filter_cv: keep every flattened entry, score 1.0."""
    out: dict = {}
    for e in self._flatten_entries():
        out.setdefault(e["type"], []).append({**e, "score": 1.0})
    return out


class CVCommandSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="cmduser")
        cls.skill = Skill.objects.create(user=cls.user, name="Python")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )
        cls.job.skills.add(cls.skill)

    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_test_writes_one_md_per_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                "cv_test",
                "--user",
                str(self.user.pk),
                "--job",
                "Senior Python engineer",
                "--grades",
                "light",
                "standard",
                "--out-dir",
                tmp,
                stdout=io.StringIO(),
            )
            self.assertTrue((Path(tmp) / "cv_light.md").exists())
            self.assertTrue((Path(tmp) / "cv_standard.md").exists())

    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_eval_writes_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "posting.md"
            job.write_text("Senior Python engineer")
            call_command(
                "cv_eval",
                "--user",
                str(self.user.pk),
                "--job-file",
                str(job),
                "--out-dir",
                tmp,
                stdout=io.StringIO(),
            )
            self.assertTrue((Path(tmp) / "findings.json").exists())
            self.assertTrue((Path(tmp) / "findings.md").exists())
