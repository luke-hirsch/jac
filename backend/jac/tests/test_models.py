"""Model-layer tests: __str__, skill years-of-experience, favourite cap."""

from datetime import date

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase

from jac.models import Domain, Education, Job, Project, Skill


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
