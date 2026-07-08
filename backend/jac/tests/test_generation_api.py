"""Async generation — REST surface (create/retrieve/list, scoping, validation).

Application-centric: a run is created against one of the user's `JobApplication`s
(`job_application` pk); the posting comes from the application, ownership is derived through
it. The Celery enqueue is patched out — these tests cover the HTTP contract, not the worker
(see test_generation_task.py for the task body).
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from jac.models import GenerationRun
from jac.tasks import GENERATION_EXPIRES_S

from ._helpers import _application, _muted


class GenerationRunModelTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="gen_model", password="pass")
        cls.app = _application(cls.user)

    def test_defaults_to_pending(self):
        run = GenerationRun.objects.create(job_application=self.app)
        self.assertEqual(run.status, "pending")
        self.assertEqual(run.result, None)
        self.assertEqual(run.alias, "default")

    def test_user_and_posting_derive_through_application(self):
        run = GenerationRun.objects.create(job_application=self.app)
        self.assertEqual(run.user, self.user)
        self.assertEqual(run.posting, self.app.posting)


class GenerationRunCreateTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="gen_create", password="pass")
        cls.app = _application(cls.user, posting_text="We need a backend dev.")

    @patch("jac.views.generate_run")
    def test_create_attaches_run_and_enqueues(self, mock_task):
        mock_task.apply_async.return_value.id = "task-abc"
        self.client.force_login(self.user)
        r = self.client.post(
            "/api/jac/generations/",
            {"job_application": self.app.pk, "alias": "default"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "pending")
        run = GenerationRun.objects.get(pk=r.data["id"])
        self.assertEqual(run.job_application_id, self.app.pk)
        # Enqueued with an expiry (a task nobody picks up must not fire hours later)
        # and the task id persisted so cancel() can revoke it.
        mock_task.apply_async.assert_called_once_with(
            args=[run.pk], expires=GENERATION_EXPIRES_S
        )
        self.assertEqual(run.task_id, "task-abc")

    @patch("jac.views.generate_run")
    def test_cannot_create_for_another_users_application(self, mock_task):
        other = User.objects.create_user(username="gen_other", password="pass")
        other_app = _application(other)
        self.client.force_login(self.user)
        r = self.client.post(
            "/api/jac/generations/",
            {"job_application": other_app.pk},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        mock_task.apply_async.assert_not_called()

    @patch("jac.views.generate_run")
    def test_omitted_grade_means_autodetect(self, _mock_task):
        _mock_task.apply_async.return_value.id = "task-1"
        self.client.force_login(self.user)
        r = self.client.post(
            "/api/jac/generations/",
            {"job_application": self.app.pk},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        # Blank grade → the task derives it from the alias strength.
        self.assertEqual(GenerationRun.objects.get(pk=r.data["id"]).grade, "")

    @patch("jac.views.generate_run")
    def test_unknown_grade_is_coerced_to_light(self, _mock_task):
        _mock_task.apply_async.return_value.id = "task-2"
        self.client.force_login(self.user)
        with self.assertLogs(level="WARNING") as logs:
            r = self.client.post(
                "/api/jac/generations/",
                {"job_application": self.app.pk, "grade": "nonsense"},
                format="json",
            )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(GenerationRun.objects.get(pk=r.data["id"]).grade, "light")
        # normalize_grade returns the Grade enum member, so the coerced value's repr in the
        # message is `Grade.light`, not `'light'` — assert on the stable offending-value part.
        self.assertTrue(
            any("Invalid grade 'nonsense' coerced to" in m for m in logs.output)
        )


class GenerationRunReadTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="gen_alice", password="pass")
        cls.bob = User.objects.create_user(username="gen_bob", password="pass")
        cls.gen = GenerationRun.objects.create(
            job_application=_application(cls.alice, title="Backend dev"),
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
        self.assertEqual(r.data["posting_title"], "Backend dev")

    def test_other_user_cannot_read(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/jac/generations/{self.gen.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_list_is_user_scoped(self):
        self.client.force_login(self.bob)
        r = self.client.get("/api/jac/generations/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)


@patch("jac.views.publish_event")
@patch("jac.views.celery_current_app")
class GenerationRunCancelTests(APITestCase):
    """POST /generations/<pk>/cancel/ — revoke the task, mark the run failed, publish
    the terminal event. Idempotent on finished runs; resilient to a dead broker."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="gen_cancel", password="pass")
        cls.app = _application(cls.user)

    def _cancel(self, run):
        return self.client.post(f"/api/jac/generations/{run.pk}/cancel/")

    def test_cancel_pending_run_revokes_and_fails_it(self, mock_celery, mock_publish):
        run = GenerationRun.objects.create(
            job_application=self.app, task_id="task-xyz"
        )
        self.client.force_login(self.user)
        r = self._cancel(run)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "failed")
        self.assertEqual(r.data["error"], "cancelled by user")
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        mock_celery.control.revoke.assert_called_once_with(
            "task-xyz", terminate=True
        )
        event = mock_publish.call_args.args[1]
        self.assertEqual(event["event"], "failed")
        self.assertEqual(event["error"], "cancelled by user")

    def test_cancel_running_run_also_works(self, mock_celery, _mock_publish):
        run = GenerationRun.objects.create(
            job_application=self.app,
            task_id="task-run",
            status=GenerationRun.Status.running,
        )
        self.client.force_login(self.user)
        r = self._cancel(run)
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        mock_celery.control.revoke.assert_called_once()

    def test_cancel_finished_run_is_a_noop(self, mock_celery, mock_publish):
        run = GenerationRun.objects.create(
            job_application=self.app,
            status=GenerationRun.Status.done,
            result={"cv": {}},
        )
        self.client.force_login(self.user)
        r = self._cancel(run)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "done")
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        mock_celery.control.revoke.assert_not_called()
        mock_publish.assert_not_called()

    def test_cancel_without_task_id_still_fails_the_run(
        self, mock_celery, _mock_publish
    ):
        run = GenerationRun.objects.create(job_application=self.app)  # task_id ""
        self.client.force_login(self.user)
        r = self._cancel(run)
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        mock_celery.control.revoke.assert_not_called()

    def test_broker_failure_does_not_block_the_cancel(
        self, mock_celery, _mock_publish
    ):
        mock_celery.control.revoke.side_effect = OSError("broker down")
        run = GenerationRun.objects.create(
            job_application=self.app, task_id="task-dead"
        )
        self.client.force_login(self.user)
        with _muted():  # the revoke failure is logged deliberately
            r = self._cancel(run)
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")

    def test_other_user_cannot_cancel(self, mock_celery, _mock_publish):
        other = User.objects.create_user(username="gen_intruder", password="pass")
        run = GenerationRun.objects.create(job_application=self.app)
        self.client.force_login(other)
        r = self._cancel(run)
        self.assertEqual(r.status_code, 404)
        run.refresh_from_db()
        self.assertEqual(run.status, "pending")
        mock_celery.control.revoke.assert_not_called()
