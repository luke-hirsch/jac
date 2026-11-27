"""Model-layer tests: the Mode vocabulary, GenerationRun's executor shape, and
the JobApplication lifecycle (+ the pinned_entries field).

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`
(2026-07-16 single-executor redesign).
"""

import io
import json
import tempfile
from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from jac.career_import import SAMPLE_CAREER, import_career
from jac.models import (
    Certification,
    Education,
    GenerationRun,
    Job,
    JobApplication,
    Language,
    Location,
    Mode,
    Project,
    Skill,
    TransitionError,
    days_of_use,
    normalize_mode,
)

from ._helpers import make_application, make_user


class ModeVocabularyTests(TestCase):
    def test_the_three_modes(self):
        self.assertEqual(set(Mode.values), {"manual", "standard", "high"})

    def test_normalize_passes_valid_modes_through(self):
        for mode in Mode.values:
            self.assertEqual(normalize_mode(mode), mode)

    def test_normalize_coerces_everything_else_to_standard(self):
        # Blank, None, typos, AND the dead vocabularies (grades, old mode names) —
        # nothing maps, everything coerces. Input tolerance, not a compat bridge.
        for raw in ("", None, "instruct", "conversational", "light", "strong", "turbo"):
            self.assertEqual(normalize_mode(raw), Mode.standard, raw)


class GenerationRunModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def test_defaults_are_a_standard_hirschai_run(self):
        run = GenerationRun.objects.create(job_application=self.application)
        self.assertEqual(run.mode, Mode.standard)
        self.assertEqual(run.provider, "ollama")  # HirschAI
        self.assertEqual(run.model, "")  # blank = the executor's own default
        self.assertEqual(run.status, GenerationRun.Status.pending)

    def test_executor_fields_round_trip(self):
        run = GenerationRun.objects.create(
            job_application=self.application,
            mode=Mode.high,
            provider="anthropic",
            model="claude-sonnet-5",
        )
        run.refresh_from_db()
        self.assertEqual(
            (run.mode, run.provider, run.model),
            (Mode.high, "anthropic", "claude-sonnet-5"),
        )

    def test_user_and_posting_derive_from_the_application(self):
        run = GenerationRun.objects.create(job_application=self.application)
        self.assertEqual(run.user, self.user)
        self.assertEqual(run.posting, self.application.posting)


class JobApplicationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def test_pinned_entries_defaults_to_an_empty_list(self):
        application = make_application(self.user)
        self.assertEqual(application.pinned_entries, [])

    def test_legal_transition_stamps_side_effects(self):
        application = make_application(self.user)
        application.apply_transition("approved")
        self.assertEqual(application.status, JobApplication.StatusChoices.approved)
        self.assertIsNotNone(application.approved_at)
        self.assertLessEqual(application.approved_at, timezone.now())

    def test_illegal_transition_raises_and_leaves_the_instance_untouched(self):
        application = make_application(self.user)
        with self.assertRaises(TransitionError):
            application.apply_transition("response")  # draft can't jump to response
        self.assertEqual(application.status, JobApplication.StatusChoices.draft)
        self.assertIsNone(application.responded_at)

    def test_sent_requires_a_known_delivery_method(self):
        application = make_application(self.user)
        application.apply_transition("approved")
        with self.assertRaises(TransitionError):
            application.apply_transition("sent", delivery_method="pigeon")

    def test_where_to_apply_and_the_email_start_empty(self):
        application = make_application(self.user)
        self.assertEqual(application.apply_url, "")
        self.assertEqual(application.apply_note, "")
        self.assertEqual(application.email_body, "")

    def test_the_new_columns_keep_their_default_in_the_database(self):
        """A rollback runs the previous image on this schema: its inserts leave the three columns
        out, so the database has to fill them."""
        for name in ("apply_url", "apply_note", "email_body"):
            with self.subTest(field=name):
                self.assertEqual(JobApplication._meta.get_field(name).db_default, "")


