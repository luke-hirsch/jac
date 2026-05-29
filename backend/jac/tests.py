from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

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
    Skill,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class DomainModelTests(TestCase):
    def test_str_returns_name(self):
        self.assertEqual(str(Domain.objects.create(name="Fintech")), "Fintech")


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
        # Earliest is the 2012 job.
        self.assertIsNotNone(skill.years_of_experience)
        if skill.years_of_experience is not None:
            self.assertGreaterEqual(int(skill.years_of_experience), 13)


# ---------------------------------------------------------------------------
# CV query/filter tests
# ---------------------------------------------------------------------------


class CVQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="lukas")
        cls.other = User.objects.create(username="someone_else")

        cls.dom_python = Domain.objects.create(name="Python")
        cls.dom_data = Domain.objects.create(name="Data")
        cls.dom_unused = Domain.objects.create(name="Robotics")

        cls.location = Location.objects.create(city="Berlin")

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
        cls.dom = Domain.objects.create(name="Healthcare")

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
        with patch.dict(
            CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False
        ):
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
        mock_extract.assert_called_once_with("posting", llm="default", user=self.user.pk)

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
        with patch.dict(
            CV._MIN_PER_SECTION, {"skills": 0, "jobs": 0}, clear=False
        ):
            result = cv.ai_tailor_with_fallback("posting", threshold=0.4)
        self.assertEqual(result["tier"], "filter")
        self.assertEqual(result["selection"], None)
        # Snapshot was restored before filter ran, then filter dropped Excel.
        self.assertEqual({s.name for s in cv.entries["skills"]}, {"Python"})

    @patch("jac.cv.jac_llm.extract_job_keywords")
    @patch("jac.cv.jac_llm.score_entries_for_job")
    @patch("jac.cv.jac_llm.tailor_cv_conversationally")
    def test_falls_through_to_keyword_tier(
        self, mock_tailor, mock_score, mock_extract
    ):
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
