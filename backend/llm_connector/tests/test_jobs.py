"""Short AI jobs: the status row a page polls, and the worker rules around it. Same rules as a
generation run: safe to run twice, a crash loop ends, the model server being gone is a stage."""

from datetime import timedelta
from unittest.mock import patch
from urllib import error

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APITestCase

from llm_connector.base import WAITING_STAGE, LLMTransportError
from llm_connector.client import _compute_wait
from llm_connector.jobs import AiJobFailed, ai_job, jobs_ahead, start_ai_job
from llm_connector.models import AiJob
from llm_connector.tasks import (
    MAX_JOB_ATTEMPTS,
    prewarm_model_server,
    request_prewarm,
    run_ai_job,
)

from ._helpers import TEST_HIRSCHAI, _muted

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


class ReapLostJobsTests(APITestCase):
    """A job whose task is gone: it expired after a crash, or the broker lost it. Without this its
    page polls for a day, and it counts as "ahead" for everyone behind it."""

    def setUp(self):
        self.user = User.objects.create(username="alice")

    def _job(self, status, age_s):
        job = AiJob.objects.create(
            kind="test_ok", user=self.user, status=status, payload={"text": "my letter"}
        )
        AiJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(seconds=age_s)
        )
        return job

    def _reap(self):
        from llm_connector.tasks import reap_lost_jobs

        with _muted():  # the reaper logs what it closes
            return reap_lost_jobs()

    def test_a_job_past_its_expiry_is_closed_with_a_message(self):
        for status in (AiJob.Status.pending, AiJob.Status.running):
            with self.subTest(status):
                job = self._job(status, settings.AI_JOB_EXPIRES_S + 120)
                self.assertEqual(self._reap(), 1)
                job.refresh_from_db()
                self.assertEqual(job.status, AiJob.Status.failed)
                self.assertTrue(job.error)
                self.assertEqual(job.payload, {})

    def test_a_job_that_can_still_run_stays(self):
        job = self._job(AiJob.Status.pending, settings.AI_JOB_EXPIRES_S - 60)
        self.assertEqual(self._reap(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.pending)

    def test_a_finished_job_stays(self):
        job = self._job(AiJob.Status.done, settings.AI_JOB_EXPIRES_S + 120)
        self.assertEqual(self._reap(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, AiJob.Status.done)


class RequestPrewarmTests(TestCase):
    """Asking for the model server ahead of a request: off without a key, one knock however many
    people arrive, and it never gets in the way of the page that asked."""

    TASK = "llm_connector.tasks.prewarm_model_server"

    def setUp(self):
        cache.clear()

    @override_settings(HIRSCHAI_PREWARM_KEY="")
    def test_off_without_a_key(self):
        """A plain Ollama, a rented box, every laptop."""
        with patch(self.TASK) as task:
            request_prewarm()
        task.apply_async.assert_not_called()

    @override_settings(HIRSCHAI_PREWARM_KEY="k")
    def test_many_people_one_knock(self):
        with patch(self.TASK) as task:
            for _ in range(3):
                request_prewarm()
        task.apply_async.assert_called_once()

    @override_settings(HIRSCHAI_PREWARM_KEY="k")
    def test_a_broker_outage_stays_out_of_the_page(self):
        with patch(self.TASK) as task, _muted():
            task.apply_async.side_effect = OperationalError("down")
            request_prewarm()

    @override_settings(HIRSCHAI_PREWARM_KEY="k")
    def test_a_late_knock_is_dropped(self):
        """Nobody is helped by a wake-up that sat behind sleeping jobs for minutes."""
        with patch(self.TASK) as task:
            request_prewarm()
        self.assertEqual(task.apply_async.call_args.kwargs["expires"], 60)


@override_settings(HIRSCHAI=TEST_HIRSCHAI, HIRSCHAI_PREWARM_KEY="prewarm-key")
class PrewarmModelServerTests(TestCase):
    URLOPEN = "llm_connector.tasks.request.urlopen"

    def test_it_knocks_with_its_own_token(self):
        """The contract's request: `GET {AI_BASE_URL}/models`, so the lab's usage shows what warming
        costs."""
        with patch(self.URLOPEN) as urlopen:
            prewarm_model_server()
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.full_url, "http://localhost:11434/v1/models")
        self.assertEqual(req.get_header("Authorization"), "Bearer prewarm-key")

    def test_a_503_is_the_answer_it_expects(self):
        waking = error.HTTPError("http://x", 503, "waking", {"Retry-After": "30"}, None)
        with patch(self.URLOPEN, side_effect=waking), _muted():
            prewarm_model_server()

    def test_no_answer_at_all_is_fine(self):
        with patch(self.URLOPEN, side_effect=OSError("no route")), _muted():
            prewarm_model_server()


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
