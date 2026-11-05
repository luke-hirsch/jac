"""View-layer tests: generation-run create/read/cancel, application auto-run,
chat/rewrite executor plumbing, and the pinned-entries API.

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`.
Celery is always mocked (apply_async must return a STRING id — a Mock poisons
the task_id CharField); the tower probe is mocked wherever resolution consults it.
"""

import json
import threading
from datetime import timedelta
from unittest.mock import patch

import redis
from asgiref.sync import async_to_sync
from django.conf import settings as dj_settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient, APITestCase

from jac import views as jac_views
from jac.career_import import ENTRY_MODELS, wipe_career
from jac.llm_prompts import LetterChat, ParagraphRewrite
from jac.models import (
    DailyGenerationCount,
    Domain,
    Education,
    GenerationRun,
    Job,
    JobApplication,
    JobPosting,
    Language,
    Location,
    Mode,
    Project,
    Skill,
)
from jac.queues import (
    count_generations,
    daily_generation_cap,
    generation_budget_left,
    queue_report,
    runs_ahead,
)
from jac.serializers import MAX_POSTING_CHARS
from llm_connector.catalog import default_model
from llm_connector.executor import Executor
from llm_connector.models import AiJob, LLMConfig
from llm_connector.tasks import run_ai_job

from ._helpers import TEST_HIRSCHAI, make_application, make_user, queued_jobs

RUNS_URL = "/api/jac/generations/"
APPS_URL = "/api/jac/applications/"

# The chat throttle test's REST_FRAMEWORK: the live dict + a tiny llm-chat rate.
THROTTLED_RF = {
    **dj_settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"llm-chat": "2/min"},
}
CHAT_ONCE_A_DAY = {
    **dj_settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"llm-chat": "20/min", "llm-chat-daily": "1/day"},
}


def _stream_body(response) -> str:
    """The body of an async streaming response (what daphne iterates, chunk by chunk)."""

    async def collect():
        return b"".join([part async for part in response.streaming_content])

    return async_to_sync(collect)().decode()


def _mock_enqueue(mock_task):
    mock_task.apply_async.return_value.id = "task-123"


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class GenerationRunCreateTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        cache.clear()  # scoped-throttle history lives in the default cache

    def _post(self, **body):
        payload = {"job_application": self.application.pk, **body}
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            response = self.client.post(RUNS_URL, payload, format="json")
        return response, task

    def _anthropic_row(self):
        row = LLMConfig(user=self.user, provider="anthropic")
        row.api_key = "sk-a"
        row.save()
        return row

    def test_hirschai_standard_run_is_created_and_enqueued(self):
        response, task = self._post(provider="ollama")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["mode"], Mode.standard)
        self.assertEqual(response.data["provider"], "ollama")
        self.assertEqual(response.data["model"], "")
        task.apply_async.assert_called_once()
        run = GenerationRun.objects.get(pk=response.data["id"])
        self.assertEqual(run.task_id, "task-123")

    def test_blank_mode_coerces_to_standard(self):
        response, _ = self._post(provider="ollama", mode="")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["mode"], Mode.standard)

    def test_manual_mode_is_rejected(self):
        response, task = self._post(provider="ollama", mode="manual")
        self.assertEqual(response.status_code, 400)
        task.apply_async.assert_not_called()

    def test_high_on_hirschai_is_rejected(self):
        # high is commercial-only — explicit 400, never a silent degrade.
        response, _ = self._post(provider="ollama", mode="high")
        self.assertEqual(response.status_code, 400)

    def test_unknown_provider_is_rejected(self):
        response, _ = self._post(provider="google")
        self.assertEqual(response.status_code, 400)

    def test_commercial_without_key_is_rejected(self):
        response, _ = self._post(provider="anthropic")
        self.assertEqual(response.status_code, 400)

    def test_commercial_run_gets_the_catalog_default_model(self):
        self._anthropic_row()
        response, _ = self._post(provider="anthropic", mode="high")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["mode"], Mode.high)
        self.assertEqual(response.data["model"], default_model("anthropic"))

    def test_unknown_model_is_rejected(self):
        self._anthropic_row()
        response, _ = self._post(provider="anthropic", model="gpt-4o")
        self.assertEqual(response.status_code, 400)

    def test_blank_provider_resolves_the_default_executor(self):
        response, _ = self._post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["provider"], "ollama")

    def test_a_blank_provider_cant_pick_the_model_servers_model(self):
        response, _ = self._post(model="sneaky-model")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["provider"], "ollama")
        self.assertEqual(response.data["model"], "")

    def test_a_hirschai_run_goes_to_the_model_servers_lane(self):
        _, task = self._post()
        self.assertEqual(task.apply_async.call_args.kwargs["queue"], "jac-ai")

    def test_a_commercial_run_never_waits_behind_the_model_server(self):
        self._anthropic_row()
        _, task = self._post(provider="anthropic")
        self.assertEqual(task.apply_async.call_args.kwargs["queue"], "jac-cloud")

    def test_a_broker_that_refuses_fails_the_run_with_a_message(self):
        with patch("jac.views.generate_run") as task:
            task.apply_async.side_effect = OperationalError("connection refused")
            with self.assertLogs("jac.views", level="ERROR"):
                response = self.client.post(
                    RUNS_URL, {"job_application": self.application.pk}, format="json"
                )
        self.assertEqual(response.status_code, 201)
        run = GenerationRun.objects.get(pk=response.data["id"])
        self.assertEqual(run.status, GenerationRun.Status.failed)
        self.assertIn("queue", run.error)
        self.assertNotIn("refused", run.error)

    def test_foreign_application_is_rejected(self):
        theirs = make_application(self.other)
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            response = self.client.post(
                RUNS_URL,
                {"job_application": theirs.pk, "provider": "ollama"},
                format="json",
            )
        self.assertEqual(response.status_code, 400)


class GenerationRunReadTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        cls.application = make_application(cls.user)
        cls.gen_run = GenerationRun.objects.create(
            job_application=cls.application,
            mode=Mode.high,
            provider="anthropic",
            model="claude-sonnet-5",
        )
        GenerationRun.objects.create(job_application=make_application(cls.other))

    def setUp(self):
        self.client.force_login(self.user)

    def test_detail_shape_names_the_executor(self):
        r = self.client.get(f"{RUNS_URL}{self.gen_run.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            set(r.data),
            {
                "id", "job_application", "status", "stage", "error", "result",
                "mode", "provider", "model", "params", "letter_tone",
                "letter_focus", "posting_title", "queue_ahead", "created_at", "updated_at",
            },
        )
        self.assertEqual(r.data["provider"], "anthropic")
        self.assertEqual(r.data["model"], "claude-sonnet-5")

    def test_list_is_owner_scoped(self):
        r = self.client.get(RUNS_URL)
        self.assertEqual([row["id"] for row in r.data["results"]], [self.gen_run.pk])


