"""Async generation — the Celery task body + CV-selection serializer.

The pipeline collaborators (`CV`, `CoverLetter`, `AddressExtract`) are patched, so no
Ollama/LLM is needed; we assert the task's lifecycle, result shape, and the fill-if-empty
hand-off into the owning `JobApplication`. Runs under an in-memory channel layer (no Redis).
"""

from datetime import date
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from llm_connector.base import LLMTransportError

from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.models import GenerationRun, Job, JobPostAddress
from jac.tasks import generate_run

from ._helpers import _application, _muted

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

_LETTER = {
    "language": "en",
    "subject": "Application",
    "salutation": "Dear team,",
    "body": "…",
    "sender": {"name": "Ada Lovelace", "city": "Berlin"},
    "recipient": {"company": "ACME", "city": "Duckburg"},
    "date": "2026-07-09",
    "closing": "Kind regards,",
    "ai_share": 0.1,
    "grounding": {"count": None, "claims": []},
    "personal_paragraph": "",
    "personal_paragraph_is_stub": False,
    "personal_paragraph_sources": [],
    "personal_paragraph_grounding": {"count": None, "claims": []},
    "text": "…full text…",
}

# The furniture slice of _LETTER that auto-fill copies into JobApplication.letter_meta.
_LETTER_META = {
    k: _LETTER[k]
    for k in ("language", "subject", "salutation", "date", "closing", "sender", "recipient")
}


class SerializeCvSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ser_user", password="pass")
        cls.job = Job.objects.create(
            user=cls.user, title="Senior Dev", company="ACME", started=date(2021, 1, 1)
        )

    def test_shapes_kept_entries_with_score(self):
        cv = CV(user_pk=self.user.pk)
        self.job.relevance_score = 0.88
        cv.entries = {
            "jobs": [self.job], "skills": [], "educations": [],
            "certifications": [], "projects": [], "languages": [],
        }
        out = serialize_cv_selection(cv)
        self.assertEqual(len(out["jobs"]), 1)
        row = out["jobs"][0]
        self.assertEqual(row["id"], f"job:{self.job.pk}")
        self.assertIn("Senior Dev", row["label"])
        self.assertEqual(row["relevance_score"], 0.88)
        self.assertEqual(out["skills"], [])


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class GenerateRunTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="task_user", password="pass")

    def _run(self, **application_kwargs):
        app = _application(self.user, **application_kwargs)
        return GenerationRun.objects.create(job_application=app, alias="default")

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_happy_path_writes_result_and_marks_done(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {"title": "Dev", "language": "en"}
        mock_letter.return_value.build.return_value = _LETTER

        run = self._run()
        generate_run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertIn("cover_letter", run.result)
        self.assertIn("cv", run.result)
        self.assertEqual(run.result["cover_letter"]["ai_share"], 0.1)
        # The extracted title/language land on the posting.
        posting = run.job_application.posting
        posting.refresh_from_db()
        self.assertEqual(posting.title, "Dev")

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_done_autofills_empty_application(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {}
        mock_letter.return_value.build.return_value = _LETTER

        run = self._run()
        generate_run(run.pk)

        run.refresh_from_db()
        app = run.job_application
        app.refresh_from_db()
        # Body-only contract: the editable cover_letter gets the letter *body* (plus personal
        # paragraph, when present); the furniture lands in letter_meta instead of the full text.
        self.assertEqual(app.cover_letter, _LETTER["body"])
        self.assertEqual(app.letter_meta, _LETTER_META)
        self.assertEqual(app.cv_content, run.result["cv"])

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_run_persists_extracted_address(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        """The extracted recipient address is saved on the posting (1:1) — updated on a
        re-run, never duplicated — so the SPA can prefill the letter recipient."""
        mock_cv.return_value.filter_cv.return_value = {}
        mock_letter.return_value.build.return_value = _LETTER
        mock_extract.return_value.extract.return_value = {
            "title": "Dev",
            "language": "en",
            "company": "ACME",
            "city": "Duckburg",
        }

        run = self._run()
        generate_run(run.pk)

        posting = run.job_application.posting
        addr = JobPostAddress.objects.get(job_posting=posting)
        self.assertEqual(addr.company, "ACME")
        self.assertEqual(addr.city, "Duckburg")

        mock_extract.return_value.extract.return_value = {"company": "ACME GmbH"}
        second = GenerationRun.objects.create(
            job_application=run.job_application, alias="default"
        )
        generate_run(second.pk)

        self.assertEqual(
            JobPostAddress.objects.filter(job_posting=posting).count(), 1
        )
        addr.refresh_from_db()
        self.assertEqual(addr.company, "ACME GmbH")

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_run_fills_application_deadline(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        """The extracted `deadline` line is parsed to a real date and written to the owning
        application (foundation lifecycle guide) — same fill-if-empty hand-off as cv/letter."""
        mock_cv.return_value.filter_cv.return_value = {}
        mock_letter.return_value.build.return_value = _LETTER
        mock_extract.return_value.extract.return_value = {
            "title": "Dev",
            "deadline": "2026-08-01",
        }

        run = self._run()
        generate_run(run.pk)

        app = run.job_application
        app.refresh_from_db()
        self.assertEqual(app.deadline, date(2026, 8, 1))

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_run_does_not_clobber_manual_deadline(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        """A deadline the user already set is never overwritten by a later run's extraction."""
        mock_cv.return_value.filter_cv.return_value = {}
        mock_letter.return_value.build.return_value = _LETTER
        mock_extract.return_value.extract.return_value = {"deadline": "2026-08-01"}

        run = self._run(deadline=date(2026, 6, 30))
        generate_run(run.pk)

        app = run.job_application
        app.refresh_from_db()
        self.assertEqual(app.deadline, date(2026, 6, 30))

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_done_never_clobbers_edited_application(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {}
        mock_letter.return_value.build.return_value = _LETTER

        run = self._run(cover_letter="my hand-written letter")
        generate_run(run.pk)

        app = run.job_application
        app.refresh_from_db()
        self.assertEqual(app.cover_letter, "my hand-written letter")
        self.assertEqual(app.cv_content, {})

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_failure_marks_failed_with_error(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {}
        mock_letter.return_value.build.side_effect = RuntimeError("boom")

        run = self._run()
        with _muted():
            generate_run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("boom", run.error)

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_soft_time_limit_reads_as_timeout(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {}
        mock_letter.return_value.build.side_effect = SoftTimeLimitExceeded()

        run = self._run()
        with _muted():
            generate_run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("timed out", run.error)

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_transport_error_hints_at_the_model_server(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {}
        mock_letter.return_value.build.side_effect = LLMTransportError(
            "could not reach http://localhost:11434/api/chat: timed out"
        )

        run = self._run()
        with _muted():
            generate_run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("could not reach", run.error)
        self.assertIn("model server is running", run.error)

    @patch("jac.tasks.CV")
    def test_terminal_run_is_not_claimed(self, mock_cv):
        """A run cancelled while still queued (or one already handled) is skipped —
        the stale Celery delivery must not resurrect it."""
        app = _application(self.user)
        cancelled = GenerationRun.objects.create(
            job_application=app,
            status=GenerationRun.Status.failed,
            error="cancelled by user",
        )
        generate_run(cancelled.pk)

        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, "failed")
        self.assertEqual(cancelled.error, "cancelled by user")
        mock_cv.assert_not_called()

    @patch("jac.tasks.get_alias_strength", return_value="light")
    @patch("jac.tasks.AddressExtract")
    @patch("jac.tasks.CoverLetter")
    @patch("jac.tasks.CV")
    def test_cancel_during_run_wins_over_done(
        self, mock_cv, mock_letter, mock_extract, _mock_strength
    ):
        """A cancel landing mid-pipeline keeps the run failed: the finishing task must
        not overwrite it with `done`, and must not auto-fill the application."""
        mock_cv.return_value.filter_cv.return_value = {}
        mock_extract.return_value.extract.return_value = {}
        run = self._run()

        def cancel_then_finish():
            GenerationRun.objects.filter(pk=run.pk).update(
                status=GenerationRun.Status.failed, error="cancelled by user"
            )
            return _LETTER

        mock_letter.return_value.build.side_effect = cancel_then_finish
        generate_run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error, "cancelled by user")
        self.assertIsNone(run.result)
        app = run.job_application
        app.refresh_from_db()
        self.assertEqual(app.cover_letter, "")
        self.assertEqual(app.cv_content, {})
