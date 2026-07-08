"""Async generation — the Celery task body + CV-selection serializer.

The pipeline collaborators (`CV`, `CoverLetter`, `AddressExtract`) are patched, so no
Ollama/LLM is needed; we assert the task's lifecycle, result shape, and the fill-if-empty
hand-off into the owning `JobApplication`. Runs under an in-memory channel layer (no Redis).
"""

from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.models import GenerationRun, Job
from jac.tasks import generate_run

from ._helpers import _application, _muted

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

_LETTER = {
    "language": "en",
    "subject": "Application",
    "body": "…",
    "ai_share": 0.1,
    "grounding": {"count": None, "claims": []},
    "personal_paragraph": "",
    "personal_paragraph_is_stub": False,
    "personal_paragraph_sources": [],
    "personal_paragraph_grounding": {"count": None, "claims": []},
    "text": "…full text…",
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
        self.assertEqual(app.cover_letter, _LETTER["text"])
        self.assertEqual(app.cv_content, run.result["cv"])

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
