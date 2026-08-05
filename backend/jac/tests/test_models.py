"""Model-layer tests: the Mode vocabulary, GenerationRun's executor shape, and
the JobApplication lifecycle (+ the pinned_entries field).

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`
(2026-07-16 single-executor redesign).
"""

from datetime import date
from unittest import skip

from django.test import TestCase
from django.utils import timezone

from jac.models import (
    Education,
    GenerationRun,
    JobApplication,
    Mode,
    TransitionError,
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


# --- [fullstack]-education-degree ---------------------------------------------------
# SKIP-MARKED: not the active guide. Step 0 of that guide: drop the @skip decorator.


@skip("[fullstack]-education-degree — step 0: unskip")
class EducationDegreeTests(TestCase):
    """`is_degree` is the predicate the whole force-keep hangs off: an ordered level so
    "highest" is a max(), plus a `completed` flag so an unfinished study period is content
    rather than a qualification."""

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
        self.assertLess(L.none, L.vocational)
        self.assertLess(L.vocational, L.bachelor)
        self.assertLess(L.bachelor, L.master)
        self.assertLess(L.master, L.doctorate)

    def test_defaults_are_the_conservative_ones(self):
        e = self._edu()
        self.assertEqual(e.degree_level, Education.DegreeLevel.none)
        self.assertTrue(e.completed)  # most education entries ARE finished
        self.assertFalse(e.is_degree)  # …but "finished nothing" is not a degree

    def test_a_finished_degree_is_a_degree(self):
        e = self._edu(degree_level=Education.DegreeLevel.bachelor, completed=True)
        self.assertTrue(e.is_degree)

    def test_an_unfinished_master_is_not_a_degree(self):
        """The drop-out case: real experience, ranked like any other entry, never the
        candidate's highest qualification."""
        e = self._edu(degree_level=Education.DegreeLevel.master, completed=False)
        self.assertFalse(e.is_degree)
