"""Async generation — the Celery task body + CV-selection serializer.

Red until `[backend]-generation-pipeline` lands `jac.generation_result.serialize_cv_selection`
and swaps the stub `generate_run` body for the real pipeline. The pipeline collaborators
(`CV`, `CoverLetter`, `AddressExtract`) are patched, so no Ollama/LLM is needed; we assert the
task's lifecycle + result shape. Runs under an in-memory channel layer (no Redis).
"""

from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.models import GenerationRun, Job, JobPosting
from jac.tasks import generate_run

from ._helpers import _muted

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

    def _run(self):
        jp = JobPosting.objects.create(user=self.user, posting_text="x")
        return GenerationRun.objects.create(
            user=self.user, job_posting=jp, posting_text="We need a dev.", alias="default"
        )

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
