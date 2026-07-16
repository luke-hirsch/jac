"""Model-layer tests: the Mode vocabulary, GenerationRun's executor shape, and
the JobApplication lifecycle (+ the pinned_entries field).

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`
(2026-07-16 single-executor redesign).
"""

from django.test import TestCase
from django.utils import timezone

from jac.models import (
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
