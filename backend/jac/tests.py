import io
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from jac import llm as jac_llm
from jac.cv import CV
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


# ---------------------------------------------------------------------------
# CV deterministic helpers
# ---------------------------------------------------------------------------


class CVDeterministicFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        cls.dom = Domain.objects.create(user=cls.user, name="Healthcare")

        cls.skill = Skill.objects.create(user=cls.user, name="Python")
        cls.skill.domains.add(cls.dom)
        cls.skill_unrelated = Skill.objects.create(user=cls.user, name="COBOL")

        cls.job = Job.objects.create(
            user=cls.user,
            title="ML Engineer",
            company="HealthCo",
            started=date(2022, 1, 1),
        )
        cls.job.skills.add(cls.skill)

        cls.job_unrelated = Job.objects.create(
            user=cls.user,
            title="Janitor",
            company="Unrelated",
            started=date(2010, 1, 1),
        )

        Education.objects.create(
            user=cls.user,
            institution="Health University",
            field_of_study="Bio",
            started=date(2010, 1, 1),
        )
        Education.objects.create(
            user=cls.user,
            institution="Art School",
            field_of_study="Painting",
            started=date(2008, 1, 1),
        )

        Certification.objects.create(
            user=cls.user, name="Healthcare Data Cert", issuer="X"
        )
        Certification.objects.create(user=cls.user, name="Unrelated", issuer="Y")

        cls.proj = Project.objects.create(
            user=cls.user, name="Patient ETL", started=date(2021, 1, 1)
        )
        cls.proj.domains.add(cls.dom)
        Project.objects.create(
            user=cls.user, name="Cat photo blog", started=date(2018, 1, 1)
        )

        Language.objects.create(user=cls.user, name="English")
        Language.objects.create(user=cls.user, name="Spanish")

    def test_empty_keywords_is_noop(self):
        cv = CV(user_pk=self.user.pk)
        before = {k: len(v) for k, v in cv.entries.items()}
        cv.deterministic_filter([])
        after = {k: len(v) for k, v in cv.entries.items()}
        self.assertEqual(before, after)

    def test_filter_matches_substrings_case_insensitively(self):
        cv = CV(user_pk=self.user.pk)
        cv.deterministic_filter(["health"])
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})
        self.assertEqual({j.title for j in cv.entries["jobs"]}, {"ML Engineer"})
        self.assertEqual(
            {e.institution for e in cv.entries["educations"]}, {"Health University"}
        )
        self.assertEqual(
            {c.name for c in cv.entries["certifications"]}, {"Healthcare Data Cert"}
        )
        self.assertEqual({p.name for p in cv.entries["projects"]}, {"Patient ETL"})

    def test_filter_matches_through_related_skills_and_domains(self):
        cv = CV(user_pk=self.user.pk)
        # 'python' is only on a skill or via a skill's name on a job
        cv.deterministic_filter(["python"])
        self.assertEqual({j.title for j in cv.entries["jobs"]}, {"ML Engineer"})

    def test_filter_languages_by_name(self):
        cv = CV(user_pk=self.user.pk)
        cv.deterministic_filter(["english"])
        self.assertEqual({la.name for la in cv.entries["languages"]}, {"English"})


class CVExtractKeywordsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        Skill.objects.create(user=cls.user, name="Python")
        Skill.objects.create(user=cls.user, name="Go")
        Language.objects.create(user=cls.user, name="English")

    def test_returns_lowercase_significant_words(self):
        cv = CV(user_pk=self.user.pk)
        matched = cv.extract_keywords("We need Python and Haskell developers.")
        self.assertIn("python", matched)
        self.assertIn("haskell", matched)
        self.assertIn("developers", matched)

    def test_short_tokens_filtered_out(self):
        # Min length 3 — "We", "Go", "to" never surface.
        cv = CV(user_pk=self.user.pk)
        matched = cv.extract_keywords("We go to work today.")
        self.assertNotIn("we", matched)
        self.assertNotIn("go", matched)
        self.assertNotIn("to", matched)

    def test_strict_drops_filler_loose_keeps_it(self):
        cv = CV(user_pk=self.user.pk)
        strict = cv.extract_keywords("Looking for a team player with experience.")
        loose = cv.extract_keywords(
            "Looking for a team player with experience.", loose=True
        )
        self.assertNotIn("team", strict)
        self.assertNotIn("experience", strict)
        self.assertIn("team", loose)
        self.assertIn("experience", loose)

    def test_empty_text_returns_empty(self):
        cv = CV(user_pk=self.user.pk)
        self.assertEqual(cv.extract_keywords(""), [])

    def test_sorted_longest_first(self):
        cv = CV(user_pk=self.user.pk)
        matched = cv.extract_keywords("PostgreSQL and Python developers.")
        lengths = [len(w) for w in matched]
        self.assertEqual(lengths, sorted(lengths, reverse=True))


