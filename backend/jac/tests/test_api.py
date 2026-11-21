"""View-layer tests: generation-run create/read/cancel, application auto-run,
chat/rewrite executor plumbing, and the pinned-entries API.

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`.
Celery is always mocked (apply_async must return a STRING id — a Mock poisons
the task_id CharField); the tower probe is mocked wherever resolution consults it.
"""

import io
import json
import threading
import zipfile
from datetime import timedelta
from unittest.mock import patch

import redis
from asgiref.sync import async_to_sync
from django.conf import settings as dj_settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient, APITestCase

from jac import views as jac_views
from jac.career_changes import FIELDS, LISTS
from jac.career_export import export_career
from jac.career_import import (
    ENTRY_MODELS,
    import_career,
    load_sample_career,
    wipe_career,
)
from jac.llm_prompts import LetterChat, ParagraphRewrite
from jac.models import (
    AccessToken,
    CareerChange,
    Certification,
    DailyGenerationCount,
    Domain,
    Education,
    GenerationRun,
    Job,
    JobApplication,
    JobPostAddress,
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
from jac.tokens import mint_token
from llm_connector.catalog import default_model
from llm_connector.executor import Executor
from llm_connector.models import AiJob, LLMConfig
from llm_connector.tasks import run_ai_job
from llm_connector.tests._helpers import ANTHROPIC_MODELS

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

    def _anthropic_row(self, *, consent=True, models=ANTHROPIC_MODELS):
        row = LLMConfig(user=self.user, provider="anthropic", models=models)
        row.api_key = "sk-a"
        if consent:
            row.transfer_consent_at = timezone.now()
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

    def test_a_key_without_consent_is_rejected_and_queues_nothing(self):
        self._anthropic_row(consent=False)
        response, task = self._post(provider="anthropic", mode="high")
        self.assertEqual(response.status_code, 400)
        self.assertIn("agree to the transfer", str(response.data["provider"]))
        task.apply_async.assert_not_called()
        self.assertFalse(GenerationRun.objects.exists())

    def test_commercial_run_gets_the_keys_default_model(self):
        self._anthropic_row()
        response, _ = self._post(provider="anthropic", mode="high")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["mode"], Mode.high)
        self.assertEqual(
            response.data["model"], default_model("anthropic", ANTHROPIC_MODELS)
        )

    def test_unknown_model_is_rejected(self):
        self._anthropic_row()
        response, _ = self._post(provider="anthropic", model="gpt-4o")
        self.assertEqual(response.status_code, 400)

    def test_a_key_without_a_model_list_is_a_400_that_names_the_fix(self):
        self._anthropic_row(models=[])
        response, task = self._post(provider="anthropic")
        self.assertEqual(response.status_code, 400)
        self.assertIn("press Check", str(response.data["provider"]))
        task.apply_async.assert_not_called()

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
                "id",
                "job_application",
                "status",
                "stage",
                "error",
                "result",
                "mode",
                "provider",
                "model",
                "params",
                "letter_tone",
                "letter_focus",
                "posting_title",
                "queue_ahead",
                "created_at",
                "updated_at",
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
        r = self._patch(
            letter_meta={"subject": "Application", "salutation": "Dear team,"}
        )
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
            r = self.client.post(
                self.URL, {"postings": postings, **body}, format="json"
            )
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
        row = LLMConfig(user=self.user, provider="anthropic", models=ANTHROPIC_MODELS)
        row.api_key = "sk-a"
        row.transfer_consent_at = timezone.now()
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
                APPS_URL,
                {"posting_text": "We need a Python engineer."},
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

    def test_a_default_key_without_a_model_list_fails_the_run_with_the_fix(self):
        """Never a silent switch to HirschAI: the run is there, failed, and says what to do.
        It never reached the queue, so it costs no budget."""
        row = LLMConfig(user=self.user, provider="anthropic", default=True)
        row.api_key = "sk-a"
        row.transfer_consent_at = timezone.now()
        row.save()
        left = generation_budget_left(self.user)
        with patch("jac.views.generate_run") as task:
            r = self.client.post(
                APPS_URL,
                {"posting_text": "We need a Python engineer."},
                format="json",
            )
        self.assertEqual(r.status_code, 201, r.data)
        run = GenerationRun.objects.get(job_application_id=r.data["id"])
        self.assertEqual(run.status, GenerationRun.Status.failed)
        self.assertEqual(run.provider, "anthropic")
        self.assertIn("press Check", run.error)
        task.apply_async.assert_not_called()
        self.assertEqual(generation_budget_left(self.user), left)

    def test_create_queues_a_hirschai_run_without_asking_the_model_server(self):
        """A model server that is asleep is normal, so a new application always gets its run:
        it waits in line and says so. The old gate silently dropped it."""
        with (
            patch("llm_connector.probe.request.urlopen") as urlopen,
            patch("jac.views.generate_run") as task,
        ):
            _mock_enqueue(task)
            r = self.client.post(
                APPS_URL,
                {"posting_text": "We need a Python engineer."},
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

        with patch("jac.views.resolve_executor", side_effect=ExecutorError("no key")):
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
    """[fullstack]-model-knobs: per-run effort (`params`), validated against the run's
    model's knob spec in the key's stored list; HirschAI has no knobs."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        row = LLMConfig(user=self.user, provider="anthropic", models=ANTHROPIC_MODELS)
        row.api_key = "sk-a"
        row.transfer_consent_at = timezone.now()
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

    def test_temperature_is_no_knob_anymore(self):
        # A 400 on every current Claude model: offered by none, refused here.
        r = self._post(provider="anthropic", params={"temperature": 0.3})
        self.assertEqual(r.status_code, 400)
        self.assertIn("params", r.data)

    def test_unknown_knob_is_rejected(self):
        r = self._post(provider="anthropic", params={"top_k": 5})
        self.assertEqual(r.status_code, 400)

    def test_the_levels_are_the_picked_models_own(self):
        haiku = "claude-haiku-4-5-20251001"  # a token budget: low/medium/high only
        r = self._post(provider="anthropic", model=haiku, params={"effort": "max"})
        self.assertEqual(r.status_code, 400)
        r = self._post(provider="anthropic", model=haiku, params={"effort": "high"})
        self.assertEqual(r.status_code, 201, r.data)
        r = self._post(
            provider="anthropic", model="claude-opus-4-8", params={"effort": "max"}
        )
        self.assertEqual(r.status_code, 201, r.data)

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
            json.loads(line[len("data:") :])
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
            lock.add.call_args.args[2],
            jac_views.JobApplicationViewSet.CHAT_WALL_CLOCK_S,
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
        self.assertEqual(
            self.client.post(self.url, {"body": "x"}, format="json").status_code, 400
        )
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


EXPORT_URL = "/api/jac/career/export/"


class CareerExportApiTests(APITestCase):
    """GET /career/export/ is the `cv_export` file: what `cv_import` reads, for the user only."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")

    def setUp(self):
        self.client.force_login(self.user)

    def _export(self):
        r = self.client.get(EXPORT_URL)
        self.assertEqual(r.status_code, 200)
        return json.loads(r.content)  # the bytes the browser gets, dates as strings

    def test_anonymous_is_refused(self):
        self.client.logout()
        self.assertIn(self.client.get(EXPORT_URL).status_code, (401, 403))

    def test_the_file_imports_back_as_the_same_career(self):
        load_sample_career(self.user)
        data = self._export()
        self.assertEqual(len(data["jobs"]), 2)
        self.assertEqual(data["jobs"][0]["started"][:4], "2018")  # ISO, oldest first
        import_career(self.other, data)
        self.client.force_login(self.other)
        self.assertEqual(self._export(), data)

    def test_only_the_users_own_rows(self):
        Job.objects.create(
            user=self.other, title="Theirs", company="Bob AG", started="2020-01-01"
        )
        data = self._export()
        self.assertEqual(data["jobs"], [])
        self.assertEqual(
            sorted(data),
            [
                "certifications",
                "domains",
                "educations",
                "jobs",
                "languages",
                "locations",
                "projects",
                "skills",
            ],
        )