CREATE_ONCE_A_MINUTE = {
    **dj_settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"application-create": "1/min"},
}


class ApplicationLimitTests(APITestCase):
    """Every account can write these, and until now only nginx's 12 MB bounded them. The numbers are
    far above any real application: a two-page CV's content is a few KB."""

    def setUp(self):
        self.user = make_user()
        self.application = make_application(self.user)
        self.client.force_login(self.user)
        cache.clear()  # scoped-throttle history lives in the default cache

    def _patch(self, **fields):
        return self.client.patch(
            f"{APPS_URL}{self.application.pk}/", fields, format="json"
        )

    def test_text_over_its_cap_is_refused(self):
        for field, cap in (("cover_letter", 20_000), ("notes", 20_000)):
            with self.subTest(field):
                r = self._patch(**{field: "x" * (cap + 1)})
                self.assertEqual(r.status_code, 400)
                self.assertIn(field, r.data)

    def test_json_over_its_cap_is_refused(self):
        for field in ("cv_content", "letter_meta"):
            with self.subTest(field):
                r = self._patch(**{field: {"subject": "x" * (256 * 1024)}})
                self.assertEqual(r.status_code, 400)
                self.assertIn(field, r.data)

    def test_real_sizes_pass(self):
        r = self._patch(cover_letter="x" * 20_000, notes="x" * 20_000)
        self.assertEqual(r.status_code, 200)
        r = self._patch(letter_meta={"subject": "Application", "salutation": "Dear team,"})
        self.assertEqual(r.status_code, 200)

    @override_settings(REST_FRAMEWORK=CREATE_ONCE_A_MINUTE)
    def test_creating_applications_is_throttled(self):
        """The daily cap only counts runs: past it, an application still gets created."""
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            first = self.client.post(APPS_URL, {"posting_text": "One."}, format="json")
            second = self.client.post(APPS_URL, {"posting_text": "Two."}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 429)


class GenerationRunCancelTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        # A cancel reports the runs before it. Unpatched, that queues a real task on the dev broker.
        patcher = patch("jac.views.notify_when_drained")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cancel_revokes_and_fails_a_pending_run(self):
        run = GenerationRun.objects.create(
            job_application=self.application, task_id="task-9"
        )
        with patch("jac.views.celery_current_app") as celery:
            r = self.client.post(f"{RUNS_URL}{run.pk}/cancel/")
        self.assertEqual(r.status_code, 200)
        celery.control.revoke.assert_called_once_with("task-9", terminate=True)
        run.refresh_from_db()
        self.assertEqual(run.status, GenerationRun.Status.failed)
        self.assertEqual(run.error, "cancelled by user")

    def test_cancel_of_a_finished_run_is_idempotent(self):
        run = GenerationRun.objects.create(
            job_application=self.application, status=GenerationRun.Status.done
        )
        r = self.client.post(f"{RUNS_URL}{run.pk}/cancel/")
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, GenerationRun.Status.done)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ApplicationBatchTests(APITestCase):
    """Paste several postings, leave, get one mail: one wake, several runs."""

    URL = f"{APPS_URL}batch/"

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, postings, **body):
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            r = self.client.post(self.URL, {"postings": postings, **body}, format="json")
        return r, task

    def test_each_posting_gets_an_application_and_a_queued_run(self):
        r, task = self._post(["We need a cook.", "We need a pilot."])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(r.data), 2)
        self.assertEqual(task.apply_async.call_count, 2)
        runs = GenerationRun.objects.filter(job_application__user=self.user)
        self.assertEqual(runs.count(), 2)
        self.assertTrue(all(run.task_id == "task-123" for run in runs))

    def test_it_uses_the_named_executor_and_its_lane(self):
        row = LLMConfig(user=self.user, provider="anthropic")
        row.api_key = "sk-a"
        row.save()
        r, task = self._post(["We need a cook."], provider="anthropic")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(task.apply_async.call_args.kwargs["queue"], "jac-cloud")

    def test_an_unknown_executor_is_a_400_and_creates_nothing(self):
        r, _ = self._post(["We need a cook."], provider="anthropic")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(GenerationRun.objects.exists())

    def test_too_many_at_once_is_a_400(self):
        r, _ = self._post(["x"] * (dj_settings.GENERATION_BATCH_MAX + 1))
        self.assertEqual(r.status_code, 400)

    def test_a_blank_posting_creates_nothing(self):
        r, _ = self._post(["We need a cook.", "   "])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(GenerationRun.objects.exists())

    @patch("jac.views.generation_budget_left", return_value=1)
    def test_all_of_them_fit_todays_budget_or_none_starts(self, _left):
        r, task = self._post(["We need a cook.", "We need a pilot."])
        self.assertEqual(r.status_code, 429)
        self.assertIn("1", r.data["detail"])
        task.apply_async.assert_not_called()
        self.assertFalse(GenerationRun.objects.exists())


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class GenerationBudgetTests(APITestCase):
    """One daily number, however a run gets started: the throttle only sees the generations
    endpoint, so an auto-run per new application used to walk around it. And it's a counter of its
    own: run rows leave with their application, so create + delete in a loop used to be free."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        cache.clear()  # the generations endpoint's throttle history
        self.addCleanup(cache.clear)

    def _post(self, url, body):
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            return self.client.post(url, body, format="json")

    def test_every_way_to_start_a_run_spends_the_budget(self):
        cap = daily_generation_cap()
        self.assertEqual(generation_budget_left(self.user), cap)
        self._post(APPS_URL, {"posting_text": "A cook."})
        self.assertEqual(generation_budget_left(self.user), cap - 1)
        self._post(RUNS_URL, {"job_application": self.application.pk})
        self.assertEqual(generation_budget_left(self.user), cap - 2)
        self._post(f"{APPS_URL}batch/", {"postings": ["A cook.", "A pilot."]})
        self.assertEqual(generation_budget_left(self.user), cap - 4)

    def test_deleting_the_application_gives_nothing_back(self):
        r = self._post(APPS_URL, {"posting_text": "A cook."})
        with patch("jac.views.celery_current_app"):
            deleted = self.client.delete(f"{APPS_URL}{r.data['id']}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(GenerationRun.objects.exists())
        self.assertEqual(generation_budget_left(self.user), daily_generation_cap() - 1)

    @patch("jac.queues.daily_generation_cap", return_value=2)
    def test_a_create_and_delete_loop_runs_out(self, _cap):
        started = 0
        for _ in range(3):
            with (
                patch("jac.views.generate_run") as task,
                patch("jac.views.celery_current_app"),
            ):
                _mock_enqueue(task)
                r = self.client.post(
                    APPS_URL, {"posting_text": "A cook."}, format="json"
                )
                self.client.delete(f"{APPS_URL}{r.data['id']}/")
            started += task.apply_async.call_count
        self.assertEqual(started, 2)

    @patch("jac.queues.daily_generation_cap", return_value=1)
    def test_a_refused_batch_spends_nothing(self, _cap):
        r = self._post(f"{APPS_URL}batch/", {"postings": ["A cook.", "A pilot."]})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(generation_budget_left(self.user), 1)

    def test_yesterdays_count_and_other_peoples_dont_matter(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        DailyGenerationCount.objects.create(user=self.user, day=yesterday, count=999)
        count_generations(make_user("bob"), 999)
        self.assertEqual(generation_budget_left(self.user), daily_generation_cap())

    def test_one_row_per_user_per_day(self):
        count_generations(self.user)
        count_generations(self.user, 2)
        row = DailyGenerationCount.objects.get(user=self.user)
        self.assertEqual(row.count, 3)
        self.assertEqual(row.day, timezone.now().date())

    @patch("jac.views.generation_budget_left", return_value=0)
    def test_over_the_cap_a_new_application_gets_no_auto_run(self, _left):
        with patch("jac.views.generate_run") as task:
            r = self.client.post(APPS_URL, {"posting_text": "A cook."}, format="json")
        self.assertEqual(r.status_code, 201)
        task.apply_async.assert_not_called()
        self.assertFalse(GenerationRun.objects.exists())

    @patch("jac.views.generation_budget_left", return_value=0)
    def test_over_the_cap_a_new_run_is_refused(self, _left):
        with patch("jac.views.generate_run") as task:
            r = self.client.post(
                RUNS_URL, {"job_application": self.application.pk}, format="json"
            )
        self.assertEqual(r.status_code, 429)
        task.apply_async.assert_not_called()


class GenerationBudgetRaceTests(TransactionTestCase):
    """The last slot, wanted by two requests at once. `atomic()` alone doesn't serialise them on
    Postgres: under READ COMMITTED both read the same count, so the cap needs the row lock. Each
    request waits for the other right after reading the budget. Without the lock both get there
    and both see the slot; with it, the second can't read until the first has committed."""

    # How long the request holding the lock waits for one that can't come.
    WAIT_S = 0.3

    def setUp(self):
        cache.clear()  # the generations endpoint's throttle history
        self.addCleanup(cache.clear)
        self.user = make_user()
        self.application = make_application(self.user)
        DailyGenerationCount.objects.create(
            user=self.user, day=timezone.now().date(), count=daily_generation_cap() - 1
        )

    def _race(self, url, body):
        """POST `body` to `url` from two threads at once. Returns their status codes, sorted."""
        barrier = threading.Barrier(2)

        def read_then_wait(user):
            left = generation_budget_left(user)
            try:
                barrier.wait(timeout=self.WAIT_S)
            except threading.BrokenBarrierError:
                pass
            return left

        statuses = []

        def post():
            client = APIClient()
            client.force_authenticate(self.user)
            try:
                statuses.append(client.post(url, body, format="json").status_code)
            finally:
                connection.close()  # this thread's own connection

        with (
            patch("jac.views.generation_budget_left", read_then_wait),
            patch("jac.views.generate_run") as task,
        ):
            _mock_enqueue(task)
            threads = [threading.Thread(target=post) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        return sorted(statuses)

    def _assert_the_last_slot_went_once(self):
        self.assertEqual(GenerationRun.objects.count(), 1)
        self.assertEqual(
            DailyGenerationCount.objects.get(user=self.user).count,
            daily_generation_cap(),
        )

    def test_two_new_runs(self):
        statuses = self._race(RUNS_URL, {"job_application": self.application.pk})
        self.assertEqual(statuses, [201, 429])
        self._assert_the_last_slot_went_once()

    def test_two_new_applications(self):
        # Both applications stand; one run waits for a click.
        statuses = self._race(APPS_URL, {"posting_text": "A cook."})
        self.assertEqual(statuses, [201, 201])
        self._assert_the_last_slot_went_once()

    def test_two_batches(self):
        statuses = self._race(f"{APPS_URL}batch/", {"postings": ["A cook."]})
        self.assertEqual(statuses, [201, 429])
        self._assert_the_last_slot_went_once()


class ApplicationDeleteTests(APITestCase):
    """The runs leave with the application. A task still computing would go on for nobody."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)
        cls.url = f"{APPS_URL}{cls.application.pk}/"

    def setUp(self):
        self.client.force_login(self.user)

    def _run(self, task_id, status=GenerationRun.Status.pending):
        return GenerationRun.objects.create(
            job_application=self.application, task_id=task_id, status=status
        )

    def test_it_revokes_the_tasks_of_active_runs_only(self):
        self._run("queued")
        self._run("computing", GenerationRun.Status.running)
        self._run("finished", GenerationRun.Status.done)
        self._run("")  # the broker refused it: there is no task
        with patch("jac.views.celery_current_app") as celery:
            r = self.client.delete(self.url)
        self.assertEqual(r.status_code, 204)
        revoked = {call.args[0] for call in celery.control.revoke.call_args_list}
        self.assertEqual(revoked, {"queued", "computing"})
        self.assertFalse(GenerationRun.objects.exists())

    def test_a_broker_hiccup_doesnt_stop_the_delete(self):
        self._run("queued")
        with (
            patch("jac.views.celery_current_app") as celery,
            self.assertLogs("jac.views", level="WARNING"),
        ):
            celery.control.revoke.side_effect = OSError("down")
            r = self.client.delete(self.url)
        self.assertEqual(r.status_code, 204)


class QueuePositionTests(APITestCase):
    """What the page says instead of a spinner: how many runs stand before this one, whoever's
    they are, read from the status rows."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        cls.application = make_application(cls.user)
        cls.their_application = make_application(cls.other)

    def _run(self, application=None, *, provider="ollama", age_s=0, **fields):
        run = GenerationRun.objects.create(
            job_application=application or self.application, provider=provider, **fields
        )
        GenerationRun.objects.filter(pk=run.pk).update(
            created_at=timezone.now() - timedelta(seconds=age_s)
        )
        run.refresh_from_db()
        return run

    def test_counts_earlier_unfinished_runs_of_anyone_in_the_lane(self):
        self._run(self.their_application, age_s=30, status=GenerationRun.Status.running)
        self._run(self.their_application, age_s=20)
        self.assertEqual(runs_ahead(self._run(age_s=10)), 2)

    def test_finished_and_later_runs_dont_count(self):
        self._run(age_s=30, status=GenerationRun.Status.done)
        self._run(age_s=20, status=GenerationRun.Status.failed)
        mine = self._run(age_s=10)
        self._run(age_s=0)
        self.assertEqual(runs_ahead(mine), 0)

    def test_the_lanes_dont_wait_for_each_other(self):
        self._run(age_s=30, status=GenerationRun.Status.running)
        self.assertEqual(runs_ahead(self._run(provider="anthropic", age_s=10)), 0)
        self._run(provider="anthropic", age_s=20, status=GenerationRun.Status.running)
        # Both anthropic runs and not the ollama one: the commercial providers share a lane.
        self.assertEqual(runs_ahead(self._run(provider="openai", age_s=5)), 2)

    def test_a_running_run_stands_behind_nobody(self):
        self._run(age_s=30)
        mine = self._run(age_s=10, status=GenerationRun.Status.running)
        self.assertEqual(runs_ahead(mine), 0)

    def test_the_api_reports_it(self):
        self._run(self.their_application, age_s=30)
        mine = self._run(age_s=10)
        self.client.force_login(self.user)
        r = self.client.get(f"{RUNS_URL}{mine.pk}/")
        self.assertEqual(r.data["queue_ahead"], 1)


class QueueReportTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.application = make_application(make_user())

    def _failed(self, provider, error="boom"):
        GenerationRun.objects.create(
            job_application=self.application,
            provider=provider,
            status=GenerationRun.Status.failed,
            error=error,
        )

    def test_counts_failures_per_queue_and_leaves_out_user_cancels(self):
        cache.clear()
        self._failed("ollama")
        self._failed("ollama", GenerationRun.CANCELLED_ERROR)
        self._failed("anthropic")
        with patch("jac.queues.redis.from_url") as broker:
            broker.return_value.llen.return_value = 2
            report = queue_report()
        self.assertEqual(report["jac-ai"], {"waiting": 2, "failed": 1})
        self.assertEqual(report["jac-cloud"], {"waiting": 2, "failed": 1})
        self.assertEqual(report["jac-quick"], {"waiting": 2, "failed": 0})
        self.assertEqual(report["jac-mail"], {"waiting": 2, "failed": 0})

    def test_an_unreachable_broker_is_null_not_an_error(self):
        with (
            patch("jac.queues.redis.from_url", side_effect=redis.ConnectionError),
            self.assertLogs("jac.queues", level="WARNING"),
        ):
            report = queue_report()
        self.assertIsNone(report["jac-ai"]["waiting"])

    def test_the_endpoint_answers_over_plain_http_for_a_container_hostname(self):
        with (
            patch("jac.queues.redis.from_url") as broker,
            self.settings(SECURE_SSL_REDIRECT=True),
        ):
            broker.return_value.llen.return_value = 0
            r = self.client.get("/health/queues")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["jac-ai"]["waiting"], 0)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ApplicationAutoRunTests(APITestCase):
    """Creating an application auto-runs `standard` on the default executor —
    and ONLY creating (never retroactive, never on PATCH)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def setUp(self):
        self.client.force_login(self.user)

    def test_create_with_default_executor_enqueues_a_standard_run(self):
        executor = Executor("anthropic", "claude-sonnet-5", None)
        with (
            patch("jac.views.default_executor", return_value=executor),
            patch("jac.views.generate_run") as task,
        ):
            _mock_enqueue(task)
            r = self.client.post(
                APPS_URL, {"posting_text": "We need a Python engineer."},
                format="json",
            )
        self.assertEqual(r.status_code, 201, r.data)
        runs = GenerationRun.objects.filter(job_application_id=r.data["id"])
        self.assertEqual(runs.count(), 1)
        run = runs.get()
        self.assertEqual(run.mode, Mode.standard)
        self.assertEqual(run.provider, "anthropic")
        self.assertEqual(run.model, "claude-sonnet-5")
        self.assertEqual(run.task_id, "task-123")
        task.apply_async.assert_called_once()

    def test_create_queues_a_hirschai_run_without_asking_the_model_server(self):
        """A model server that is asleep is normal, so a new application always gets its run:
        it waits in line and says so. The old gate silently dropped it."""
        with (
            patch("llm_connector.probe.request.urlopen") as urlopen,
            patch("jac.views.generate_run") as task,
        ):
            _mock_enqueue(task)
            r = self.client.post(
                APPS_URL, {"posting_text": "We need a Python engineer."},
                format="json",
            )
        self.assertEqual(r.status_code, 201)
        run = GenerationRun.objects.get(job_application_id=r.data["id"])
        self.assertEqual(run.provider, "ollama")
        urlopen.assert_not_called()

    def test_a_posting_over_the_cap_is_a_400_and_starts_nothing(self):
        with patch("jac.views.generate_run") as task:
            r = self.client.post(
                APPS_URL, {"posting_text": "x" * (MAX_POSTING_CHARS + 1)}, format="json"
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("posting_text", r.data)
        task.apply_async.assert_not_called()
        self.assertFalse(JobPosting.objects.exists())

    def test_a_posting_at_the_cap_is_fine(self):
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            r = self.client.post(
                APPS_URL, {"posting_text": "x" * MAX_POSTING_CHARS}, format="json"
            )
        self.assertEqual(r.status_code, 201)

    def test_patch_never_auto_runs(self):
        application = make_application(self.user)
        with patch("jac.views.default_executor") as default:
            r = self.client.patch(
                f"{APPS_URL}{application.pk}/", {"notes": "hello"}, format="json"
            )
        self.assertEqual(r.status_code, 200)
        default.assert_not_called()
        self.assertEqual(application.runs.count(), 0)


class LetterChatViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/chat/"

    def _chat(self, **body):
        payload = {
            "body": "Dear team, I am great.",
            "messages": [{"role": "user", "content": "make it warmer"}],
            **body,
        }
        return self.client.post(self.url, payload, format="json")

    def test_executor_error_is_a_400(self):
        from llm_connector.conf import ExecutorError

        with patch(
            "jac.views.resolve_executor", side_effect=ExecutorError("no key")
        ):
            r = self._chat(provider="anthropic")
        self.assertEqual(r.status_code, 400)

    def test_transcript_shape_is_still_guarded(self):
        with patch("jac.views.resolve_executor", return_value=Executor("ollama")):
            r = self._chat(messages=[])
        self.assertEqual(r.status_code, 400)

    def test_a_body_that_isnt_an_object_is_a_400(self):
        r = self.client.post(self.url, "[1, 2]", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_a_letter_body_that_isnt_text_is_a_400(self):
        with patch("jac.views.resolve_executor", return_value=Executor("ollama")):
            r = self._chat(body=5)
        self.assertEqual(r.status_code, 400)
        self.assertIn("body", r.data)

    def test_a_provider_that_isnt_text_is_a_400(self):
        r = self._chat(provider=5)
        self.assertEqual(r.status_code, 400)
        self.assertIn("provider", r.data)


class ParagraphRewriteViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/rewrite/"

    def test_rewrite_round_trip(self):
        """202 at once, then the worker does it, then the page reads the result."""
        with (
            patch("jac.views.resolve_executor", return_value=Executor("ollama")),
            queued_jobs() as task,
        ):
            r = self.client.post(self.url, {"text": "Old text."}, format="json")
        self.assertEqual(r.status_code, 202, r.data)
        task.apply_async.assert_called_once()
        with (
            patch("jac.ai_jobs.resolve_executor", return_value=Executor("ollama")),
            patch("jac.ai_jobs.ParagraphRewrite") as rewrite_cls,
        ):
            rewrite_cls.return_value.rewrite.return_value = "New text."
            run_ai_job(str(r.data["id"]))
        job = self.client.get(f"/api/llm/jobs/{r.data['id']}/").data
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"], {"text": "New text."})

    def test_a_model_that_returns_nothing_fails_the_job(self):
        with queued_jobs():
            r = self.client.post(self.url, {"text": "Old text."}, format="json")
        with (
            patch("jac.ai_jobs.resolve_executor", return_value=Executor("ollama")),
            patch("jac.ai_jobs.ParagraphRewrite") as rewrite_cls,
        ):
            rewrite_cls.return_value.rewrite.return_value = ""
            run_ai_job(str(r.data["id"]))
        job = AiJob.objects.get(pk=r.data["id"])
        self.assertEqual(job.status, AiJob.Status.failed)
        self.assertIn("nothing", job.error)

    def test_blank_text_is_a_400(self):
        r = self.client.post(self.url, {"text": "   "}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_the_letters_language_and_form_reach_the_prompt(self):
        """[fullstack]-letter-register-de: a "more formal" rewrite of an ihr letter must
        not bring "Sie" back, so the editor sends the letter's form along. The LETTER's
        language, like /shorten/: the user can switch the letter after the run."""
        with queued_jobs():
            r = self.client.post(
                self.url,
                {"text": "Alt.", "language": "de", "address_form": "ihr"},
                format="json",
            )
        self.assertEqual(r.status_code, 202, r.data)
        with (
            patch("jac.ai_jobs.resolve_executor", return_value=Executor("ollama")),
            patch("jac.ai_jobs.ParagraphRewrite") as rewrite_cls,
        ):
            rewrite_cls.return_value.rewrite.return_value = "Neu."
            run_ai_job(str(r.data["id"]))
        kwargs = rewrite_cls.call_args.kwargs
        self.assertEqual(kwargs["language"], "de")
        self.assertEqual(kwargs["address_form"], "ihr")

    def test_an_unknown_form_is_a_400(self):
        # It lands in a prompt: validated, never passed through.
        with patch("jac.views.resolve_executor", return_value=Executor("ollama")):
            r = self.client.post(
                self.url, {"text": "Alt.", "address_form": "Sie"}, format="json"
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("address_form", r.data)

    def test_a_body_that_isnt_an_object_is_a_400(self):
        for body in ("[1, 2]", '"Old text."', "5", "null"):
            r = self.client.post(self.url, body, content_type="application/json")
            self.assertEqual(r.status_code, 400, body)

    def test_values_that_arent_text_are_a_400(self):
        for field, body in (
            ("text", {"text": 5}),
            ("instruction", {"text": "Alt.", "instruction": 5}),
            ("language", {"text": "Alt.", "language": ["de"]}),
            ("provider", {"text": "Alt.", "provider": 5}),
            ("model", {"text": "Alt.", "model": {"a": 1}}),
        ):
            r = self.client.post(self.url, body, format="json")
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(field, r.data)

    def test_the_passage_and_the_instruction_are_capped(self):
        r = self.client.post(
            self.url, {"text": "x" * (ParagraphRewrite._MAX_CHARS + 1)}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Passage too long", str(r.data["text"]))
        r = self.client.post(
            self.url, {"text": "Alt.", "instruction": "x" * 501}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("instruction", r.data)
        with queued_jobs():
            r = self.client.post(
                self.url, {"text": "Alt.", "instruction": "x" * 500}, format="json"
            )
        self.assertEqual(r.status_code, 202, r.data)


class PinnedEntriesApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        cls.application = make_application(cls.user)
        cls.skill = Skill.objects.create(user=cls.user, name="Python")
        cls.foreign_skill = Skill.objects.create(user=cls.other, name="Theirs")

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/"

    def _patch_pins(self, pins):
        return self.client.patch(self.url, {"pinned_entries": pins}, format="json")

    def test_patch_round_trip_dedupes(self):
        pin = f"skill:{self.skill.pk}"
        r = self._patch_pins([pin, pin])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["pinned_entries"], [pin])

    def test_malformed_ids_are_rejected(self):
        self.assertEqual(self._patch_pins(["skill:abc"]).status_code, 400)
        self.assertEqual(self._patch_pins(["drone:1"]).status_code, 400)
        self.assertEqual(self._patch_pins("skill:1").status_code, 400)  # not a list

    def test_foreign_entries_are_rejected(self):
        r = self._patch_pins([f"skill:{self.foreign_skill.pk}"])
        self.assertEqual(r.status_code, 400)

    def test_the_pin_cap_is_enforced(self):
        pins = [f"skill:{self.skill.pk}"] * 51
        self.assertEqual(self._patch_pins(pins).status_code, 400)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class GenerationRunParamsTests(APITestCase):
    """[fullstack]-model-knobs: per-run effort/temperature (`params`), validated
    against the catalog knob spec; HirschAI has no knobs. At unskip time also add
    "params" to the exact-shape set in
    GenerationRunReadTests.test_detail_shape_names_the_executor."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        row = LLMConfig(user=self.user, provider="anthropic")
        row.api_key = "sk-a"
        row.save()

    def _post(self, **body):
        payload = {"job_application": self.application.pk, **body}
        with patch("jac.views.generate_run") as task:
            _mock_enqueue(task)
            return self.client.post(RUNS_URL, payload, format="json")

    def test_valid_params_persist_and_echo(self):
        r = self._post(provider="anthropic", mode="high", params={"effort": "high"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["params"], {"effort": "high"})
        run = GenerationRun.objects.get(pk=r.data["id"])
        self.assertEqual(run.params, {"effort": "high"})

    def test_temperature_alone_is_valid(self):
        r = self._post(provider="anthropic", params={"temperature": 0.3})
        self.assertEqual(r.status_code, 201, r.data)

    def test_out_of_range_temperature_is_rejected(self):
        r = self._post(provider="anthropic", params={"temperature": 9})
        self.assertEqual(r.status_code, 400)
        self.assertIn("params", r.data)

    def test_unknown_knob_is_rejected(self):
        r = self._post(provider="anthropic", params={"top_k": 5})
        self.assertEqual(r.status_code, 400)

    def test_effort_and_temperature_cannot_combine(self):
        # The exclusion is spec data (catalog KNOBS), enforced here once.
        r = self._post(
            provider="anthropic", params={"effort": "high", "temperature": 0.3}
        )
        self.assertEqual(r.status_code, 400)

    def test_knobs_on_hirschai_are_rejected(self):
        r = self._post(provider="ollama", params={"effort": "high"})
        self.assertEqual(r.status_code, 400)


class LetterChatStreamTests(APITestCase):
    """[fullstack]-chat-assistant-rework: the chat action becomes an SSE stream
    (`text/event-stream`, `data: {"delta"|"done"|"error"}` lines) behind an
    `llm-chat` scoped throttle. At unskip time DELETE the JSON-era tests in
    LetterChatViewTests (test_any_executor_chats +
    test_chat_round_trip_without_patching_letterchat); the 400 guards there stay."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/chat/"
        cache.clear()  # scoped-throttle history lives in the default cache

    def _chat(self, **body):
        payload = {
            "body": "Dear team.",
            "messages": [{"role": "user", "content": "hi"}],
            **body,
        }
        return self.client.post(self.url, payload, format="json")

    def test_chat_streams_sse_deltas_and_done(self):
        with (
            patch(
                "jac.views.resolve_executor",
                return_value=Executor("fake", "fake-1", self.user),
            ),
            patch.object(LetterChat, "stream", return_value=iter(["Hel", "lo"])),
        ):
            r = self._chat()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/event-stream")
        self.assertEqual(r["X-Accel-Buffering"], "no")
        body = _stream_body(r)
        events = [
            json.loads(line[len("data:"):])
            for line in body.splitlines()
            if line.startswith("data:")
        ]
        self.assertEqual(events, [{"delta": "Hel"}, {"delta": "lo"}, {"done": True}])

    def test_shape_problems_400_as_json_before_any_stream(self):
        with patch(
            "jac.views.resolve_executor",
            return_value=Executor("fake", "fake-1", self.user),
        ):
            r = self._chat(messages=[])
        self.assertEqual(r.status_code, 400)
        self.assertIn("messages", r.data)

    def test_the_letters_language_and_form_reach_the_assistant(self):
        """[fullstack]-letter-register-de: a REVISED BODY drafted in the wrong "you" would
        undo the letter's register in one click."""
        with (
            patch(
                "jac.views.resolve_executor",
                return_value=Executor("fake", "fake-1", self.user),
            ),
            patch("jac.views.LetterChat") as chat_cls,
        ):
            chat_cls._MAX_TRANSCRIPT_CHARS = LetterChat._MAX_TRANSCRIPT_CHARS
            chat_cls._MAX_BODY_CHARS = LetterChat._MAX_BODY_CHARS
            chat_cls.return_value.stream.return_value = iter(["ok"])
            r = self._chat(language="de", address_form="ihr")
            _stream_body(r)
        self.assertEqual(r.status_code, 200)
        kwargs = chat_cls.call_args.kwargs
        self.assertEqual(kwargs["language"], "de")
        self.assertEqual(kwargs["address_form"], "ihr")

    def test_an_unknown_form_is_a_400(self):
        with patch(
            "jac.views.resolve_executor",
            return_value=Executor("fake", "fake-1", self.user),
        ):
            r = self._chat(address_form="euch")
        self.assertEqual(r.status_code, 400)
        self.assertIn("address_form", r.data)

    @override_settings(REST_FRAMEWORK=THROTTLED_RF)
    def test_chat_is_rate_limited(self):
        # Throttling runs before validation, so cheap shape-400s count turns.
        with patch(
            "jac.views.resolve_executor",
            return_value=Executor("fake", "fake-1", self.user),
        ):
            self.assertEqual(self._chat(messages=[]).status_code, 400)
            self.assertEqual(self._chat(messages=[]).status_code, 400)
            self.assertEqual(self._chat(messages=[]).status_code, 429)


def _reply_that_breaks_off():
    yield "Hel"
    raise RuntimeError("the model server went away")


class LetterChatLimitTests(APITestCase):
    """The pace alone allowed about 40 replies side by side per account, each holding a web thread
    for up to two minutes, and there was no daily budget at all."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/chat/"
        cache.clear()  # the lock and the throttle history live in the default cache
        self.addCleanup(cache.clear)
        executor = patch(
            "jac.views.resolve_executor",
            return_value=Executor("fake", "fake-1", self.user),
        )
        executor.start()
        self.addCleanup(executor.stop)
        stream = patch.object(
            LetterChat, "stream", side_effect=lambda: iter(["Hel", "lo"])
        )
        self.stream = stream.start()
        self.addCleanup(stream.stop)

    def _chat(self, **body):
        payload = {"messages": [{"role": "user", "content": "hi"}], **body}
        return self.client.post(self.url, payload, format="json")

    def test_a_second_reply_waits_for_the_first(self):
        first = self._chat()
        self.assertEqual(first.status_code, 200)
        second = self._chat()
        self.assertEqual(second.status_code, 429)
        self.assertIn("messages", second.data)  # what the chat panel shows
        _stream_body(first)
        self.assertEqual(self._chat().status_code, 200)

    def test_another_account_isnt_held_up(self):
        self.assertEqual(self._chat().status_code, 200)
        other = make_user("bob")
        self.client.force_login(other)
        url = f"{APPS_URL}{make_application(other).pk}/chat/"
        r = self.client.post(
            url, {"messages": [{"role": "user", "content": "hi"}]}, format="json"
        )
        self.assertEqual(r.status_code, 200)

    def test_a_reply_that_breaks_off_frees_the_lock(self):
        self.stream.side_effect = _reply_that_breaks_off
        with self.assertLogs("jac.views", level="ERROR"):
            body = _stream_body(self._chat())
        self.assertIn('"error"', body)
        self.assertEqual(self._chat().status_code, 200)

    def test_a_client_that_leaves_mid_reply_frees_the_lock(self):
        """What a disconnect does: the async generator closes, and with it the sync one."""
        streams = []
        serve = jac_views._serve_chunk_by_chunk

        def keep(events):
            streams.append(serve(events))
            return streams[-1]

        async def read_one_chunk_and_leave(stream):
            await anext(stream)
            await stream.aclose()

        with patch("jac.views._serve_chunk_by_chunk", side_effect=keep):
            self.assertEqual(self._chat().status_code, 200)
            self.assertEqual(self._chat().status_code, 429)
            async_to_sync(read_one_chunk_and_leave)(streams[0])
            self.assertEqual(self._chat().status_code, 200)

    def test_a_stream_nobody_reads_holds_the_lock_no_longer_than_a_reply_may_take(self):
        with patch("jac.views.cache") as lock:
            lock.add.return_value = True
            self.assertEqual(self._chat().status_code, 200)
        self.assertEqual(
            lock.add.call_args.args[2], jac_views.JobApplicationViewSet.CHAT_WALL_CLOCK_S
        )

    def test_a_refused_request_takes_no_lock(self):
        self.assertEqual(self._chat(messages=[]).status_code, 400)
        self.assertEqual(self._chat().status_code, 200)

    @override_settings(REST_FRAMEWORK=CHAT_ONCE_A_DAY)
    def test_the_daily_budget_runs_out(self):
        _stream_body(self._chat())
        r = self._chat()
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("messages", r.data)  # the throttle's answer, not the lock's


# --- [fullstack]-education-degree ---------------------------------------------------
# ACTIVE guide (activated 2026-08-07, rescoped to `degree_level` alone). Red until the
# serializer exposes the field.


class EducationDegreeApiTests(APITestCase):
    """The new field has to reach the SPA form — the user classifies their own education;
    nothing infers a degree level from free text or from the grade field."""

    EDU_URL = "/api/jac/education/"

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def setUp(self):
        self.client.force_login(self.user)

    def _create(self, **kw):
        return self.client.post(
            self.EDU_URL,
            {
                "institution": "FU Berlin",
                "field_of_study": "Physics",
                "started": "2012-10-01",
                **kw,
            },
            format="json",
        )

    def test_the_default_comes_back_on_create(self):
        """Omitting it claims nothing — an unclassified row is not a degree."""
        r = self._create()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["degree_level"], 0)

    def test_the_level_round_trips(self):
        r = self._create(degree_level=3)  # bachelor
        self.assertEqual(r.status_code, 201, r.data)
        pk = r.data["id"]
        patched = self.client.patch(
            f"{self.EDU_URL}{pk}/", {"degree_level": 4}, format="json"
        )
        self.assertEqual(patched.status_code, 200, patched.data)
        self.assertEqual(patched.data["degree_level"], 4)

    def test_an_out_of_range_level_is_rejected(self):
        r = self._create(degree_level=9)
        self.assertEqual(r.status_code, 400)
        self.assertIn("degree_level", r.data)

    def test_a_grade_alone_does_not_make_a_degree(self):
        """The rejected alternative, pinned so it can't creep back: `grade` is display
        data. Leaving it off a CV must not change what the entry IS."""
        r = self._create(grade="2.6")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["degree_level"], 0)


# --- [fullstack]-cv-section-toggles -------------------------------------------------


class SectionsOffApiTests(APITestCase):
    """Whole sections switched off for one application. Unknown keys are a 400, not a
    silent no-op: a typo that quietly does nothing is the worst failure mode here."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/"

    def _patch(self, sections):
        return self.client.patch(self.url, {"sections_off": sections}, format="json")

    def test_defaults_to_every_section_on(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["sections_off"], [])

    def test_round_trips_and_dedupes(self):
        r = self._patch(["certifications", "languages", "certifications"])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["sections_off"], ["certifications", "languages"])

    def test_unknown_sections_are_rejected(self):
        # "certification" (singular) is the plausible typo — cv_content uses the plural.
        self.assertEqual(self._patch(["certification"]).status_code, 400)
        self.assertEqual(self._patch(["hobbies"]).status_code, 400)

    def test_a_bare_string_is_not_a_list(self):
        self.assertEqual(self._patch("certifications").status_code, 400)

    def test_can_be_cleared_again(self):
        self._patch(["certifications"])
        r = self._patch([])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["sections_off"], [])