class CVDeterministicFilterTextRetryTests(TestCase):
    """When given raw text, deterministic_filter retries with loose stopwords
    if the strict pass keeps fewer than min_kept entries."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        # Entries whose only overlap with the job text is a "filler" word —
        # caught by loose but dropped by strict.
        Skill.objects.create(
            user=cls.user, name="Python", description="Strong team player."
        )
        Skill.objects.create(
            user=cls.user, name="Java", description="Years of experience."
        )
        Skill.objects.create(
            user=cls.user, name="Rust", description="Worked on team projects."
        )

    def test_strict_first_then_loose_fallback(self):
        cv = CV(user_pk=self.user.pk)
        # Posting has no overlap on tech terms — only "team" and "experience".
        used = cv.deterministic_filter(
            "We are looking for a team player with experience.", min_kept=2
        )
        # Loose retry should have kicked in; "team" should be among needles.
        self.assertIn("team", used)
        # At least 2 entries should survive thanks to the retry.
        kept = sum(len(v) for v in cv.entries.values())
        self.assertGreaterEqual(kept, 2)

    def test_text_input_returns_keywords_used(self):
        cv = CV(user_pk=self.user.pk)
        used = cv.deterministic_filter("Strong Python developer wanted.")
        self.assertIsInstance(used, list)
        self.assertIn("python", used)


# ---------------------------------------------------------------------------
# CV LLM-driven methods (mocked)
# ---------------------------------------------------------------------------


class CVAIMethodsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        cls.skill_py = Skill.objects.create(user=cls.user, name="Python")
        cls.skill_excel = Skill.objects.create(user=cls.user, name="Excel")
        cls.job = Job.objects.create(
            user=cls.user,
            title="Engineer",
            company="Acme",
            started=date(2022, 1, 1),
        )

    def test_entries_for_llm_flattens_all_sections(self):
        cv = CV(user_pk=self.user.pk)
        flat = cv._entries_for_llm()
        ids = {item["id"] for item in flat}
        self.assertIn(f"skill:{self.skill_py.pk}", ids)
        self.assertIn(f"skill:{self.skill_excel.pk}", ids)
        self.assertIn(f"job:{self.job.pk}", ids)
        for item in flat:
            self.assertIn("type", item)
            self.assertIn("text", item)

    def test_apply_scores_attaches_relevance_attrs(self):
        cv = CV(user_pk=self.user.pk)
        scores = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.9, "reason": "match"},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.1, "reason": "weak"},
            {"id": f"job:{self.job.pk}", "score": 0.5, "reason": "ok"},
        ]
        cv._apply_scores(scores)
        skills = {s.pk: s for s in cv.entries["skills"]}
        self.assertEqual(skills[self.skill_py.pk].relevance_score, 0.9)
        self.assertEqual(skills[self.skill_py.pk].relevance_reason, "match")
        self.assertEqual(skills[self.skill_excel.pk].relevance_score, 0.1)
        self.assertEqual(cv.entries["jobs"][0].relevance_score, 0.5)

    def test_apply_scores_defaults_missing_to_zero(self):
        cv = CV(user_pk=self.user.pk)
        cv._apply_scores([])  # no scores for anything
        for s in cv.entries["skills"]:
            self.assertEqual(s.relevance_score, 0.0)
            self.assertEqual(s.relevance_reason, "")

    def test_apply_scores_handles_unparseable_score(self):
        cv = CV(user_pk=self.user.pk)
        cv._apply_scores([{"id": f"skill:{self.skill_py.pk}", "score": "not a number"}])
        skills = {s.pk: s for s in cv.entries["skills"]}
        self.assertEqual(skills[self.skill_py.pk].relevance_score, 0.0)

    def test_apply_scores_skips_entries_without_id(self):
        cv = CV(user_pk=self.user.pk)
        # Should not raise.
        cv._apply_scores([{"score": 0.9}])

    @patch("jac.cv.jac_llm.score_entries_for_job")
    def test_ai_filter_entries_drops_below_threshold(self, mock_score):
        cv = CV(user_pk=self.user.pk)
        mock_score.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.9, "reason": "yes"},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.1, "reason": "no"},
            {"id": f"job:{self.job.pk}", "score": 0.5, "reason": "ok"},
        ]
        # Patch the per-section floors to 0 so the threshold drop is what's tested.
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False):
            cv.ai_filter_entries("some job text", threshold=0.4)
        skill_names = {s.name for s in cv.entries["skills"]}
        self.assertEqual(skill_names, {"Python"})
        self.assertEqual(len(cv.entries["jobs"]), 1)

    @patch("jac.cv.jac_llm.score_entries_for_job")
    def test_ai_filter_entries_floor_fills_sparse_section(self, mock_score):
        """When fewer than the per-section floor survive the threshold, the
        ranked top-K are kept instead so the section is never empty."""
        cv = CV(user_pk=self.user.pk)
        mock_score.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.9, "reason": "yes"},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.1, "reason": "no"},
            {"id": f"job:{self.job.pk}", "score": 0.05, "reason": "no"},
        ]
        cv.ai_filter_entries("some job text", threshold=0.4)
        # Floor for skills is 5 but only 2 exist — both are kept (Python ranked first).
        skill_names_in_order = [s.name for s in cv.entries["skills"]]
        self.assertEqual(skill_names_in_order, ["Python", "Excel"])
        # Floor for jobs is 3 but only 1 exists — kept even though below threshold.
        self.assertEqual(len(cv.entries["jobs"]), 1)

    @patch("jac.cv.jac_llm.score_entries_for_job")
    def test_ai_rank_entries_sorts_desc_without_dropping(self, mock_score):
        cv = CV(user_pk=self.user.pk)
        mock_score.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.2},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.8},
        ]
        cv.ai_rank_entries("some job text")
        names_in_order = [s.name for s in cv.entries["skills"]]
        self.assertEqual(names_in_order[:2], ["Excel", "Python"])
        # Nothing was dropped.
        self.assertEqual(len(cv.entries["skills"]), 2)

    @patch("jac.cv.jac_llm.extract_job_keywords")
    def test_ai_extract_keywords_delegates_to_llm(self, mock_extract):
        mock_extract.return_value = ["Python", "Django"]
        cv = CV(user_pk=self.user.pk)
        self.assertEqual(cv.ai_extract_keywords("posting"), ["Python", "Django"])
        mock_extract.assert_called_once_with(
            "posting", llm="default", user=self.user.pk
        )

    @patch("jac.cv.jac_llm.score_entries_with_analysis")
    @patch("jac.cv.jac_llm.analyze_job")
    def test_agentic_tailor_calls_analyze_then_filters_and_sorts(
        self, mock_analyze, mock_score
    ):
        mock_analyze.return_value = {"role_title": "Eng", "must_have": ["Python"]}
        mock_score.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.95},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.05},
            {"id": f"job:{self.job.pk}", "score": 0.7},
        ]
        cv = CV(user_pk=self.user.pk)
        # Patch the skills floor to 0 so the threshold drop is what's tested
        # (default floor of 5 would otherwise refill from the ranked list).
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0}, clear=False):
            analysis = cv.agentic_tailor("posting", threshold=0.4)

        self.assertEqual(analysis["role_title"], "Eng")
        # analysis is passed through to scorer
        _, kwargs = mock_score.call_args
        positional = mock_score.call_args.args
        self.assertEqual(positional[1], analysis)
        # Filtered: Excel (0.05) dropped, Python kept.
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})

    @patch("jac.cv.jac_llm.score_entries_for_job")
    def test_ai_filter_skips_llm_when_no_entries(self, mock_score):
        empty_user = User.objects.create(username="empty")
        cv = CV(user_pk=empty_user.pk)
        cv.ai_filter_entries("posting")
        mock_score.assert_not_called()

    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_ai_conversational_tailor_keeps_selection_in_order(self, mock_tailor):
        mock_tailor.return_value = [
            {"id": f"skill:{self.skill_excel.pk}", "reason": "spreadsheet ops"},
            {"id": f"skill:{self.skill_py.pk}", "reason": "automation"},
        ]
        cv = CV(user_pk=self.user.pk)
        selection = cv.ai_conversational_tailor("posting")

        names_in_order = [s.name for s in cv.entries["skills"]]
        self.assertEqual(names_in_order, ["Excel", "Python"])
        # The selected job wasn't in the response → jobs section drops to empty.
        self.assertEqual(cv.entries["jobs"], [])
        # Reasons are attached to the model instances.
        excel = next(s for s in cv.entries["skills"] if s.name == "Excel")
        self.assertEqual(excel.relevance_reason, "spreadsheet ops")
        self.assertEqual(len(selection), 2)

    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_ai_conversational_tailor_propagates_value_error(self, mock_tailor):
        mock_tailor.side_effect = ValueError("too few valid ids")
        cv = CV(user_pk=self.user.pk)
        with self.assertRaises(ValueError):
            cv.ai_conversational_tailor("posting")

    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_ai_conversational_tailor_skips_llm_when_no_entries(self, mock_tailor):
        empty_user = User.objects.create(username="empty")
        cv = CV(user_pk=empty_user.pk)
        out = cv.ai_conversational_tailor("posting")
        self.assertEqual(out, [])
        mock_tailor.assert_not_called()


# ---------------------------------------------------------------------------
# CV fallback ladder
# ---------------------------------------------------------------------------


class CVTailorWithFallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        cls.skill_py = Skill.objects.create(
            user=cls.user, name="Python", description="Backend automation."
        )
        cls.skill_excel = Skill.objects.create(
            user=cls.user, name="Excel", description="Spreadsheets."
        )
        cls.job = Job.objects.create(
            user=cls.user,
            title="Backend Engineer",
            company="Acme",
            started=date(2022, 1, 1),
            description="Python services.",
        )

    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_tier1_conversational_happy_path(self, mock_tailor):
        mock_tailor.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "reason": "direct"},
            {"id": f"job:{self.job.pk}", "reason": "direct"},
        ]
        cv = CV(user_pk=self.user.pk)
        result = cv.ai_tailor_with_fallback("python backend engineer wanted")
        self.assertEqual(result["tier"], "conversational")
        self.assertEqual(result["keywords"], None)
        self.assertEqual(len(result["selection"]), 2)
        # Excel was excluded by the selection.
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})

    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_tier1_failure_falls_through_to_filter(self, mock_tailor, mock_score):
        mock_tailor.side_effect = TimeoutError("model timed out")
        mock_score.return_value = [
            {"id": f"skill:{self.skill_py.pk}", "score": 0.9},
            {"id": f"skill:{self.skill_excel.pk}", "score": 0.05},
            {"id": f"job:{self.job.pk}", "score": 0.8},
        ]
        cv = CV(user_pk=self.user.pk)
        with patch.dict(CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False):
            result = cv.ai_tailor_with_fallback("posting", threshold=0.4)
        self.assertEqual(result["tier"], "filter")
        self.assertEqual(result["selection"], None)
        # Snapshot was restored before filter ran, then filter dropped Excel.
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})

    @patch("jac.cv.jac_llm.extract_job_keywords")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_falls_through_to_keyword_tier(self, mock_tailor, mock_score, mock_extract):
        mock_tailor.side_effect = RuntimeError("boom")
        mock_score.side_effect = RuntimeError("boom")
        mock_extract.return_value = ["python"]
        cv = CV(user_pk=self.user.pk)
        result = cv.ai_tailor_with_fallback("posting")
        self.assertEqual(result["tier"], "keyword")
        self.assertEqual(result["keywords"], ["python"])

    @patch("jac.cv.jac_llm.extract_job_keywords")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_falls_through_to_deterministic_tier(
        self, mock_tailor, mock_score, mock_extract
    ):
        mock_tailor.side_effect = RuntimeError("boom")
        mock_score.side_effect = RuntimeError("boom")
        mock_extract.side_effect = RuntimeError("boom")
        cv = CV(user_pk=self.user.pk)
        result = cv.ai_tailor_with_fallback(
            "Python automation engineer wanted.", language="en"
        )
        self.assertEqual(result["tier"], "deterministic")
        self.assertIsInstance(result["keywords"], list)
        # Substring match on "python" should retain at least the Python skill / job.
        kept = sum(len(v) for v in cv.entries.values())
        self.assertGreaterEqual(kept, 1)

    @patch("jac.cv.jac_llm.extract_job_keywords")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_all_tiers_fail_returns_unfiltered_snapshot(
        self, mock_tailor, mock_score, mock_extract
    ):
        mock_tailor.side_effect = RuntimeError("boom")
        mock_score.side_effect = RuntimeError("boom")
        mock_extract.side_effect = RuntimeError("boom")
        cv = CV(user_pk=self.user.pk)
        before_counts = {k: len(v) for k, v in cv.entries.items()}
        # Posting with zero substring overlap on any of our entries' tokens.
        result = cv.ai_tailor_with_fallback("zzz qqq xxx yyy.", language="en")
        self.assertEqual(result["tier"], "unfiltered")
        self.assertEqual(result["selection"], None)
        self.assertEqual(result["keywords"], None)
        after_counts = {k: len(v) for k, v in cv.entries.items()}
        self.assertEqual(before_counts, after_counts)


# ---------------------------------------------------------------------------
# jac.llm wrappers — JSON parsing + mocked complete()
# ---------------------------------------------------------------------------


class ParseJsonTests(TestCase):
    def test_plain_json_parses(self):
        self.assertEqual(jac_llm._parse_json('{"a": 1}', "ctx"), {"a": 1})

    def test_strips_json_fence(self):
        raw = '```json\n{"a": 1}\n```'
        self.assertEqual(jac_llm._parse_json(raw, "ctx"), {"a": 1})

    def test_strips_bare_fence(self):
        raw = "```\n[1, 2, 3]\n```"
        self.assertEqual(jac_llm._parse_json(raw, "ctx"), [1, 2, 3])

    def test_invalid_json_raises_with_context(self):
        with self.assertRaises(ValueError) as cm:
            jac_llm._parse_json("not json", "my-context")
        self.assertIn("my-context", str(cm.exception))


class LLMWrappersTests(TestCase):
    @patch("jac.llm.complete")
    def test_extract_job_keywords_parses_array(self, mock_complete):
        mock_complete.return_value = '["Python", "Django"]'
        out = jac_llm.extract_job_keywords("posting")
        self.assertEqual(out, ["Python", "Django"])

    @patch("jac.llm.complete")
    def test_extract_job_keywords_uses_default_alias(self, mock_complete):
        mock_complete.return_value = "[]"
        jac_llm.extract_job_keywords("posting")
        self.assertEqual(mock_complete.call_args.kwargs["alias"], "default")

    @patch("jac.llm.complete")
    def test_extract_job_keywords_forwards_user(self, mock_complete):
        mock_complete.return_value = "[]"
        jac_llm.extract_job_keywords("posting", user=42)
        self.assertEqual(mock_complete.call_args.kwargs["user"], 42)

    @patch("jac.llm.complete")
    def test_score_entries_for_job_forwards_user(self, mock_complete):
        mock_complete.return_value = "[]"
        jac_llm.score_entries_for_job("posting", [], user=42)
        self.assertEqual(mock_complete.call_args.kwargs["user"], 42)

    @patch("jac.llm.complete")
    def test_analyze_job_forwards_user(self, mock_complete):
        mock_complete.return_value = "{}"
        jac_llm.analyze_job("posting", user=42)
        self.assertEqual(mock_complete.call_args.kwargs["user"], 42)

    @patch("jac.llm.complete")
    def test_score_entries_with_analysis_forwards_user(self, mock_complete):
        mock_complete.return_value = "[]"
        jac_llm.score_entries_with_analysis("posting", {}, [], user=42)
        self.assertEqual(mock_complete.call_args.kwargs["user"], 42)

    @patch("jac.llm.complete")
    def test_extract_job_keywords_rejects_non_list(self, mock_complete):
        mock_complete.return_value = '{"not": "a list"}'
        with self.assertRaises(ValueError):
            jac_llm.extract_job_keywords("posting")

    @patch("jac.llm.complete")
    def test_extract_job_keywords_coerces_items_to_str(self, mock_complete):
        mock_complete.return_value = "[1, 2, 3]"
        self.assertEqual(jac_llm.extract_job_keywords("posting"), ["1", "2", "3"])

    @patch("jac.llm.complete")
    def test_score_entries_for_job_returns_parsed(self, mock_complete):
        mock_complete.return_value = '[{"id": "skill:1", "score": 0.9}]'
        out = jac_llm.score_entries_for_job(
            "posting", [{"id": "skill:1", "type": "skill", "text": "x"}]
        )
        self.assertEqual(out, [{"id": "skill:1", "score": 0.9}])

    @patch("jac.llm.complete")
    def test_score_entries_for_job_rejects_non_list(self, mock_complete):
        mock_complete.return_value = '{"oops": true}'
        with self.assertRaises(ValueError):
            jac_llm.score_entries_for_job("posting", [])

    @patch("jac.llm.complete")
    def test_analyze_job_returns_dict(self, mock_complete):
        mock_complete.return_value = '{"role_title": "Engineer"}'
        out = jac_llm.analyze_job("posting")
        self.assertEqual(out, {"role_title": "Engineer"})

    @patch("jac.llm.complete")
    def test_analyze_job_rejects_non_dict(self, mock_complete):
        mock_complete.return_value = "[]"
        with self.assertRaises(ValueError):
            jac_llm.analyze_job("posting")

    @patch("jac.llm.complete")
    def test_score_entries_with_analysis_returns_parsed(self, mock_complete):
        mock_complete.return_value = '[{"id": "job:1", "score": 0.7}]'
        out = jac_llm.score_entries_with_analysis(
            "posting", {"must_have": ["x"]}, [{"id": "job:1"}]
        )
        self.assertEqual(out, [{"id": "job:1", "score": 0.7}])

    @patch("jac.llm.complete")
    def test_tailor_cv_conversationally_returns_ranked_selection(self, mock_complete):
        mock_complete.return_value = (
            '[{"id": "skill:1", "reason": "core"}, '
            ' {"id": "job:2", "reason": "direct"}]'
        )
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:2", "type": "job", "text": "Backend Engineer at Acme"},
        ]
        out = jac_llm.tailor_cv_conversationally("posting", entries)
        self.assertEqual(out[0]["id"], "skill:1")
        self.assertEqual(out[1]["id"], "job:2")
        self.assertEqual(out[0]["reason"], "core")

    @patch("jac.llm.complete")
    def test_tailor_cv_conversationally_drops_hallucinated_ids(self, mock_complete):
        mock_complete.return_value = (
            '[{"id": "skill:1", "reason": "real"}, '
            ' {"id": "skill:999", "reason": "made up"}, '
            ' {"id": "job:2", "reason": "real"}]'
        )
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:2", "type": "job", "text": "Backend"},
        ]
        out = jac_llm.tailor_cv_conversationally("posting", entries)
        ids = [item["id"] for item in out]
        self.assertEqual(ids, ["skill:1", "job:2"])

    @patch("jac.llm.complete")
    def test_tailor_cv_conversationally_raises_when_too_few_valid(self, mock_complete):
        mock_complete.return_value = '[{"id": "skill:999", "reason": "made up"}]'
        entries = [
            {"id": "skill:1", "type": "skill", "text": "Python"},
            {"id": "job:2", "type": "job", "text": "Backend"},
        ]
        with self.assertRaises(ValueError):
            jac_llm.tailor_cv_conversationally("posting", entries)

    @patch("jac.llm.complete")
    def test_tailor_cv_conversationally_rejects_non_list(self, mock_complete):
        mock_complete.return_value = '{"not": "a list"}'
        with self.assertRaises(ValueError):
            jac_llm.tailor_cv_conversationally("posting", [])

    @patch("jac.llm.complete")
    def test_tailor_cv_conversationally_forwards_user(self, mock_complete):
        mock_complete.return_value = (
            '[{"id": "a", "reason": "x"}, {"id": "b", "reason": "y"}]'
        )
        entries = [{"id": "a"}, {"id": "b"}]
        jac_llm.tailor_cv_conversationally("posting", entries, user=42)
        self.assertEqual(mock_complete.call_args.kwargs["user"], 42)


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

        job = Job.objects.create(
            user=cls.a,
            title="Engineer",
            company="Co",
            location=loc,
            started=date(2021, 1, 1),
        )
        job.skills.add(py)
        job.domains.add(domain)

        Language.objects.create(user=cls.a, name="German", fluency="native")

        snippet = ResumeSnippet.objects.create(
            user=cls.a, title="Intro", content="Hi.", kind="intro"
        )
        snippet.skills.add(py)
        snippet.domains.add(domain)

    def _round_trip(self):
        """Export A to a temp file and import it into B."""
        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
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

    def test_domains_are_scoped_to_importing_user(self):
        self._round_trip()
        py = Skill.objects.get(user=self.b, name="Python")
        b_domain = Domain.objects.get(user=self.b, name="Backend")
        self.assertIn(b_domain, py.domains.all())
        # A's original domain is untouched / not cross-linked to B.
        self.assertEqual(Domain.objects.filter(name="Backend").count(), 2)
        self.assertEqual(
            Domain.objects.filter(user=self.a, name="Backend").count(), 1
        )

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
        self.assertNotIn("years_of_experience\"", buf.getvalue())
        self.assertIn("years_of_experience_override", buf.getvalue())