CAREER_IMPORT_URL = "/api/jac/career/import/"
THROTTLED_IMPORT_RF = {
    **dj_settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"career-import": "1/min", "application-import": "1/min"},
}


def _upload(client, url, data, name="file.json"):
    body = data if isinstance(data, bytes) else json.dumps(data).encode()
    return client.post(
        url,
        {"file": SimpleUploadedFile(name, body, content_type="application/json")},
        format="multipart",
    )


class CareerImportApiTests(APITestCase):
    """POST /career/import/ fills an EMPTY career from the download, every row checked as the
    editor checks it; a file it can't take is a 400 that writes nothing, never a 500."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")

    def setUp(self):
        self.client.force_login(self.user)
        cache.clear()  # scoped-throttle history lives in the default cache

    def _counts(self, user):
        return sum(m.objects.filter(user=user).count() for m in ENTRY_MODELS)

    def _job(self, **kw):
        return {
            "jobs": [{"title": "Dev", "company": "Acme", "started": "2020-01-01", **kw}]
        }

    def test_anonymous_is_refused(self):
        self.client.logout()
        r = _upload(self.client, CAREER_IMPORT_URL, self._job())
        self.assertIn(r.status_code, (401, 403))

    def test_the_download_round_trips_favourites_included(self):
        load_sample_career(self.other)
        job = Job.objects.filter(user=self.other).order_by("started").first()
        job.favourite = True
        job.save()
        self.client.force_login(self.other)
        data = json.loads(self.client.get(EXPORT_URL).content)
        self.client.force_login(self.user)
        r = _upload(self.client, CAREER_IMPORT_URL, data)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["created"]["jobs"], 2)
        self.assertEqual(r.data["warnings"], [])
        self.assertEqual(json.loads(self.client.get(EXPORT_URL).content), data)
        self.assertTrue(Job.objects.get(user=self.user, title=job.title).favourite)

    def test_a_career_with_entries_is_refused_and_untouched(self):
        Language.objects.create(user=self.user, name="French")
        r = _upload(self.client, CAREER_IMPORT_URL, self._job())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self._counts(self.user), 1)

    def test_a_script_url_is_a_400_and_writes_nothing(self):
        # The portfolio links an entry's url: only what the editor accepts may land.
        r = _upload(
            self.client, CAREER_IMPORT_URL, self._job(url="javascript:alert(1)")
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("url", r.data["detail"])
        self.assertEqual(self._counts(self.user), 0)

    def test_a_bad_choice_is_a_400(self):
        r = _upload(self.client, CAREER_IMPORT_URL, self._job(job_type="pirate"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("job_type", r.data["detail"])

    def test_a_missing_field_is_a_400_not_a_crash(self):
        data = {"jobs": [{"title": "Dev", "started": "2020-01-01"}]}
        r = _upload(self.client, CAREER_IMPORT_URL, data)
        self.assertEqual(r.status_code, 400)
        self.assertIn("company", r.data["detail"])

    def test_a_bad_date_is_a_400(self):
        r = _upload(self.client, CAREER_IMPORT_URL, self._job(started="01.01.2020"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("01.01.2020", r.data["detail"])

    def test_two_skills_of_one_name_are_a_400(self):
        data = {"skills": [{"name": "Python"}, {"name": "Python"}]}
        r = _upload(self.client, CAREER_IMPORT_URL, data)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._counts(self.user), 0)

    def test_a_late_failure_rolls_back_the_rows_before_it(self):
        data = {**self._job(), "languages": [{"name": "German", "fluency": "fluent?"}]}
        r = _upload(self.client, CAREER_IMPORT_URL, data)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._counts(self.user), 0)

    def test_what_isnt_a_career_file_is_a_400(self):
        for body in (b"not json", b"[1, 2]", json.dumps({"applications": []}).encode()):
            with self.subTest(body=body):
                r = _upload(self.client, CAREER_IMPORT_URL, body)
                self.assertEqual(r.status_code, 400)
                self.assertIn("detail", r.data)

    def test_without_a_file_is_a_400(self):
        r = self.client.post(CAREER_IMPORT_URL, {}, format="multipart")
        self.assertEqual(r.status_code, 400)

    @override_settings(REST_FRAMEWORK=THROTTLED_IMPORT_RF)
    def test_it_is_throttled(self):
        self.assertEqual(
            _upload(self.client, CAREER_IMPORT_URL, self._job()).status_code, 201
        )
        self.assertEqual(
            _upload(self.client, CAREER_IMPORT_URL, self._job()).status_code, 429
        )


APPS_EXPORT_URL = "/api/jac/applications/export/"
APPS_IMPORT_URL = "/api/jac/applications/import/"


class ApplicationTransferApiTests(APITestCase):
    """GET /applications/export/ + POST /applications/import/: the applications as a file whose
    CV picks name entries by natural key, so they land on the importing user's own entries."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        load_sample_career(cls.user)
        cls.job = Job.objects.filter(user=cls.user).order_by("started").first()
        cls.skill = Skill.objects.filter(user=cls.user).order_by("name").first()
        posting = JobPosting.objects.create(
            user=cls.user, title="Backend Dev", posting_text="We need a backend dev."
        )
        JobPostAddress.objects.create(
            job_posting=posting, company="Beispiel GmbH", city="Berlin"
        )
        cls.sent = JobApplication.objects.create(
            user=cls.user,
            posting=posting,
            cv_content={
                "jobs": [{"id": f"job:{cls.job.pk}", "label": "Job", "pinned": True}],
                "skills": [
                    {
                        "id": f"skill:{cls.skill.pk}",
                        "label": "Skill",
                        "detail": "compact",
                    }
                ],
            },
            pinned_entries=[f"job:{cls.job.pk}"],
            cover_letter="Dear team, …",
            letter_meta={"language": "en", "subject": "Backend Dev"},
            sections_off=["certifications"],
            notes="Called on Monday.",
            deadline="2026-12-01",
            status=JobApplication.StatusChoices.sent,
            delivery_method=JobApplication.DeliveryMethod.email,
            sent_at=timezone.now() - timedelta(days=3),
        )
        cls.draft = JobApplication.objects.create(user=cls.user, posting=posting)

    def setUp(self):
        self.client.force_login(self.user)
        cache.clear()

    def _export(self):
        r = self.client.get(APPS_EXPORT_URL)
        self.assertEqual(r.status_code, 200)
        return json.loads(r.content)

    def _give_other_the_career(self):
        import_career(
            self.other, json.loads(json.dumps(export_career(self.user), default=str))
        )

    def test_anonymous_is_refused(self):
        self.client.logout()
        self.assertIn(self.client.get(APPS_EXPORT_URL).status_code, (401, 403))
        self.assertIn(_upload(self.client, APPS_IMPORT_URL, {}).status_code, (401, 403))

    def test_the_file_names_entries_by_key_and_shares_the_posting(self):
        data = self._export()
        self.assertEqual(len(data["postings"]), 1)
        self.assertEqual([a["posting"] for a in data["applications"]], [0, 0])
        sent = data["applications"][0]
        row = sent["cv_content"]["jobs"][0]
        self.assertNotIn("id", row)
        self.assertEqual(
            row["ref"],
            ["job", self.job.title, self.job.company, self.job.started.isoformat()],
        )
        self.assertEqual(
            sent["cv_content"]["skills"][0]["ref"], ["skill", self.skill.name]
        )
        self.assertEqual(sent["pinned_entries"], [row["ref"]])
        self.assertEqual(data["postings"][0]["address"]["company"], "Beispiel GmbH")

    def test_only_the_users_own_applications(self):
        make_application(self.other)
        self.assertEqual(len(self._export()["applications"]), 2)

    def test_the_file_round_trips_onto_the_other_users_entries(self):
        data = self._export()
        self._give_other_the_career()
        self.client.force_login(self.other)
        with patch("jac.views.generate_run") as task:
            r = _upload(self.client, APPS_IMPORT_URL, data)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["created"], {"postings": 1, "applications": 2})
        self.assertEqual(r.data["warnings"], [])
        task.apply_async.assert_not_called()  # an import queues no run
        self.assertFalse(GenerationRun.objects.exists())
        self.assertEqual(self._export(), data)
        imported = JobApplication.objects.get(user=self.other, status="sent")
        job = Job.objects.get(user=self.other, title=self.job.title)
        self.assertEqual(imported.pinned_entries, [f"job:{job.pk}"])
        self.assertEqual(imported.cv_content["jobs"][0]["id"], f"job:{job.pk}")

    def test_an_entry_the_career_lacks_is_left_out_with_a_warning(self):
        data = self._export()
        self._give_other_the_career()
        Skill.objects.filter(user=self.other, name=self.skill.name).delete()
        self.client.force_login(self.other)
        r = _upload(self.client, APPS_IMPORT_URL, data)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(r.data["warnings"]), 1)
        self.assertIn("1 CV entry", r.data["warnings"][0])
        imported = JobApplication.objects.get(user=self.other, status="sent")
        self.assertEqual(imported.cv_content["skills"], [])

    def test_with_applications_already_it_is_refused(self):
        data = self._export()
        r = _upload(self.client, APPS_IMPORT_URL, data)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(JobApplication.objects.filter(user=self.user).count(), 2)

    def test_without_a_career_it_is_refused(self):
        data = self._export()
        self.client.force_login(self.other)
        r = _upload(self.client, APPS_IMPORT_URL, data)
        self.assertEqual(r.status_code, 409)
        self.assertIn("career", r.data["detail"])

    def test_a_bad_status_is_a_400_and_writes_nothing(self):
        data = self._export()
        data["applications"][1]["status"] = "hired"
        self._give_other_the_career()
        self.client.force_login(self.other)
        r = _upload(self.client, APPS_IMPORT_URL, data)
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.data["detail"])
        self.assertFalse(JobPosting.objects.filter(user=self.other).exists())
        self.assertFalse(JobApplication.objects.filter(user=self.other).exists())

    def test_a_posting_index_outside_the_file_is_a_400(self):
        data = self._export()
        data["applications"][0]["posting"] = 5
        self._give_other_the_career()
        self.client.force_login(self.other)
        self.assertEqual(_upload(self.client, APPS_IMPORT_URL, data).status_code, 400)

    def test_a_career_file_is_not_an_applications_file(self):
        self._give_other_the_career()
        self.client.force_login(self.other)
        career = json.loads(self.client.get(EXPORT_URL).content)
        r = _upload(self.client, APPS_IMPORT_URL, career)
        self.assertEqual(r.status_code, 400)
        self.assertIn("applications file", r.data["detail"])


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