# --- [fullstack]-letter-fit ---------------------------------------------------------


class ShortenLetterViewTests(APITestCase):
    """The page fit is measured in the BROWSER (react-pdf); the server only does the
    language part, so the word budget arrives from the client."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/shorten/"

    def _post(self, **body):
        return self.client.post(
            self.url,
            {"body": "One.\n\nTwo.", "target_words": 120, **body},
            format="json",
        )

    def _run(self, job_id, shortened):
        with (
            patch("jac.ai_jobs.resolve_executor", return_value=Executor("ollama")),
            patch("jac.ai_jobs.ShortenLetter") as cls,
        ):
            cls.return_value.shorten.return_value = shortened
            run_ai_job(str(job_id))
        return cls

    def test_round_trip(self):
        with queued_jobs():
            r = self._post()
        self.assertEqual(r.status_code, 202, r.data)
        self._run(r.data["id"], "Tighter.\n\nStill two.")
        job = self.client.get(f"/api/llm/jobs/{r.data['id']}/").data
        self.assertEqual(job["result"], {"body": "Tighter.\n\nStill two."})

    def test_the_budget_is_required_and_sane(self):
        self.assertEqual(self.client.post(self.url, {"body": "x"}, format="json").status_code, 400)
        self.assertEqual(self._post(target_words=2).status_code, 400)
        self.assertEqual(self._post(target_words=100000).status_code, 400)

    def test_another_users_application_is_not_reachable(self):
        self.client.force_login(self.other)
        self.assertIn(self._post().status_code, (403, 404))

    def test_the_letters_form_reaches_the_prompt(self):
        with queued_jobs():
            r = self._post(language="de", address_form="sie")
        self.assertEqual(r.status_code, 202, r.data)
        cls = self._run(r.data["id"], "Kürzer.\n\nImmer noch zwei.")
        self.assertEqual(cls.call_args.kwargs["address_form"], "sie")

    def test_an_unknown_form_is_a_400(self):
        r = self._post(address_form="dich")
        self.assertEqual(r.status_code, 400)
        self.assertIn("address_form", r.data)

    def test_a_body_that_isnt_text_is_a_400(self):
        r = self._post(body=5)
        self.assertEqual(r.status_code, 400)
        self.assertIn("body", r.data)

SAMPLE_URL = "/api/jac/sample-career/"
# The throttle test's REST_FRAMEWORK: the live dict + a tiny sample-career rate.
THROTTLED_SAMPLE_RF = {
    **dj_settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"sample-career": "1/min"},
}


class SampleCareerApiTests(APITestCase):
    """POST /sample-career/ fills an empty career DB and refuses everything else."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")

    def setUp(self):
        self.client.force_login(self.user)
        cache.clear()  # scoped-throttle history lives in the default cache

    def _post(self):
        return self.client.post(SAMPLE_URL)

    def _counts(self, user):
        return {
            model.__name__: model.objects.filter(user=user).count()
            for model in ENTRY_MODELS
        }

    def test_anonymous_is_refused(self):
        self.client.logout()
        self.assertIn(self._post().status_code, (401, 403))
        self.assertFalse(any(self._counts(self.user).values()))

    def test_loads_the_small_career_for_the_requesting_user_only(self):
        r = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(
            self._counts(self.user),
            {
                "Education": 1,
                "Certification": 0,
                "Skill": 8,
                "Job": 2,
                "Project": 1,
                "Language": 2,
            },
        )
        self.assertEqual(r.data["created"]["jobs"], 2)
        self.assertFalse(any(self._counts(self.other).values()))

    def test_the_entries_say_they_are_sample_data(self):
        self._post()
        for name in (
            *Job.objects.filter(user=self.user).values_list("company", flat=True),
            *Project.objects.filter(user=self.user).values_list("name", flat=True),
            *Education.objects.filter(user=self.user).values_list(
                "institution", flat=True
            ),
        ):
            self.assertIn("(sample)", name)

    def test_the_references_resolve(self):
        self._post()
        project = Project.objects.get(user=self.user)
        self.assertEqual(project.job.title, "Data Analyst")
        self.assertEqual(project.skills.count(), 2)
        self.assertTrue(
            Skill.objects.get(user=self.user, name="Django").builds_on.exists()
        )
        self.assertEqual(Location.objects.filter(user=self.user).count(), 1)

    def test_any_career_entry_makes_it_a_409_and_writes_nothing(self):
        Language.objects.create(user=self.user, name="French")
        r = self._post()
        self.assertEqual(r.status_code, 409)
        self.assertIn("detail", r.data)
        counts = self._counts(self.user)
        self.assertEqual(counts["Language"], 1)
        self.assertEqual(sum(counts.values()), 1)

    def test_a_second_post_is_a_409_and_doubles_nothing(self):
        self.assertEqual(self._post().status_code, 201)
        before = self._counts(self.user)
        self.assertEqual(self._post().status_code, 409)
        self.assertEqual(self._counts(self.user), before)

    def test_a_refusal_inside_the_transaction_still_writes_nothing(self):
        """The double click: both requests pass the first check, the second one loses at the
        check inside the transaction."""
        with patch("jac.career_import.has_career_entries", side_effect=[False, True]):
            self.assertEqual(self._post().status_code, 409)
        self.assertFalse(any(self._counts(self.user).values()))

    def test_it_loads_again_after_the_career_was_emptied(self):
        self._post()
        wipe_career(self.user)
        self.assertEqual(self._post().status_code, 201)
        self.assertEqual(Location.objects.filter(user=self.user).count(), 1)

    def test_system_default_domains_are_reused(self):
        system, _ = User.objects.get_or_create(
            username=dj_settings.SYSTEM_USER_USERNAME, defaults={"is_active": False}
        )
        shared = Domain.objects.create(user=system, name="logistics")
        self._post()
        self.assertFalse(
            Domain.objects.filter(user=self.user, name="logistics").exists()
        )
        self.assertIn(shared, Job.objects.get(title="Data Analyst").domains.all())

    def test_it_queues_no_run_and_calls_no_model(self):
        with (
            patch("jac.views.generate_run") as task,
            patch("jac.llm_prompts.complete") as model,
        ):
            self.assertEqual(self._post().status_code, 201)
        task.apply_async.assert_not_called()
        model.assert_not_called()
        self.assertFalse(GenerationRun.objects.exists())

    @override_settings(REST_FRAMEWORK=THROTTLED_SAMPLE_RF)
    def test_it_is_throttled(self):
        self.assertEqual(self._post().status_code, 201)
        self.assertEqual(self._post().status_code, 429)



class ApplicationTransitionApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def setUp(self):
        self.client.force_login(self.user)
        self.application = make_application(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/transition/"

    def test_a_legal_move_goes_through(self):
        r = self.client.post(self.url, {"to": "approved"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "approved")

    def test_the_side_fields_are_stored_stripped(self):
        self.application.status = JobApplication.StatusChoices.approved
        self.application.save()
        r = self.client.post(
            self.url,
            {"to": "sent", "delivery_method": " email "},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["delivery_method"], "email")

    def test_an_illegal_move_is_a_400_on_to(self):
        r = self.client.post(self.url, {"to": "sent"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("to", r.data)

    def test_a_body_that_isnt_an_object_is_a_400(self):
        r = self.client.post(self.url, "[1, 2]", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_values_that_arent_text_are_a_400(self):
        for field, body in (
            ("to", {}),
            ("to", {"to": 5}),
            ("to", {"to": ["approved"]}),
            ("delivery_method", {"to": "approved", "delivery_method": 5}),
            ("response_outcome", {"to": "approved", "response_outcome": 5}),
            ("note", {"to": "approved", "note": ["x"]}),
        ):
            r = self.client.post(self.url, body, format="json")
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(field, r.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, JobApplication.StatusChoices.draft)


class BulkActionShapeTests(APITestCase):
    URL = "/api/jac/skills/bulk/"

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.skill = Skill.objects.create(user=cls.user, name="Python")
        cls.domain = Domain.objects.create(user=cls.user, name="Backend")

    def setUp(self):
        self.client.force_login(self.user)

    def _patch_domains(self, **body):
        return self.client.post(
            self.URL,
            {"action": "patch_domains", "ids": [self.skill.pk], **body},
            format="json",
        )

    def test_a_body_that_isnt_an_object_is_a_400(self):
        r = self.client.post(self.URL, "[1, 2]", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_domain_lists_that_arent_lists_of_ids_are_a_400(self):
        for body in ({"add": 5}, {"remove": 5}, {"add": [[1]]}, {"add": ["x"]}):
            self.assertEqual(self._patch_domains(**body).status_code, 400, body)

    def test_a_real_domain_list_still_works(self):
        r = self._patch_domains(add=[self.domain.pk])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(list(self.skill.domains.all()), [self.domain])
