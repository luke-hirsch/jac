"""Short AI jobs: the status row a page polls, and the worker rules around it. Same rules as a
generation run: safe to run twice, a crash loop ends, the model server being gone is a stage."""

from unittest.mock import patch

from django.contrib.auth.models import User
from kombu.exceptions import OperationalError
from rest_framework.test import APITestCase

from llm_connector.base import WAITING_STAGE, LLMTransportError
from llm_connector.client import _compute_wait
from llm_connector.jobs import AiJobFailed, ai_job, jobs_ahead, start_ai_job
from llm_connector.models import AiJob
from llm_connector.tasks import MAX_JOB_ATTEMPTS, run_ai_job

from ._helpers import _muted

CALLS = []


@ai_job("test_ok")
def _ok(job):
    CALLS.append(job.payload)
    return {"answer": 42}


@ai_job("test_refused")
def _refused(job):
    raise AiJobFailed("You may read this.")


@ai_job("test_boom")
def _boom(job):
    raise RuntimeError("HTTP 500 at http://10.0.0.9:11434/api/chat")


@ai_job("test_down", wait_s=0)
def _down(job):
    raise LLMTransportError("could not reach http://10.0.0.9:11434")


@ai_job("test_waits")
def _waits(job):
    wait = _compute_wait.get()
    wait.on_wait(5.0, "down")
    seen = AiJob.objects.get(pk=job.pk).stage
    wait.on_resume()
    return {"stage_while_waiting": seen}


class StartAiJobTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="alice")

    def test_creates_the_row_and_hands_it_to_the_broker(self):
        with patch("llm_connector.tasks.run_ai_job") as task:
            job = start_ai_job("test_ok", user=self.user, payload={"a": 1})
        self.assertEqual(job.status, AiJob.Status.pending)
        self.assertEqual(task.apply_async.call_args.kwargs["args"], [str(job.pk)])

    def test_the_input_stays_out_of_the_workers_log_lines(self):
        with patch("llm_connector.tasks.run_ai_job") as task:
            start_ai_job("test_ok", user=self.user, payload={"text": "my letter"})
        self.assertNotIn("my letter", task.apply_async.call_args.kwargs["argsrepr"])

    def test_a_broker_that_refuses_fails_the_job_with_a_message(self):
        with (
            patch("llm_connector.tasks.run_ai_job") as task,
            self.assertLogs("llm_connector.jobs", level="ERROR"),
        ):
            task.apply_async.side_effect = OperationalError("connection refused")
            job = start_ai_job("test_ok", user=self.user, payload={"text": "x"})
        self.assertEqual(job.status, AiJob.Status.failed)
        self.assertEqual(job.payload, {})
        self.assertNotIn("refused", job.error)


class RunAiJobTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="alice")
        CALLS.clear()

    def _job(self, kind="test_ok", **fields):
        return AiJob.objects.create(kind=kind, user=self.user, **fields)

    def test_a_job_finishes_and_drops_its_input(self):
        job = self._job(payload={"text": "my letter"})
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.done)
        self.assertEqual(job.result, {"answer": 42})
        self.assertEqual(job.payload, {})
        self.assertEqual(job.attempts, 1)

    def test_a_redelivered_job_is_claimed_again(self):
        job = self._job(status=AiJob.Status.running, attempts=1)
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.done)
        self.assertEqual(job.attempts, 2)

    def test_a_finished_job_is_not_redone(self):
        job = self._job(status=AiJob.Status.done, result={"keep": 1})
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.result, {"keep": 1})
        self.assertEqual(CALLS, [])

    def test_a_crash_loop_ends(self):
        job = self._job(status=AiJob.Status.running, attempts=MAX_JOB_ATTEMPTS)
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.failed)
        self.assertEqual(CALLS, [])

    def test_a_handlers_message_reaches_the_page(self):
        job = self._job("test_refused")
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.error, "You may read this.")

    def test_an_unexpected_error_doesnt_show_its_text(self):
        job = self._job("test_boom")
        with _muted():
            run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.failed)
        self.assertNotIn("10.0.0.9", job.error)

    def test_an_absent_model_server_doesnt_show_its_address(self):
        job = self._job("test_down")
        with _muted():
            run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.failed)
        self.assertNotIn("10.0.0.9", job.error)

    def test_waiting_for_compute_is_on_the_row(self):
        job = self._job("test_waits")
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.result, {"stage_while_waiting": WAITING_STAGE})
        self.assertEqual(job.stage, "")


class AiJobApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")
        self.job = AiJob.objects.create(
            kind="letter_rewrite", user=self.alice, result={"text": "private"}
        )
        self.url = f"/api/llm/jobs/{self.job.pk}/"

    def test_the_owner_reads_it(self):
        self.client.force_login(self.alice)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["result"], {"text": "private"})

    def test_someone_else_gets_a_404(self):
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_input_is_never_part_of_the_answer(self):
        self.client.force_login(self.alice)
        self.assertNotIn("payload", self.client.get(self.url).data)

    def test_it_says_how_many_stand_before_it(self):
        mine = AiJob.objects.create(kind="letter_rewrite", user=self.alice)
        self.assertEqual(jobs_ahead(mine), 1)  # self.job is still pending
        self.client.force_login(self.alice)
        r = self.client.get(f"/api/llm/jobs/{mine.pk}/")
        self.assertEqual(r.data["queue_ahead"], 1)
