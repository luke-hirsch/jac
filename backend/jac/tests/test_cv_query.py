"""CV query layer — loading, flattening, relationship edges, apply_selection."""

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

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
