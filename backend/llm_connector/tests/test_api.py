"""Connector API surface: per-provider credential CRUD, the executors endpoint
(the generate panel's single source), the connectivity check, the spend audit.

Target API = `[backend]-executor-connector`.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from llm_connector.catalog import CATALOG, models_for
from llm_connector.conf import HIRSCHAI_PROVIDER, hirschai_row
from llm_connector.models import LLMConfig, LLMRequestLog

from ._helpers import TEST_HIRSCHAI, fake_row, keyed_row

CONFIGS_URL = "/api/llm/configs/"
EXECUTORS_URL = "/api/llm/executors/"


class LLMConfigApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")

    def setUp(self):
        self.client.force_login(self.alice)

    def test_create_returns_row_without_key_material(self):
        r = self.client.post(
            CONFIGS_URL,
            {
                "provider": "anthropic",
                "api_key": "sk-x",
                "default": True,
                "transfer_consent": True,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["provider"], "anthropic")
        self.assertTrue(r.data["default"])
        self.assertTrue(r.data["has_api_key"])
        self.assertNotIn("api_key", r.data)
        # The thin model is the API: no url/extra/max_tokens surface for users.
        self.assertNotIn("url", r.data)
        self.assertNotIn("extra", r.data)
        row = LLMConfig.objects.get(user=self.alice, provider="anthropic")
        self.assertEqual(row.api_key, "sk-x")

    def test_ollama_provider_is_rejected(self):
        r = self.client.post(
            CONFIGS_URL,
            {"provider": "ollama", "api_key": "k", "transfer_consent": True},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_duplicate_provider_is_rejected(self):
        self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "a", "transfer_consent": True},
            format="json",
        )
        r = self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "b", "transfer_consent": True},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_default_is_exclusive_through_the_api(self):
        self.client.post(
            CONFIGS_URL,
            {
                "provider": "anthropic",
                "api_key": "a",
                "default": True,
                "transfer_consent": True,
            },
            format="json",
        )
        self.client.post(
            CONFIGS_URL,
            {
                "provider": "openai",
                "api_key": "b",
                "default": True,
                "transfer_consent": True,
            },
            format="json",
        )
        rows = self.client.get(CONFIGS_URL).data["results"]
        defaults = [row["provider"] for row in rows if row["default"]]
        self.assertEqual(defaults, ["openai"])

    def test_patch_without_key_preserves_it(self):
        create = self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "sk-keep", "transfer_consent": True},
            format="json",
        )
        pk = create.data["id"]
        r = self.client.patch(
            f"{CONFIGS_URL}{pk}/", {"default": True, "api_key": ""}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["has_api_key"])
        self.assertEqual(
            LLMConfig.objects.get(pk=pk).api_key, "sk-keep"
        )

    @override_settings(HIRSCHAI=TEST_HIRSCHAI)
    def test_list_shows_only_own_rows_never_the_system_row(self):
        hirschai_row()  # the system row exists…
        fake_row(self.bob, provider="anthropic", api_key="sk-b")
        mine = fake_row(self.alice, provider="openai", api_key="sk-a")
        rows = self.client.get(CONFIGS_URL).data["results"]
        self.assertEqual([row["id"] for row in rows], [mine.pk])

    def test_cross_user_detail_is_404(self):
        theirs = fake_row(self.bob, provider="anthropic")
        r = self.client.get(f"{CONFIGS_URL}{theirs.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_unauthenticated_request_is_forbidden(self):
        self.client.logout()
        self.assertEqual(self.client.get(CONFIGS_URL).status_code, 403)


class TransferConsentTests(APITestCase):
    """A key sends data out of the EU, so saving one takes explicit consent (Art. 49(1)(a)), with a
    timestamp as the proof Art. 7(1) asks for. Deleting the row is the withdrawal."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")

    def setUp(self):
        self.client.force_login(self.alice)

    def _post(self, **body):
        return self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "sk-x", **body},
            format="json",
        )

    def test_a_key_without_consent_is_refused(self):
        r = self._post()
        self.assertEqual(r.status_code, 400)
        self.assertIn("transfer_consent", r.data)
        self.assertFalse(LLMConfig.objects.filter(user=self.alice).exists())

    def test_an_unticked_box_counts_as_no_consent(self):
        self.assertEqual(self._post(transfer_consent=False).status_code, 400)

    def test_consent_is_stamped_and_readable(self):
        r = self._post(transfer_consent=True)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNotNone(LLMConfig.objects.get(user=self.alice).transfer_consent_at)
        self.assertTrue(r.data["transfer_consent_at"])
        self.assertNotIn("transfer_consent", r.data)

    def test_the_client_cannot_write_the_timestamp(self):
        self._post(transfer_consent=True, transfer_consent_at="2020-01-01T00:00:00Z")
        row = LLMConfig.objects.get(user=self.alice)
        self.assertNotEqual(row.transfer_consent_at.year, 2020)

    def test_replacing_a_key_keeps_the_first_consent(self):
        pk = self._post(transfer_consent=True).data["id"]
        first = LLMConfig.objects.get(pk=pk).transfer_consent_at
        r = self.client.patch(
            f"{CONFIGS_URL}{pk}/", {"api_key": "sk-new"}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.data)
        row = LLMConfig.objects.get(pk=pk)
        self.assertEqual(row.api_key, "sk-new")
        self.assertEqual(row.transfer_consent_at, first)

    def test_adding_a_key_to_a_row_without_consent_is_refused(self):
        pk = self.client.post(
            CONFIGS_URL, {"provider": "anthropic"}, format="json"
        ).data["id"]
        r = self.client.patch(f"{CONFIGS_URL}{pk}/", {"api_key": "sk-x"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(LLMConfig.objects.get(pk=pk).has_api_key)

    def test_making_a_row_default_needs_no_new_consent(self):
        pk = self._post(transfer_consent=True).data["id"]
        r = self.client.patch(f"{CONFIGS_URL}{pk}/", {"default": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    def test_deleting_the_key_withdraws_the_consent(self):
        pk = self._post(transfer_consent=True).data["id"]
        self.client.delete(f"{CONFIGS_URL}{pk}/")
        self.assertEqual(self._post().status_code, 400)

    def test_consent_does_not_move_to_another_provider(self):
        """Consent names one provider. Switching the row would send the data elsewhere unasked."""
        pk = self._post(transfer_consent=True).data["id"]
        r = self.client.patch(
            f"{CONFIGS_URL}{pk}/",
            {"provider": "openai", "api_key": "sk-y"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("provider", r.data)
        row = LLMConfig.objects.get(pk=pk)
        self.assertEqual((row.provider, row.api_key), ("anthropic", "sk-x"))

    def test_resending_the_same_provider_is_fine(self):
        # The SPA sends the row's own provider with every PATCH.
        pk = self._post(transfer_consent=True).data["id"]
        r = self.client.patch(
            f"{CONFIGS_URL}{pk}/",
            {"provider": "anthropic", "api_key": "sk-y"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class PrewarmApiTests(APITestCase):
    URL = "/api/llm/prewarm/"
    REQUEST = "llm_connector.views.request_prewarm"

    def setUp(self):
        cache.clear()  # scoped-throttle history lives in the default cache
        self.user = User.objects.create_user(username="alice", password="pw")

    def test_someone_on_hirschai_asks_for_it(self):
        self.client.force_login(self.user)
        with patch(self.REQUEST) as prewarm:
            r = self.client.post(self.URL)
        self.assertEqual(r.status_code, 204)
        prewarm.assert_called_once()

    def test_someone_on_their_own_key_never_touches_the_tower(self):
        fake_row(self.user, "anthropic", default=True)
        self.client.force_login(self.user)
        with patch(self.REQUEST) as prewarm:
            r = self.client.post(self.URL)
        self.assertEqual(r.status_code, 204)
        prewarm.assert_not_called()

    def test_a_key_without_consent_runs_on_hirschai_so_it_wakes_the_tower(self):
        keyed_row(self.user, default=True, consent=False)
        self.client.force_login(self.user)
        with patch(self.REQUEST) as prewarm:
            self.client.post(self.URL)
        prewarm.assert_called_once()

    def test_anonymous_is_refused(self):
        with patch(self.REQUEST) as prewarm:
            r = self.client.post(self.URL)
        self.assertEqual(r.status_code, 403)
        prewarm.assert_not_called()


class ExecutorListApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")

    def _rows(self, *, reachable=True):
        self.client.force_login(self.alice)
        with patch("llm_connector.views.hirschai_state", return_value=reachable):
            r = self.client.get(EXECUTORS_URL)
        self.assertEqual(r.status_code, 200)
        return {row["provider"]: row for row in r.data}

    def test_hirschai_row_shape(self):
        row = self._rows()[HIRSCHAI_PROVIDER]
        self.assertEqual(row["label"], "HirschAI")
        self.assertTrue(row["self_hosted"])
        self.assertTrue(row["configured"])
        self.assertTrue(row["reachable"])
        self.assertEqual(row["models"], [])
        self.assertEqual(row["modes"], ["standard"])  # high is commercial-only

    def test_hirschai_reports_offline(self):
        self.assertFalse(self._rows(reachable=False)[HIRSCHAI_PROVIDER]["reachable"])

    def test_commercial_rows_cover_the_catalog(self):
        rows = self._rows()
        for provider in CATALOG:
            row = rows[provider]
            self.assertFalse(row["configured"])  # nothing configured yet
            self.assertFalse(row["default"])
            self.assertEqual(row["models"], models_for(provider))
            self.assertEqual(row["modes"], ["standard", "high"])

    def test_configured_default_marks_the_row_and_unmarks_hirschai(self):
        keyed_row(self.alice, default=True)
        rows = self._rows()
        self.assertTrue(rows["anthropic"]["configured"])
        self.assertTrue(rows["anthropic"]["default"])
        self.assertFalse(rows[HIRSCHAI_PROVIDER]["default"])

    def test_a_key_without_consent_is_not_configured(self):
        """Set in the admin, or older than the consent: nothing runs on it, so the panel
        mustn't offer it, and HirschAI stays the default."""
        keyed_row(self.alice, default=True, consent=False)
        rows = self._rows()
        self.assertFalse(rows["anthropic"]["configured"])
        self.assertFalse(rows["anthropic"]["default"])
        self.assertTrue(rows[HIRSCHAI_PROVIDER]["default"])

    def test_hirschai_is_default_without_a_commercial_default(self):
        self.assertTrue(self._rows()[HIRSCHAI_PROVIDER]["default"])

    def test_configs_do_not_leak_across_users(self):
        keyed_row(self.bob, "openai", api_key="sk-b", default=True)
        rows = self._rows()
        self.assertFalse(rows["openai"]["configured"])
        self.assertFalse(rows["openai"]["default"])

    def test_unauthenticated_request_is_forbidden(self):
        self.assertEqual(self.client.get(EXECUTORS_URL).status_code, 403)


@override_settings(LLM_LOGGING=False)
class LLMConfigCheckActionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")
        cls.row = fake_row(cls.alice, model="fake-1")

    def setUp(self):
        self.client.force_login(self.alice)

    def _check(self, pk):
        return self.client.post(f"{CONFIGS_URL}{pk}/check/")

    def test_success_returns_ok_and_latency(self):
        r = self._check(self.row.pk)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["ok"])
        self.assertIn("latency_ms", r.data)

    def test_failure_is_a_result_not_an_http_error(self):
        with patch(
            "llm_connector.views.LLMClient", side_effect=RuntimeError("boom")
        ):
            r = self._check(self.row.pk)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["ok"])
        self.assertIn("boom", r.data["error"])

    def test_other_users_config_is_404(self):
        self.client.force_login(self.bob)
        self.assertEqual(self._check(self.row.pk).status_code, 404)

    def test_a_key_without_consent_is_not_tried(self):
        row = fake_row(self.alice, provider="fakesearch", consent=False)
        r = self._check(row.pk)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["ok"])
        self.assertIn("agree to the transfer", r.data["error"])


class LLMRequestLogApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")
        cls.mine = LLMRequestLog.objects.create(
            user=cls.alice, provider="fake", model="fake-1",
            request_messages=[{"role": "user", "content": "hi"}],
        )
        LLMRequestLog.objects.create(
            user=cls.bob, provider="fake", model="fake-1",
            request_messages=[{"role": "user", "content": "yo"}],
        )

    def test_list_returns_only_own_logs(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/llm/request-logs/")
        rows = r.data["results"]
        self.assertEqual([row["id"] for row in rows], [self.mine.pk])
        self.assertEqual(rows[0]["provider"], "fake")

    def test_retrieve_other_users_log_is_404(self):
        self.client.force_login(self.alice)
        other = LLMRequestLog.objects.get(user=self.bob)
        r = self.client.get(f"/api/llm/request-logs/{other.pk}/")
        self.assertEqual(r.status_code, 404)

    def _error_as_shown(self, provider, error):
        row = LLMRequestLog.objects.create(
            user=self.alice, provider=provider, model="m", request_messages=[], error=error
        )
        self.client.force_login(self.alice)
        return row, self.client.get(f"/api/llm/request-logs/{row.pk}/").data["error"]

    def test_hirschai_errors_reach_the_user_as_a_category_only(self):
        """They name the model server's address. The row keeps the text for the operator."""
        text = "LLM ollama provider could not reach http://model-server:11434/api/chat: refused"
        row, shown = self._error_as_shown(HIRSCHAI_PROVIDER, text)
        self.assertTrue(shown)
        self.assertNotIn("model-server", shown)
        row.refresh_from_db()
        self.assertEqual(row.error, text)

    def test_a_users_own_provider_errors_stay_readable(self):
        _, shown = self._error_as_shown("anthropic", "Your credit balance is too low.")
        self.assertEqual(shown, "Your credit balance is too low.")

    def test_a_hirschai_row_without_an_error_shows_none(self):
        _, shown = self._error_as_shown(HIRSCHAI_PROVIDER, "")
        self.assertEqual(shown, "")


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ExecutorKnobAdvertisingTests(APITestCase):
    """[fullstack]-model-knobs: the executors endpoint advertises each provider's
    knob spec so the generate panel renders controls from data, never hardcoded."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="pw")

    def setUp(self):
        self.client.force_login(self.user)

    def test_commercial_rows_carry_the_knob_spec_hirschai_none(self):
        with patch("llm_connector.views.hirschai_state", return_value=True):
            rows = {r["provider"]: r for r in self.client.get("/api/llm/executors/").data}
        self.assertEqual(rows["ollama"]["knobs"], {})
        # Anthropic keeps both knobs; OpenAI is reasoning-only (effort, no temperature).
        self.assertIn("effort", rows["anthropic"]["knobs"])
        self.assertIn("choices", rows["anthropic"]["knobs"]["effort"])
        self.assertIn("temperature", rows["anthropic"]["knobs"])
        self.assertIn("effort", rows["openai"]["knobs"])
        self.assertNotIn("temperature", rows["openai"]["knobs"])