# --- [fullstack]-claude-skill -------------------------------------------------------------

SKILL_URL = "/api/jac/skill/"


def _letter(words=200, word="Wort"):
    return " ".join([word] * words)


class _SkillCase(APITestCase):
    """A user with a small career and a token; requests go out on the token, not a session."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")
        cls.python = Skill.objects.create(user=cls.user, name="Python")
        cls.job = Job.objects.create(
            user=cls.user, title="Backend Dev", company="Acme", started="2020-01-01"
        )
        cls.job.skills.add(cls.python)
        cls.master = Education.objects.create(
            user=cls.user,
            institution="FU Berlin",
            started="2012-10-01",
            ended="2017-09-30",
            degree_level=Education.DegreeLevel.master,
        )
        cls.theirs = Skill.objects.create(user=cls.other, name="Theirs")

    def setUp(self):
        cache.clear()  # scoped-throttle history lives in the default cache
        self.token, self.key = mint_token(self.user, "test")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.key}")

    def _payload(self, **kw):
        return {
            "posting": {
                "text": "Wir suchen eine:n Backend-Entwickler:in. Sie arbeiten mit Python.",
                "title": "Backend-Entwickler:in",
                "language": "de",
                "company": "Beispiel GmbH",
                "contact_name": "Frau Muster",
            },
            "entries": [f"job:{self.job.pk}", f"skill:{self.python.pk}"],
            "letter": {"body": _letter(), "tone": "formal"},
            **kw,
        }

    def _create(self, **kw):
        return self.client.post(
            f"{SKILL_URL}applications/", self._payload(**kw), format="json"
        )


class SkillTokenAuthTests(_SkillCase):
    """The token opens the skill's endpoints and nothing else."""

    def test_the_token_reads_the_career_and_marks_itself_used(self):
        r = self.client.get(f"{SKILL_URL}career/")
        self.assertEqual(r.status_code, 200, r.data)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_no_credentials_is_refused(self):
        self.client.credentials()
        self.assertIn(self.client.get(f"{SKILL_URL}career/").status_code, (401, 403))

    def test_a_revoked_token_is_a_401(self):
        self.token.delete()
        self.assertEqual(self.client.get(f"{SKILL_URL}career/").status_code, 401)

    def test_a_made_up_token_is_a_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer jac_nope")
        self.assertEqual(self.client.get(f"{SKILL_URL}career/").status_code, 401)

    def test_the_token_does_not_open_the_rest_of_the_api(self):
        for url in (APPS_URL, "/api/jac/cv/entries/", "/api/llm/configs/"):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_a_token_cannot_mint_tokens_or_download(self):
        r = self.client.post(f"{SKILL_URL}tokens/", {"name": "more"}, format="json")
        self.assertIn(r.status_code, (401, 403))
        for url in (f"{SKILL_URL}bundle/", f"{SKILL_URL}career/file/"):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_only_the_hash_is_stored(self):
        self.assertNotEqual(self.token.key_hash, self.key)
        self.assertNotIn(self.key, self.token.key_hash)
        self.assertEqual(self.token.prefix, self.key[:12])


