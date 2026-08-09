"""Model-layer tests: the Mode vocabulary, GenerationRun's executor shape, and
the JobApplication lifecycle (+ the pinned_entries field).

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`
(2026-07-16 single-executor redesign).
"""

from datetime import date

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
