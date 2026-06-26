"""Async generation — REST surface (create/retrieve/list, scoping, validation).

Red until `[backend]-generation-async-plumbing` lands the `GenerationRun` model, the
`GenerationRunViewSet`, and its serializers. The Celery enqueue is patched out — these tests
cover the HTTP contract, not the worker (see test_generation_task.py for the task body).
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from jac.models import GenerationRun, JobPosting


class GenerationRunModelTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="gen_model", password="pass")

    def test_defaults_to_pending(self):
        run = GenerationRun.objects.create(user=self.user, posting_text="x")
        self.assertEqual(run.status, "pending")
        self.assertEqual(run.result, None)
        self.assertEqual(run.alias, "default")


class GenerationRunCreateTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="gen_create", password="pass")

    @patch("jac.views.generate_run")
    def test_create_persists_run_and_posting_and_enqueues(self, mock_task):
        self.client.force_login(self.user)
        r = self.client.post(
            "/api/jac/generations/",
            {"posting_text": "We need a backend dev.", "alias": "default"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "pending")
        run = GenerationRun.objects.get(pk=r.data["id"])
        self.assertEqual(run.user, self.user)
        self.assertIsNotNone(run.job_posting_id)
        self.assertTrue(JobPosting.objects.filter(pk=run.job_posting_id).exists())
        mock_task.delay.assert_called_once_with(run.pk)

    @patch("jac.views.generate_run")
    def test_unknown_grade_is_coerced_to_light(self, _mock_task):
        self.client.force_login(self.user)
        with self.assertLogs(level="WARNING") as logs:
            r = self.client.post(
                "/api/jac/generations/",
                {"posting_text": "x", "grade": "nonsense"},
                format="json",
            )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(GenerationRun.objects.get(pk=r.data["id"]).grade, "light")
        self.assertTrue(any("Invalid grade: nonsense" in m for m in logs.output))


class GenerationRunReadTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="gen_alice", password="pass")
        cls.bob = User.objects.create_user(username="gen_bob", password="pass")
        cls.gen = GenerationRun.objects.create(
            user=cls.alice,
            posting_text="x",
            status=GenerationRun.Status.done,
            stage="done",
            result={"meta": {"grade": "light"}, "cv": {}, "cover_letter": {}},
        )

    def test_retrieve_returns_snapshot_with_result(self):
        self.client.force_login(self.alice)
        r = self.client.get(f"/api/jac/generations/{self.gen.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "done")
        self.assertEqual(r.data["result"]["meta"]["grade"], "light")

    def test_other_user_cannot_read(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/jac/generations/{self.gen.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_list_is_user_scoped(self):
        self.client.force_login(self.bob)
        r = self.client.get("/api/jac/generations/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)