class SkillTokenManagementTests(APITestCase):
    """Minting, listing and revoking happen on the session, in Account → LLM → Claude."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.other = make_user("bob")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _create(self, **body):
        return self.client.post(
            f"{SKILL_URL}tokens/", {"transfer_consent": True, **body}, format="json"
        )

    def test_no_token_without_consent(self):
        """A token hands the career to Claude, and so to Anthropic, also outside the EU: the
        same Art. 49 consent as an API key, before the token exists."""
        for body in ({"name": "laptop"}, {"name": "laptop", "transfer_consent": False}):
            with self.subTest(body=body):
                r = self.client.post(f"{SKILL_URL}tokens/", body, format="json")
                self.assertEqual(r.status_code, 400)
                self.assertIn("transfer_consent", r.data)
        self.assertFalse(AccessToken.objects.filter(user=self.user).exists())

    def test_the_consent_is_stamped_on_the_token(self):
        before = timezone.now()
        r = self._create(name="laptop")
        self.assertEqual(r.status_code, 201, r.data)
        token = AccessToken.objects.get(pk=r.data["id"])
        self.assertGreaterEqual(token.transfer_consent_at, before)
        self.assertIsNotNone(r.data["transfer_consent_at"])

    def test_a_token_needs_no_flag_once_agreed(self):
        """Agreed once on the Claude page, kept: the next token asks nothing."""
        r = self.client.post(f"{SKILL_URL}consent/")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.client.post(f"{SKILL_URL}tokens/", {"name": "laptop"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_the_flag_records_the_consent(self):
        self._create(name="laptop")
        self.assertIsNotNone(self.client.get(f"{SKILL_URL}consent/").data["given_at"])

    def test_withdrawing_revokes_every_token(self):
        _, key = mint_token(self.user)
        self._create(name="laptop")
        r = self.client.delete(f"{SKILL_URL}consent/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(AccessToken.objects.filter(user=self.user).exists())
        self.assertIsNone(self.client.get(f"{SKILL_URL}consent/").data["given_at"])
        r = self.client.post(f"{SKILL_URL}tokens/", {"name": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.client.logout()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
        self.assertEqual(self.client.get(f"{SKILL_URL}career/").status_code, 401)

    def test_a_token_cannot_give_or_withdraw_the_consent(self):
        _, key = mint_token(self.user)
        self.client.logout()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
        for method in ("post", "delete"):
            with self.subTest(method=method):
                r = getattr(self.client, method)(f"{SKILL_URL}consent/")
                self.assertIn(r.status_code, (401, 403))

    def test_the_key_comes_back_once(self):
        r = self._create(name="laptop")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["key"].startswith("jac_"))
        listed = self.client.get(f"{SKILL_URL}tokens/").data["results"]
        self.assertEqual([t["name"] for t in listed], ["laptop"])
        self.assertNotIn("key", listed[0])

    def test_revoking_ends_the_token(self):
        token, key = mint_token(self.user)
        r = self.client.delete(f"{SKILL_URL}tokens/{token.pk}/")
        self.assertEqual(r.status_code, 204)
        self.client.logout()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
        self.assertEqual(self.client.get(f"{SKILL_URL}career/").status_code, 401)

    def test_someone_elses_token_is_a_404(self):
        token, _ = mint_token(self.other)
        r = self.client.delete(f"{SKILL_URL}tokens/{token.pk}/")
        self.assertEqual(r.status_code, 404)
        self.assertTrue(AccessToken.objects.filter(pk=token.pk).exists())

    def test_the_number_of_tokens_is_capped(self):
        for _ in range(AccessToken.MAX_PER_USER):
            mint_token(self.user)
        r = self._create(name="one more")
        self.assertEqual(r.status_code, 400)
        # The zip mints nothing, so the cap doesn't stand in its way.
        self.assertEqual(self.client.get(f"{SKILL_URL}bundle/").status_code, 200)


class SkillDownloadTests(APITestCase):
    """Account → LLM → Claude's two downloads, on the session: the skill once, the career per chat
    (the Claude app's sandbox can't reach jac, so the career travels as a file)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.skill = Skill.objects.create(user=cls.user, name="Quetzalcoatl")

    def setUp(self):
        self.client.force_login(self.user)

    def test_anonymous_is_refused(self):
        self.client.logout()
        for url in (f"{SKILL_URL}bundle/", f"{SKILL_URL}career/file/"):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_the_bundle_is_the_skill_alone(self):
        r = self.client.get(f"{SKILL_URL}bundle/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(
            sorted(z.namelist()),
            ["jac/REVIEW.md", "jac/SKILL.md", "jac/scripts/jac.py"],
        )
        self.assertTrue(z.read("jac/SKILL.md").startswith(b"---\nname: jac\n"))
        everything = b"".join(z.read(name) for name in z.namelist())
        self.assertNotIn(b"Quetzalcoatl", everything)  # nothing about the user
        self.assertFalse(AccessToken.objects.exists())  # and it mints no token

    def test_the_career_file_is_the_career_with_its_review(self):
        r = self.client.get(f"{SKILL_URL}career/file/")
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertEqual(data["format"], "jac-career-for-claude")
        skill = data["entries"]["skills"][0]
        self.assertEqual(skill["id"], f"skill:{self.skill.pk}")
        self.assertTrue(skill["updated_at"])  # what a career change's `seen` must match
        self.assertIn("findings", data["review"])
        self.assertIn("counts", data["review"])


class SkillCareerTests(_SkillCase):
    def test_entries_carry_ids_and_names_not_keys(self):
        data = self.client.get(f"{SKILL_URL}career/").data
        job = data["entries"]["jobs"][0]
        self.assertEqual(job["id"], f"job:{self.job.pk}")
        self.assertEqual(job["skills"], ["Python"])
        edu = data["entries"]["educations"][0]
        self.assertEqual(edu["degree_level"], "master")
        self.assertTrue(edu["completed"])
        self.assertEqual(data["highest_degree"], f"education:{self.master.pk}")

    def test_only_the_users_own_entries(self):
        data = self.client.get(f"{SKILL_URL}career/").data
        names = [s["name"] for s in data["entries"]["skills"]]
        self.assertEqual(names, ["Python"])

    def test_educations_and_certifications_together(self):
        # Both prefetch their domains; one mixed list was a ValueError, a 500 on the career
        # and on the zip for anyone with both.
        backend = Domain.objects.create(user=self.user, name="Backend")
        self.master.domains.add(backend)
        aws = Certification.objects.create(user=self.user, name="AWS SAA", issuer="AWS")
        aws.domains.add(backend)
        r = self.client.get(f"{SKILL_URL}career/")
        self.assertEqual(r.status_code, 200)
        entries = r.data["entries"]
        self.assertEqual(entries["educations"][0]["domains"], ["Backend"])
        self.assertEqual(entries["certifications"][0]["domains"], ["Backend"])


