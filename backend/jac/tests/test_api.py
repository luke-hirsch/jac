"""View-layer tests: generation-run create/read/cancel, application auto-run,
chat/rewrite executor plumbing, and the pinned-entries API.

Target API = `[backend]-pipeline-single-executor` and `[backend]-entry-pins`.
Celery is always mocked (apply_async must return a STRING id — a Mock poisons
the task_id CharField); the tower probe is mocked wherever resolution consults it.
"""

from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from jac.models import GenerationRun, Mode, Skill
from llm_connector.catalog import default_model
from llm_connector.executor import Executor
from llm_connector.models import LLMConfig

from ._helpers import TEST_HIRSCHAI, make_application, make_user

RUNS_URL = "/api/jac/generations/"
APPS_URL = "/api/jac/applications/"


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
        with patch("llm_connector.probe.hirschai_reachable", return_value=True):
            response, _ = self._post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["provider"], "ollama")

    def test_no_executor_available_is_a_400(self):
        with patch("llm_connector.probe.hirschai_reachable", return_value=False):
            response, _ = self._post()
        self.assertEqual(response.status_code, 400)

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
        cls.run = GenerationRun.objects.create(
            job_application=cls.application,
            mode=Mode.high,
            provider="anthropic",
            model="claude-sonnet-5",
        )
        GenerationRun.objects.create(job_application=make_application(cls.other))

    def setUp(self):
        self.client.force_login(self.user)

    def test_detail_shape_names_the_executor(self):
        r = self.client.get(f"{RUNS_URL}{self.run.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            set(r.data),
            {
                "id", "job_application", "status", "stage", "error", "result",
                "mode", "provider", "model", "posting_title",
                "created_at", "updated_at",
            },
        )
        self.assertEqual(r.data["provider"], "anthropic")
        self.assertEqual(r.data["model"], "claude-sonnet-5")

    def test_list_is_owner_scoped(self):
        r = self.client.get(RUNS_URL)
        self.assertEqual([row["id"] for row in r.data], [self.run.pk])


class GenerationRunCancelTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)

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

    def test_create_without_an_executor_creates_no_run(self):
        with patch("jac.views.default_executor", return_value=None):
            r = self.client.post(
                APPS_URL, {"posting_text": "We need a Python engineer."},
                format="json",
            )
        self.assertEqual(r.status_code, 201)
        self.assertFalse(
            GenerationRun.objects.filter(job_application_id=r.data["id"]).exists()
        )

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

    def test_any_executor_chats(self):
        # No strength gate anymore — HirschAI chats too; the pick is the user's.
        with (
            patch("jac.views.resolve_executor", return_value=Executor("ollama")),
            patch("jac.views.LetterChat") as chat_cls,
        ):
            chat_cls.return_value.reply.return_value = {
                "reply": "warmer it is", "revision": None,
            }
            chat_cls._MAX_TRANSCRIPT_CHARS = 6000
            chat_cls._MAX_BODY_CHARS = 8000
            r = self._chat(provider="ollama")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["reply"], "warmer it is")

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


class ParagraphRewriteViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.application = make_application(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"{APPS_URL}{self.application.pk}/rewrite/"

    def test_rewrite_round_trip(self):
        with (
            patch("jac.views.resolve_executor", return_value=Executor("ollama")),
            patch("jac.views.ParagraphRewrite") as rewrite_cls,
        ):
            rewrite_cls.return_value.rewrite.return_value = "New text."
            rewrite_cls._MAX_CHARS = 4000
            r = self.client.post(self.url, {"text": "Old text."}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["text"], "New text.")

    def test_blank_text_is_a_400(self):
        r = self.client.post(self.url, {"text": "   "}, format="json")
        self.assertEqual(r.status_code, 400)


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