# --- [fullstack]-education-degree ---------------------------------------------------
# ACTIVE guide (activated 2026-08-07, rescoped to `degree_level` alone). Red until the
# field lands.


class EducationDegreeTests(TestCase):
    """`is_degree` is the predicate the whole force-keep hangs off. One ordered field,
    and it means what the entry EARNED — not what it aimed at. That is what makes a
    separate `completed` flag unnecessary: a drop-out earned nothing, so it is `none`,
    and the level it was aiming at stays in the free text where it already lives."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def _edu(self, **kw):
        return Education.objects.create(
            user=self.user,
            institution=kw.pop("institution", "FU Berlin"),
            started=date(2012, 10, 1),
            **kw,
        )

    def test_levels_are_ordered_so_highest_is_a_max(self):
        L = Education.DegreeLevel
        self.assertLess(L.none, L.secondary)
        self.assertLess(L.secondary, L.vocational)
        self.assertLess(L.vocational, L.bachelor)
        self.assertLess(L.bachelor, L.master)
        self.assertLess(L.master, L.doctorate)

    def test_the_default_is_the_conservative_one(self):
        """Every pre-migration row starts at `none`, i.e. claims nothing, until the user
        classifies it by hand."""
        e = self._edu()
        self.assertEqual(e.degree_level, Education.DegreeLevel.none)
        self.assertFalse(e.is_degree)

    def test_any_level_above_none_is_a_degree(self):
        """Abitur counts: with no BSc in the DB it IS the highest formal qualification,
        and a German pay grade would key on it."""
        for level in (
            Education.DegreeLevel.secondary,
            Education.DegreeLevel.vocational,
            Education.DegreeLevel.bachelor,
            Education.DegreeLevel.doctorate,
        ):
            with self.subTest(level=level):
                self.assertTrue(self._edu(degree_level=level).is_degree)

    def test_free_text_never_overrides_the_level(self):
        """The drop-out case, exactly as it sits in the real DB: the aborted Master is
        prose in `field_of_study`, and the level says what was actually earned."""
        e = self._edu(
            degree="Drop Out",
            field_of_study="Physics (Master)",
            degree_level=Education.DegreeLevel.none,
        )
        self.assertFalse(e.is_degree)


TODAY = date(2026, 11, 21)


class DaysOfUseTests(SimpleTestCase):
    """The rule itself, without a database: spans in, days out."""

    def test_overlaps_count_once(self):
        spans = [
            (date(2015, 1, 1), date(2020, 1, 1)),
            (date(2012, 1, 1), date(2016, 1, 1)),
        ]
        self.assertEqual(
            days_of_use(None, spans, TODAY), (date(2020, 1, 1) - date(2012, 1, 1)).days
        )

    def test_a_gap_between_entries_doesnt_count(self):
        spans = [
            (date(2010, 1, 1), date(2011, 1, 1)),
            (date(2020, 1, 1), date(2021, 1, 1)),
        ]
        self.assertEqual(days_of_use(None, spans, TODAY), 365 + 366)

    def test_an_open_end_runs_to_today_and_a_future_end_stops_there(self):
        for ended in (None, date(2030, 1, 1)):
            with self.subTest(ended=ended):
                spans = [(date(2026, 11, 1), ended)]
                self.assertEqual(days_of_use(None, spans, TODAY), 20)

    def test_nothing_to_count_is_none(self):
        self.assertIsNone(days_of_use(None, [], TODAY))
        # A job that hasn't begun, and one that ends before it starts (a data error).
        spans = [(date(2027, 1, 1), None), (date(2020, 1, 1), date(2019, 1, 1))]
        self.assertIsNone(days_of_use(None, spans, TODAY))

    def test_first_used_alone_runs_to_today(self):
        self.assertEqual(
            days_of_use(date(2025, 11, 21), [], TODAY),
            (TODAY - date(2025, 11, 21)).days,
        )

    def test_first_used_cuts_what_came_before_it(self):
        spans = [(date(2010, 1, 1), date(2020, 1, 1))]
        self.assertEqual(
            days_of_use(date(2018, 1, 1), spans, TODAY),
            (date(2020, 1, 1) - date(2018, 1, 1)).days,
        )
        # A span over before the skill was picked up adds nothing.
        self.assertEqual(
            days_of_use(
                date(2026, 1, 1), [(date(2010, 1, 1), date(2012, 1, 1))], TODAY
            ),
            (TODAY - date(2026, 1, 1)).days,
        )

    def test_first_used_bridges_to_the_first_linked_entry_only(self):
        """Python since 2015, a job 2015 to 2018, a project since 2024: the years in between
        don't count."""
        spans = [(date(2015, 9, 1), date(2018, 9, 1)), (date(2024, 9, 1), None)]
        days = days_of_use(date(2015, 9, 1), spans, TODAY)
        self.assertEqual(days // 365, 5)


class SkillYearsTests(TestCase):
    """The computed years, as every reader gets them from `Skill.objects`: the spans of the
    linked jobs, projects and educations, from `first_used` on; the user's override wins."""

    def setUp(self):
        self.user = make_user()

    def _years(self, skill):
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            return Skill.objects.get(pk=skill.pk).years_of_experience

    def _job(self, started, ended=None, *skills):
        job = Job.objects.create(
            user=self.user, title="Dev", company="Acme", started=started, ended=ended
        )
        job.skills.add(*skills)
        return job

    def test_first_used_wins_over_an_earlier_linked_job(self):
        django = Skill.objects.create(
            user=self.user, name="Django", first_used=date(2020, 2, 1)
        )
        self._job(date(2016, 4, 15), None, django)
        # It showed 10, counted from the job.
        self.assertEqual(self._years(django), 6)

    def test_first_used_wins_over_a_later_linked_job(self):
        python = Skill.objects.create(
            user=self.user, name="Python", first_used=date(2015, 9, 1)
        )
        self._job(date(2018, 4, 15), None, python)
        self.assertEqual(self._years(python), 11)

    def test_a_job_that_ended_stops_counting(self):
        """C++ from a job 2015 to mid-2020 showed 11 years: the end date was ignored."""
        cpp = Skill.objects.create(user=self.user, name="C++")
        self._job(date(2015, 1, 1), date(2020, 6, 30), cpp)
        self.assertEqual(self._years(cpp), 5)

    def test_an_education_counts_and_an_overlap_counts_once(self):
        cpp = Skill.objects.create(user=self.user, name="C++")
        self._job(date(2015, 1, 1), date(2020, 6, 30), cpp)
        edu = Education.objects.create(
            user=self.user,
            institution="TU",
            started=date(2012, 10, 1),
            ended=date(2016, 9, 30),
        )
        edu.skills.add(cpp)
        # 2012-10 to 2020-06, not 5.5 + 4.
        self.assertEqual(self._years(cpp), 7)

    def test_a_project_counts_and_one_without_a_start_doesnt(self):
        llm = Skill.objects.create(user=self.user, name="Local LLMs")
        Project.objects.create(
            user=self.user, name="JAC", started=date(2025, 6, 1)
        ).skills.add(llm)
        Project.objects.create(user=self.user, name="Undated").skills.add(llm)
        self.assertEqual(self._years(llm), 1)

    def test_months_of_use_are_zero_years_not_none(self):
        llm = Skill.objects.create(user=self.user, name="Local LLMs")
        Project.objects.create(
            user=self.user, name="JAC", started=date(2026, 6, 1)
        ).skills.add(llm)
        self.assertEqual(self._years(llm), 0)

    def test_a_certification_adds_no_years(self):
        aws = Skill.objects.create(user=self.user, name="AWS")
        Certification.objects.create(
            user=self.user, name="SAA", issuer="AWS", issued_on=date(2015, 1, 1)
        ).skills.add(aws)
        self.assertIsNone(self._years(aws))

    def test_the_users_own_number_wins(self):
        piano = Skill.objects.create(
            user=self.user,
            name="Piano",
            first_used=date(1994, 8, 1),
            years_of_experience_override=20,
        )
        self.assertEqual(self._years(piano), 20)


class SeedSystemDefaultsTests(TestCase):
    def test_the_site_follows_the_base_domain(self):
        """allauth puts the Site in every mail: a fresh database says "example.com" until something
        fixes it. That something belongs in the deploy step, not in a runbook."""
        with (
            tempfile.TemporaryDirectory() as media,
            override_settings(MEDIA_ROOT=media, BASE_DOMAIN="example.org"),
        ):
            call_command("seed_system_defaults", stdout=io.StringIO())
        site = Site.objects.get(pk=settings.SITE_ID)
        self.assertEqual(site.domain, "example.org")
        self.assertEqual(site.name, "jac")


class CvImportCommandTests(TestCase):
    """The command stays a thin wrapper over `jac.career_import`, with its old error shape."""

    def setUp(self):
        self.user = make_user()

    def _import(self, data, *flags):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            json.dump(data, fh)
            fh.flush()
            call_command(
                "cv_import",
                "--file",
                fh.name,
                "--user",
                str(self.user.pk),
                *flags,
                stdout=io.StringIO(),
            )

    def test_imports_the_sample_shape(self):
        self._import(json.loads(SAMPLE_CAREER.read_text(encoding="utf-8")))
        self.assertEqual(Job.objects.filter(user=self.user).count(), 2)
        self.assertEqual(Skill.objects.filter(user=self.user).count(), 8)

    def test_an_unknown_reference_is_a_command_error_and_writes_nothing(self):
        job = {
            "title": "x",
            "company": "y",
            "started": "2020-01-01",
            "skills": ["Nope"],
        }
        data = {"jobs": [job]}
        with self.assertRaisesMessage(CommandError, "Unknown skill"):
            self._import(data)
        self.assertFalse(Job.objects.filter(user=self.user).exists())

    def test_replace_clears_the_career_first(self):
        Language.objects.create(user=self.user, name="French")
        self._import({"languages": [{"name": "German"}]}, "--replace")
        self.assertEqual(
            list(
                Language.objects.filter(user=self.user).values_list("name", flat=True)
            ),
            ["German"],
        )

    def test_dry_run_writes_nothing(self):
        self._import({"languages": [{"name": "German"}]}, "--dry-run")
        self.assertFalse(Language.objects.filter(user=self.user).exists())

    def test_a_repeated_city_resolves_instead_of_crashing(self):
        """Nothing makes a city unique, and a user can have the same one twice."""
        for country in ("Germany", "USA"):
            Location.objects.create(user=self.user, city="Berlin", country=country)
        self._import(
            {
                "jobs": [
                    {
                        "title": "x",
                        "company": "y",
                        "started": "2020-01-01",
                        "location": "Berlin",
                    }
                ]
            }
        )
        self.assertEqual(Job.objects.get(user=self.user).location.city, "Berlin")


class CvExportCommandTests(TestCase):
    """The command stays a thin wrapper over `jac.career_export` (guide 07), with its old output."""

    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        import_career(self.user, json.loads(SAMPLE_CAREER.read_text(encoding="utf-8")))

    def _export(self, user) -> str:
        out = io.StringIO()
        call_command("cv_export", "--user", str(user.pk), stdout=out)
        return out.getvalue()

    def test_round_trips_through_cv_import(self):
        # Green before and after guide 07: it guards moving the sections out of the command.
        exported = self._export(self.user)
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            fh.write(exported)
            fh.flush()
            call_command(
                "cv_import",
                "--file",
                fh.name,
                "--user",
                str(self.other.pk),
                stdout=io.StringIO(),
            )
        self.assertEqual(json.loads(self._export(self.other)), json.loads(exported))

    def test_is_a_wrapper_over_export_career(self):
        # Imported here, not at the top: until the module exists, only this test is red.
        from jac.career_export import export_career

        expected = json.loads(json.dumps(export_career(self.user), default=str))
        self.assertEqual(json.loads(self._export(self.user)), expected)