class SkillApplicationTests(_SkillCase):
    """What the skill saves is an ordinary application: the editor, the fit and the export
    read it like a generated one."""

    def test_create_builds_posting_cv_and_letter_frame(self):
        r = self._create()
        self.assertEqual(r.status_code, 201, r.data)
        app = JobApplication.objects.get(pk=r.data["id"])
        self.assertEqual(app.user, self.user)
        self.assertEqual(app.posting.title, "Backend-Entwickler:in")
        self.assertEqual(app.posting.language, "de")
        self.assertEqual(app.posting.address.company, "Beispiel GmbH")
        self.assertEqual(
            [e["id"] for e in app.cv_content["jobs"]], [f"job:{self.job.pk}"]
        )
        self.assertIn("Backend Dev at Acme", app.cv_content["jobs"][0]["label"])
        meta = app.letter_meta
        self.assertEqual(meta["subject"], "Bewerbung als Backend-Entwickler:in")
        self.assertIn("Frau Muster", meta["salutation"])
        self.assertEqual(meta["closing"], "Mit freundlichen Grüßen")
        self.assertEqual(meta["address_form"], "sie")
        self.assertEqual(meta["recipient"]["company"], "Beispiel GmbH")
        self.assertEqual(meta["sender"]["name"], "alice")
        self.assertTrue(r.data["url"].endswith(f"/applications/{app.pk}"))

    def test_the_highest_degree_is_added_and_pinned(self):
        r = self._create()
        top = f"education:{self.master.pk}"
        self.assertIn(top, r.data["entries"])
        self.assertIn(top, r.data["pinned"])
        self.assertTrue(any(top in w for w in r.data["warnings"]))

    def test_it_shows_up_in_the_applications_list(self):
        app_id = self._create().data["id"]
        self.client.credentials()
        self.client.force_login(self.user)
        ids = [row["id"] for row in self.client.get(APPS_URL).data["results"]]
        self.assertIn(app_id, ids)

    def test_the_session_can_import_a_file_the_skill_wrote(self):
        self.client.credentials()
        self.client.force_login(self.user)
        self.assertEqual(self._create().status_code, 201)

    def test_someone_elses_entry_is_rejected(self):
        r = self._create(entries=[f"skill:{self.theirs.pk}"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("entries", r.data)

    def test_create_needs_posting_entries_and_letter(self):
        for field in ("posting", "entries", "letter"):
            with self.subTest(field=field):
                payload = self._payload()
                del payload[field]
                r = self.client.post(
                    f"{SKILL_URL}applications/", payload, format="json"
                )
                self.assertEqual(r.status_code, 400)
                self.assertIn(field, r.data)

    def test_warnings_tell_the_skill_what_to_fix(self):
        body = (
            "Sehr geehrte Frau Muster,\n\n"
            + _letter(50)
            + " du\n\nMit freundlichen Grüßen"
        )
        r = self._create(letter={"body": body, "tone": "formal"})
        text = " ".join(r.data["warnings"])
        self.assertIn("words", text)
        self.assertIn("salutation", text)
        self.assertIn("closing", text)
        self.assertIn("du", text)

    def test_a_clean_letter_has_no_letter_warnings(self):
        r = self._create(entries=[f"job:{self.job.pk}", f"education:{self.master.pk}"])
        self.assertEqual(r.data["warnings"], [])

    def test_patch_changes_only_what_is_sent(self):
        app_id = self._create().data["id"]
        url = f"{SKILL_URL}applications/{app_id}/"
        r = self.client.patch(
            url, {"letter": {"body": _letter(word="Neu")}}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.data)
        app = JobApplication.objects.get(pk=app_id)
        self.assertTrue(app.cover_letter.startswith("Neu"))
        self.assertEqual(
            [e["id"] for e in app.cv_content["jobs"]], [f"job:{self.job.pk}"]
        )
        self.assertEqual(app.posting.title, "Backend-Entwickler:in")

    def test_read_back_round_trips(self):
        created = self._create().data
        r = self.client.get(f"{SKILL_URL}applications/{created['id']}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["entries"], created["entries"])
        self.assertEqual(r.data["letter"]["body"], _letter())
        self.assertEqual(r.data["posting"]["company"], "Beispiel GmbH")

    def test_someone_elses_application_is_a_404(self):
        theirs = make_application(self.other)
        url = f"{SKILL_URL}applications/{theirs.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        r = self.client.patch(url, {"notes": "x"}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_the_list_is_the_users_own(self):
        make_application(self.other)
        own = self._create().data["id"]
        ids = [row["id"] for row in self.client.get(f"{SKILL_URL}applications/").data]
        self.assertEqual(ids, [own])


def _words(n, word="built"):
    return " ".join([word] * n)


class SkillReviewTests(_SkillCase):
    """The machine half of a review: what a script can find without judgment."""

    def _review(self):
        r = self.client.get(f"{SKILL_URL}review/")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def _about(self, data, entry_id, check=None):
        return [
            f
            for f in data["findings"]
            if entry_id in f["ids"] and (check is None or f["check"] == check)
        ]

    def test_a_thin_job_that_names_skills_it_doesnt_link(self):
        docker = Skill.objects.create(user=self.user, name="Docker")
        job = Job.objects.create(
            user=self.user,
            title="Dev",
            company="Beta",
            started="2018-01-01",
            ended="2019-12-31",
            description="Built APIs in Python, shipped with docker.",
        )
        data = self._review()
        jid = f"job:{job.pk}"
        self.assertTrue(self._about(data, jid, "thin_description"))
        self.assertTrue(self._about(data, jid, "no_skills"))
        named = self._about(data, jid, "skill_named_not_linked")[0]
        self.assertIn(f"skill:{self.python.pk}", named["ids"])
        self.assertIn(f"skill:{docker.pk}", named["ids"])
        self.assertEqual(named["level"], "improve")

    def test_a_well_kept_job_has_no_findings(self):
        self.job.description = _words(30)
        self.job.save()
        self.assertEqual(self._about(self._review(), f"job:{self.job.pk}"), [])

    def test_short_names_match_only_as_written(self):
        go = Skill.objects.create(user=self.user, name="Go")
        self.job.description = _words(30) + " and we go to market"
        self.job.save()
        self.assertFalse(
            self._about(self._review(), f"skill:{go.pk}", "skill_named_not_linked")
        )
        self.job.description = _words(30) + " services written in Go"
        self.job.save()
        named = self._about(
            self._review(), f"job:{self.job.pk}", "skill_named_not_linked"
        )
        self.assertIn(f"skill:{go.pk}", named[0]["ids"])

    def test_a_skill_name_inside_a_longer_one_is_no_match(self):
        Skill.objects.create(user=self.user, name="Java")
        self.job.description = _words(30) + " JavaScript all day"
        self.job.save()
        found = self._about(
            self._review(), f"job:{self.job.pk}", "skill_named_not_linked"
        )
        self.assertEqual(found, [])

    def test_dates_that_contradict_themselves_come_first(self):
        Job.objects.create(
            user=self.user,
            title="Backwards",
            company="X",
            started="2021-01-01",
            ended="2020-01-01",
        )
        data = self._review()
        self.assertEqual(data["findings"][0]["check"], "ended_before_started")
        levels = [f["level"] for f in data["findings"]]
        self.assertEqual(
            levels, sorted(levels, key=["fix", "improve", "consider"].index)
        )
        self.assertEqual(data["counts"]["fix"], 1)

    def test_a_gap_between_education_and_the_first_job(self):
        gaps = [f for f in self._review()["findings"] if f["check"] == "gap"]
        self.assertTrue(
            any("2017-09" in f["say"] and "2020-01" in f["say"] for f in gaps)
        )

    def test_a_degree_named_but_recorded_as_not_completed(self):
        edu = Education.objects.create(
            user=self.user,
            institution="TU",
            started="2008-10-01",
            ended="2011-09-30",
            degree="B.Sc.",
        )
        found = self._about(
            self._review(), f"education:{edu.pk}", "degree_not_completed"
        )
        self.assertTrue(found)

    def test_near_duplicate_skills(self):
        a = Skill.objects.create(user=self.user, name="Node.js")
        b = Skill.objects.create(user=self.user, name="NodeJS")
        found = self._about(self._review(), f"skill:{a.pk}", "duplicate_skills")
        self.assertIn(f"skill:{b.pk}", found[0]["ids"])

    def test_a_skill_nothing_backs(self):
        lone = Skill.objects.create(user=self.user, name="Kotlin")
        found = self._about(self._review(), f"skill:{lone.pk}", "skill_unbacked")
        self.assertNotIn(f"skill:{self.python.pk}", found[0]["ids"])

    def test_only_the_users_own_career(self):
        Job.objects.create(
            user=self.other, title="Theirs", company="Y", started="2020-01-01"
        )
        text = json.dumps(self._review())
        self.assertNotIn("Theirs", text)


class SkillCareerChangeTests(_SkillCase):
    """Edits the user agreed to: all or nothing, through the entries' own serializers, logged
    with what they replaced so a batch can be undone."""

    URL = f"{SKILL_URL}career/changes/"

    def _seen(self, obj):
        obj.refresh_from_db()
        return obj.updated_at.isoformat()

    def _apply(self, *changes, **kw):
        return self.client.post(
            self.URL, {"changes": list(changes), **kw}, format="json"
        )

    def _describe(self, text="- Built the billing API\n- Ran it on Kubernetes"):
        return {
            "id": f"job:{self.job.pk}",
            "seen": self._seen(self.job),
            "why": "Says what was built.",
            "set": {"description": text},
        }

    def test_a_batch_edits_creates_and_links(self):
        change = {
            **self._describe(),
            "add": {"skills": ["new:k8s"]},
            "remove": {"skills": [f"skill:{self.python.pk}"]},
        }
        new = {
            "new": "skill",
            "ref": "k8s",
            "why": "Named in the job, missing as a skill.",
            "set": {"name": "Kubernetes", "category": "technical"},
            "add": {"builds_on": [f"skill:{self.python.pk}"]},
        }
        r = self._apply(change, new)
        self.assertEqual(r.status_code, 201, r.data)
        k8s = Skill.objects.get(user=self.user, name="Kubernetes")
        self.assertEqual(r.data["created"], {"new:k8s": f"skill:{k8s.pk}"})
        self.job.refresh_from_db()
        self.assertTrue(self.job.description.startswith("- Built"))
        self.assertEqual(list(self.job.skills.all()), [k8s])
        self.assertEqual(list(k8s.builds_on.all()), [self.python])
        logged = r.data["changes"][0]
        self.assertEqual(logged["before"]["skills"], [f"skill:{self.python.pk}"])
        self.assertEqual(logged["after"]["skills"], [f"skill:{k8s.pk}"])
        self.assertEqual(logged["label"], "Backend Dev at Acme")
        self.assertIsNone(r.data["changes"][1]["before"])
        self.assertEqual(r.data["labels"][f"skill:{k8s.pk}"], "Kubernetes")
        batch = CareerChange.objects.get(pk=r.data["id"])
        self.assertEqual(batch.source, CareerChange.Source.claude)

    def test_seen_from_the_career_round_trips(self):
        career = self.client.get(f"{SKILL_URL}career/").data
        job = career["entries"]["jobs"][0]
        r = self._apply(
            {
                "id": job["id"],
                "seen": job["updated_at"],
                "set": {"url": "https://acme.test"},
            }
        )
        self.assertEqual(r.status_code, 201, r.data)

    def test_the_job_type_round_trips_by_the_name_career_shows(self):
        # Live, 2026-11-21: career showed it as `type`, Claude set `type`, and the import 500d.
        job = self.client.get(f"{SKILL_URL}career/").data["entries"]["jobs"][0]
        self.assertNotIn("type", job)
        self.assertEqual(job["job_type"], "ft")
        r = self._apply(
            {"id": job["id"], "seen": job["updated_at"], "set": {"job_type": "ct"}}
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["changes"][0]["before"], {"job_type": "ft"})
        self.assertEqual(r.data["changes"][0]["after"], {"job_type": "ct"})

    def test_every_name_career_shows_is_settable_or_read_only(self):
        # What a change can't set: jac works it out, or the user edits it elsewhere in jac.
        read_only = {"id", "updated_at", "location", "projects", "completed", "years"}
        cert = Certification.objects.create(
            user=self.user, name="AWS SAA", issuer="AWS"
        )
        Project.objects.create(user=self.user, name="Billing", job=self.job)
        Language.objects.create(user=self.user, name="Deutsch", certification=cert)
        entries = self.client.get(f"{SKILL_URL}career/").data["entries"]
        for section, rows in entries.items():
            with self.subTest(section):
                self.assertTrue(rows)
                etype = rows[0]["id"].split(":")[0]
                takes = FIELDS[etype] | set(LISTS[etype]) | read_only
                self.assertEqual(set(rows[0]) - takes, set())

    def test_a_name_the_import_doesnt_take_is_a_400_not_a_500(self):
        # The snapshot reads every touched field before anything else looked at the names: one
        # the model lacks was an AttributeError, and the SPA could only say "try again".
        jid, seen = f"job:{self.job.pk}", self._seen(self.job)
        bad = {
            "type": {"id": jid, "seen": seen, "set": {"type": "ft"}},
            "tools": {"id": jid, "seen": seen, "add": {"tools": ["skill:1"]}},
            "years": {
                "id": f"skill:{self.python.pk}",
                "seen": self._seen(self.python),
                "set": {"years": 3},
            },
            "completed": {
                "id": f"education:{self.master.pk}",
                "seen": self._seen(self.master),
                "set": {"completed": False},
            },
        }
        for field, change in bad.items():
            with self.subTest(field):
                r = self._apply(change)
                self.assertEqual(r.status_code, 400, r.data)
                self.assertIn(field, r.data["changes"]["0"])
        r = self._apply(bad["type"])
        self.assertIn("job_type", r.data["changes"]["0"]["type"][0])
        self.assertFalse(CareerChange.objects.exists())

    def test_a_dry_run_answers_and_changes_nothing(self):
        r = self._apply(self._describe(), dry_run=True)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNone(r.data["id"])
        self.assertTrue(
            r.data["changes"][0]["after"]["description"].startswith("- Built")
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "")
        self.assertFalse(CareerChange.objects.exists())

    def test_a_stale_entry_refuses_the_whole_batch(self):
        seen = self._seen(self.python)
        stale = self._describe()
        Job.objects.filter(pk=self.job.pk).update(updated_at=timezone.now())
        fine = {
            "id": f"skill:{self.python.pk}",
            "seen": seen,
            "set": {"description": "x"},
        }
        r = self._apply(fine, stale)
        self.assertEqual(r.status_code, 409, r.data)
        self.assertEqual(r.data["stale"], [f"job:{self.job.pk}"])
        self.assertEqual(
            r.data["labels"], {f"job:{self.job.pk}": "Backend Dev at Acme"}
        )
        self.assertIn("Read the career again", r.data["detail"])
        self.python.refresh_from_db()
        self.assertEqual(self.python.description, "")

    def test_one_bad_change_applies_none(self):
        r = self._apply(
            self._describe(),
            {"new": "skill", "ref": "dup", "set": {"name": "Python"}},
        )
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("1", r.data["changes"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "")

    def test_what_a_change_may_not_do(self):
        jid = f"job:{self.job.pk}"
        seen = self._seen(self.job)
        bad = {
            "no seen": {"id": jid, "set": {"description": "x"}},
            "a job": {"new": "job", "ref": "j", "set": {"title": "Made up"}},
            "theirs": {
                "id": f"skill:{self.theirs.pk}",
                "seen": seen,
                "set": {"name": "x"},
            },
            "their skill": {
                "id": jid,
                "seen": seen,
                "add": {"skills": [f"skill:{self.theirs.pk}"]},
            },
            "a list via set": {"id": jid, "seen": seen, "set": {"skills": []}},
            "a field it lacks": {
                "id": jid,
                "seen": seen,
                "set": {"user": self.other.pk},
            },
            "nothing": {"id": jid, "seen": seen},
            "an unknown domain": {
                "id": jid,
                "seen": seen,
                "add": {"domains": ["Nope"]},
            },
        }
        for name, change in bad.items():
            with self.subTest(name):
                self.assertEqual(self._apply(change).status_code, 400)
        r = self._apply(self._describe(), self._describe())
        self.assertEqual(r.status_code, 400)
        self.assertIn("One change per entry", json.dumps(r.data))

    def test_the_favourite_cap_holds(self):
        for n in range(Job.FAVOURITE_LIMIT):
            Job.objects.create(
                user=self.user,
                title=f"Fav {n}",
                company="F",
                started="2015-01-01",
                favourite=True,
            )
        r = self._apply(
            {
                "id": f"job:{self.job.pk}",
                "seen": self._seen(self.job),
                "set": {"favourite": True},
            }
        )
        self.assertEqual(r.status_code, 400, r.data)

    def test_links_by_name_and_by_id(self):
        Domain.objects.create(user=self.user, name="Fintech")
        project = Project.objects.create(user=self.user, name="Billing")
        r = self._apply(
            {
                "id": f"project:{project.pk}",
                "seen": self._seen(project),
                "set": {"job": f"job:{self.job.pk}"},
                "add": {"domains": ["Fintech"]},
            },
            {
                "id": f"education:{self.master.pk}",
                "seen": self._seen(self.master),
                "set": {"degree_level": "bachelor"},
            },
        )
        self.assertEqual(r.status_code, 201, r.data)
        project.refresh_from_db()
        self.assertEqual(project.job, self.job)
        self.assertEqual([d.name for d in project.domains.all()], ["Fintech"])
        self.master.refresh_from_db()
        self.assertEqual(self.master.degree_level, Education.DegreeLevel.bachelor)
        self.assertEqual(r.data["changes"][1]["before"], {"degree_level": "master"})

    def test_undo_puts_it_back_and_drops_the_new_skill(self):
        r = self._apply(
            {**self._describe(), "add": {"skills": ["new:k8s"]}},
            {"new": "skill", "ref": "k8s", "set": {"name": "Kubernetes"}},
        )
        undo = self.client.post(f"{self.URL}{r.data['id']}/undo/")
        self.assertEqual(undo.status_code, 200, undo.data)
        self.assertIsNotNone(undo.data["undone_at"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "")
        self.assertEqual(list(self.job.skills.all()), [self.python])
        self.assertFalse(Skill.objects.filter(name="Kubernetes").exists())
        again = self.client.post(f"{self.URL}{r.data['id']}/undo/")
        self.assertEqual(again.status_code, 409)

    def test_undo_refuses_to_overwrite_a_later_edit(self):
        batch = self._apply(self._describe()).data["id"]
        self.job.refresh_from_db()
        self.job.description = "edited in jac"
        self.job.save()
        r = self.client.post(f"{self.URL}{batch}/undo/")
        self.assertEqual(r.status_code, 409, r.data)
        self.assertEqual(r.data["stale"], [f"job:{self.job.pk}"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "edited in jac")

    def test_the_session_imports_and_lists(self):
        self.client.credentials()
        self.client.force_login(self.user)
        r = self._apply(self._describe())
        self.assertEqual(r.status_code, 201, r.data)
        listed = self.client.get(self.URL).data
        self.assertEqual([b["id"] for b in listed], [r.data["id"]])
        self.assertEqual(listed[0]["source"], CareerChange.Source.imported)

    def test_someone_elses_batch(self):
        theirs = CareerChange.objects.create(user=self.other, changes=[], stamps={})
        self.assertEqual(self.client.get(self.URL).data, [])
        r = self.client.post(f"{self.URL}{theirs.pk}/undo/")
        self.assertEqual(r.status_code, 404)

    def test_the_log_keeps_the_latest_batches(self):
        for n in range(CareerChange.KEEP + 2):
            self._apply(
                {
                    "id": f"job:{self.job.pk}",
                    "seen": self._seen(self.job),
                    "set": {"url": f"https://a.test/{n}"},
                }
            )
        self.assertEqual(
            CareerChange.objects.filter(user=self.user).count(), CareerChange.KEEP
        )


# --- [fullstack]-claude-connector ---------------------------------------------------------

MCP_URL = "/api/jac/mcp/"


class _McpCase(_SkillCase):
    """JSON-RPC over one POST, as Claude's connector sends it (MCP Streamable HTTP, stateless,
    plain JSON answers)."""

    _next_id = 0

    def _rpc(self, method, params=None, url=MCP_URL, **extra):
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        return self.client.post(
            url,
            json.dumps(msg),
            content_type="application/json",
            HTTP_ACCEPT="application/json, text/event-stream",
            **extra,
        )

    def _call(self, name, arguments=None):
        """A tools/call: (the result's parsed JSON, isError)."""
        r = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertNotIn("error", body, body)
        result = body["result"]
        (content,) = result["content"]
        self.assertEqual(content["type"], "text")
        return json.loads(content["text"]), result.get("isError", False)


class McpProtocolTests(_McpCase):
    def test_initialize_echoes_a_known_version_and_names_the_tools_capability(self):
        r = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "claude-ai", "version": "1"},
            },
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r["Content-Type"].startswith("application/json"))
        result = r.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "jac")
        self.assertIn("get_career", result["instructions"])

    def test_an_unknown_version_gets_the_newest_one_jac_speaks(self):
        r = self._rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertRegex(r.json()["result"]["protocolVersion"], r"^20\d\d-\d\d-\d\d$")
        self.assertNotEqual(r.json()["result"]["protocolVersion"], "1999-01-01")

    def test_a_notification_is_a_202_without_a_body(self):
        r = self.client.post(
            MCP_URL,
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.content, b"")

    def test_ping(self):
        self.assertEqual(self._rpc("ping").json()["result"], {})

    def test_an_unknown_method_is_a_jsonrpc_error(self):
        body = self._rpc("resources/list").json()
        self.assertEqual(body["error"]["code"], -32601)
        self.assertEqual(body["id"], self._next_id)

    def test_broken_json_is_a_parse_error(self):
        r = self.client.post(MCP_URL, "{nope", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32700)

    def test_a_message_that_isnt_a_request_is_invalid(self):
        r = self.client.post(MCP_URL, "[]", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32600)

    def test_a_batch_answers_each_request(self):
        r = self.client.post(
            MCP_URL,
            json.dumps(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ]
            ),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual([m["id"] for m in r.json()], [1, 2])

    def test_get_offers_no_stream(self):
        self.assertEqual(self.client.get(MCP_URL).status_code, 405)

    def test_the_url_works_without_its_trailing_slash(self):
        r = self._rpc("ping", url=MCP_URL.rstrip("/"))
        self.assertEqual(r.status_code, 200, r.content)

    def test_tools_list_names_every_tool_with_a_schema_and_hints(self):
        tools = self._rpc("tools/list").json()["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(
            names,
            {
                "get_career",
                "get_career_review",
                "list_applications",
                "get_application",
                "save_application",
                "apply_career_changes",
                "list_career_changes",
                "undo_career_changes",
            },
        )
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertTrue(tool["description"])
                self.assertIn("readOnlyHint", tool["annotations"])
        read_only = {t["name"] for t in tools if t["annotations"]["readOnlyHint"]}
        self.assertEqual(
            read_only,
            {
                "get_career",
                "get_career_review",
                "list_applications",
                "get_application",
                "list_career_changes",
            },
        )

    def test_an_unknown_tool_is_invalid_params(self):
        body = self._rpc("tools/call", {"name": "drop_tables"}).json()
        self.assertEqual(body["error"]["code"], -32602)

    def test_arguments_that_arent_an_object_are_invalid_params(self):
        body = self._rpc(
            "tools/call", {"name": "get_application", "arguments": [1]}
        ).json()
        self.assertEqual(body["error"]["code"], -32602)

    def test_the_skill_names_every_tool(self):
        """SKILL.md is the connector's rulebook too (get_career hands it over): it has to say
        which tool does what, or Claude reaches for the script."""
        from jac.skill import SKILL_DIR

        text = (SKILL_DIR / "SKILL.md").read_text() + (
            SKILL_DIR / "REVIEW.md"
        ).read_text()
        tools = self._rpc("tools/list").json()["result"]["tools"]
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(
                    f"`{tool['name']}`" in text, f"the skill never names {tool['name']}"
                )


class McpAuthTests(_McpCase):
    """The connector is the token's: in the Authorization header, or as the URL's last
    segment for a connector dialog without request headers. Never the session."""

    def test_no_credentials_is_a_401_that_names_bearer(self):
        self.client.credentials()
        r = self._rpc("ping")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Bearer", r["WWW-Authenticate"])

    def test_a_revoked_token_is_a_401(self):
        self.token.delete()
        self.assertEqual(self._rpc("ping").status_code, 401)

    def test_the_key_in_the_url(self):
        self.client.credentials()
        r = self._rpc("ping", url=f"{MCP_URL}{self.key}/")
        self.assertEqual(r.status_code, 200, r.content)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_a_wrong_key_in_the_url_is_a_401(self):
        self.client.credentials()
        r = self._rpc("ping", url=f"{MCP_URL}jac_not-a-real-key/")
        self.assertEqual(r.status_code, 401)

    def test_the_session_does_not_open_it(self):
        self.client.credentials()
        self.client.force_login(self.user)
        self.assertIn(self._rpc("ping").status_code, (401, 403))

    def test_a_token_still_opens_nothing_else(self):
        self.client.credentials()
        for url in (APPS_URL, "/api/llm/configs/"):
            with self.subTest(url=url):
                r = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.key}")
                self.assertIn(r.status_code, (401, 403))


class McpToolTests(_McpCase):
    def _seen(self, obj):
        obj.refresh_from_db()
        return obj.updated_at.isoformat()

    def test_get_career_hands_over_the_rules_with_the_career(self):
        data, is_error = self._call("get_career")
        self.assertFalse(is_error)
        self.assertIn("Picking the CV entries", data["rules"])
        self.assertFalse(data["rules"].startswith("---"), "the frontmatter stays out")
        ids = [e["id"] for e in data["career"]["entries"]["jobs"]]
        self.assertEqual(ids, [f"job:{self.job.pk}"])
        self.assertEqual(
            data["career"]["highest_degree"], f"education:{self.master.pk}"
        )

    def test_get_career_carries_the_style_rules(self):
        self.user.personality.style_rules = (
            "No exclamation marks.\nNever say passionate."
        )
        self.user.personality.save()
        data, _ = self._call("get_career")
        self.assertEqual(
            data["career"]["letter_defaults"]["style_rules"],
            ["No exclamation marks.", "Never say passionate."],
        )
        self.assertIn("letter_defaults.style_rules", data["rules"])

    def test_get_career_review_hands_over_its_rules(self):
        data, is_error = self._call("get_career_review")
        self.assertFalse(is_error)
        self.assertIn("Rules for changes", data["rules"])
        self.assertIn("findings", data["review"])

    def test_save_application_creates_and_links_it(self):
        payload = self._payload()
        data, is_error = self._call("save_application", payload)
        self.assertFalse(is_error, data)
        app = JobApplication.objects.get(pk=data["id"])
        self.assertEqual(app.user, self.user)
        self.assertTrue(data["url"].endswith(f"/applications/{app.pk}"))
        self.assertIn("warnings", data)
        self.assertEqual(app.posting.title, "Backend-Entwickler:in")

    def test_save_application_warns_about_broken_style_rules(self):
        body = (
            "Ich bringe Erfahrung mit — und Neugier. "
            "Ich schreibe nicht nur Code, sondern auch Tests. " + _letter(180)
        )
        payload = self._payload(
            letter={"body": body, "tone": "formal", "closing": "Beste Grüße – Alice"}
        )
        data, is_error = self._call("save_application", payload)
        self.assertFalse(is_error, data)
        style = [w for w in data["warnings"] if "rule (" in w]
        self.assertEqual(len(style), 3, data["warnings"])
        body_dash, body_antithesis, closing_dash = style
        self.assertIn("no_dashes", body_dash)
        self.assertIn("Erfahrung mit — und Neugier.", body_dash)
        self.assertIn("no_antithesis", body_antithesis)
        self.assertIn("nicht nur Code, sondern auch Tests", body_antithesis)
        self.assertIn("The closing", closing_dash)

    def test_save_application_with_an_id_updates_it(self):
        created, _ = self._call("save_application", self._payload())
        data, is_error = self._call(
            "save_application",
            {"id": created["id"], "letter": {"body": _letter(word="Neu")}},
        )
        self.assertFalse(is_error, data)
        app = JobApplication.objects.get(pk=created["id"])
        self.assertTrue(app.cover_letter.startswith("Neu"))
        self.assertEqual(JobApplication.objects.filter(user=self.user).count(), 1)

    def test_save_application_refusals_come_back_as_tool_errors(self):
        for args in (
            {**self._payload(), "posting": {"title": "no text"}},
            {**self._payload(), "entries": [f"skill:{self.theirs.pk}"]},
            {"id": 999999, "letter": {"body": "x"}},
            {"id": "seven"},
        ):
            with self.subTest(args=args):
                data, is_error = self._call("save_application", args)
                self.assertTrue(is_error, data)
        self.assertFalse(JobApplication.objects.filter(user=self.user).exists())

    def test_someone_elses_application_is_not_found(self):
        theirs = make_application(self.other)
        for name, args in (
            ("get_application", {"id": theirs.pk}),
            ("save_application", {"id": theirs.pk, "notes": "mine now"}),
        ):
            with self.subTest(tool=name):
                _, is_error = self._call(name, args)
                self.assertTrue(is_error)
        theirs.refresh_from_db()
        self.assertNotEqual(theirs.notes, "mine now")

    def test_list_and_get_applications(self):
        created, _ = self._call("save_application", self._payload())
        listed, _ = self._call("list_applications")
        self.assertEqual([a["id"] for a in listed], [created["id"]])
        shown, is_error = self._call("get_application", {"id": created["id"]})
        self.assertFalse(is_error)
        self.assertEqual(shown["entries"], created["entries"])

    def test_apply_check_apply_list_and_undo_career_changes(self):
        change = {
            "id": f"job:{self.job.pk}",
            "seen": self._seen(self.job),
            "why": "Says what was built.",
            "set": {"description": "- Built the billing API"},
        }
        checked, is_error = self._call(
            "apply_career_changes", {"changes": [change], "dry_run": True}
        )
        self.assertFalse(is_error, checked)
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "")

        applied, is_error = self._call("apply_career_changes", {"changes": [change]})
        self.assertFalse(is_error, applied)
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "- Built the billing API")
        batch = CareerChange.objects.get(pk=applied["id"])
        self.assertEqual(batch.source, CareerChange.Source.claude)

        listed, _ = self._call("list_career_changes")
        self.assertEqual([b["id"] for b in listed], [batch.pk])

        undone, is_error = self._call("undo_career_changes", {"id": batch.pk})
        self.assertFalse(is_error, undone)
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, "")

    def test_a_stale_change_is_a_tool_error_naming_the_entry(self):
        change = {
            "id": f"job:{self.job.pk}",
            "seen": "2001-01-01T00:00:00+00:00",
            "set": {"description": "late"},
        }
        data, is_error = self._call("apply_career_changes", {"changes": [change]})
        self.assertTrue(is_error)
        self.assertEqual(data["stale"], [f"job:{self.job.pk}"])

    def test_a_bad_change_is_a_tool_error_by_position(self):
        change = {
            "id": f"job:{self.job.pk}",
            "seen": self._seen(self.job),
            "set": {"type": "ft"},
        }
        data, is_error = self._call("apply_career_changes", {"changes": [change]})
        self.assertTrue(is_error)
        self.assertIn("changes", data)

    def test_undo_of_someone_elses_batch_is_not_found(self):
        theirs = CareerChange.objects.create(user=self.other, changes=[], stamps={})
        _, is_error = self._call("undo_career_changes", {"id": theirs.pk})
        self.assertTrue(is_error)
