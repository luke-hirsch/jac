"""Connector API surface: per-provider credential CRUD, the executors endpoint
(the generate panel's single source), the connectivity check, the spend audit.

Target API = `[backend]-executor-connector`; the model lists per key are
`[fullstack]-live-model-catalog`.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from llm_connector.base import LLMTransportError
from llm_connector.catalog import COMMERCIAL_PROVIDERS
from llm_connector.conf import HIRSCHAI_PROVIDER, hirschai_row
from llm_connector.models import LLMConfig, LLMRequestLog, TransferConsent
from llm_connector.providers.anthropic import AnthropicAdapter

from ._helpers import ANTHROPIC_MODELS, TEST_HIRSCHAI, FakeAdapter, fake_row, keyed_row

CONFIGS_URL = "/api/llm/configs/"
CONSENT_URL = "/api/llm/consent/"
EXECUTORS_URL = "/api/llm/executors/"


def _no_listing(test):
    """Saving a key asks the provider for its models: patched out where a test isn't about
    that, so no test reaches the network."""
    patcher = patch("llm_connector.serializers.refresh_models", return_value=[])
    test.addCleanup(patcher.stop)
    return patcher.start()


class LLMConfigApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")

    def setUp(self):
        self.client.force_login(self.alice)
        _no_listing(self)

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
        # The thin model is the API: no url/extra/max_tokens surface for users, and the
        # model list reaches the SPA through the executors endpoint, as a count here.
        self.assertNotIn("url", r.data)
        self.assertNotIn("extra", r.data)
        self.assertNotIn("models", r.data)
        self.assertIn("model_count", r.data)
        self.assertIn("models_checked_at", r.data)
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
        _no_listing(self)

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

    def test_deleting_a_key_keeps_the_consent(self):
        """The consent is kept like a cookie choice (Lukas, 2026-11-21: "I have to click it every
        time"): a deleted key sends nothing, and the next key needs no second click."""
        pk = self._post(transfer_consent=True).data["id"]
        self.client.delete(f"{CONFIGS_URL}{pk}/")
        self.assertEqual(self._post().status_code, 201)

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


class KeySaveListsModelsTests(APITestCase):
    """Saving a key asks the provider which models it reaches, synchronously, so the generate
    panel offers them at once. A provider that doesn't answer never stops the key."""

    LIST = "llm_connector.providers.anthropic.AnthropicAdapter.list_models"

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")

    def setUp(self):
        self.client.force_login(self.alice)

    def _post(self):
        return self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "sk-x", "transfer_consent": True},
            format="json",
        )

    def test_saving_a_key_lists_its_models(self):
        with patch(self.LIST, return_value=ANTHROPIC_MODELS) as listing:
            r = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        listing.assert_called_once()
        self.assertEqual(r.data["model_count"], len(ANTHROPIC_MODELS))
        self.assertTrue(r.data["models_checked_at"])
        row = LLMConfig.objects.get(user=self.alice)
        self.assertEqual(row.models, ANTHROPIC_MODELS)

    def test_a_provider_that_doesnt_answer_never_blocks_the_key(self):
        with (
            patch(self.LIST, side_effect=LLMTransportError("provider down")),
            self.assertLogs("llm_connector.serializers", "WARNING"),
        ):
            r = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["has_api_key"])
        self.assertEqual(r.data["model_count"], 0)
        self.assertIsNone(r.data["models_checked_at"])

    def test_a_new_key_drops_the_old_keys_list(self):
        """Another account at the same provider may reach other models."""
        row = keyed_row(self.alice, models=ANTHROPIC_MODELS)
        with (
            patch(self.LIST, side_effect=LLMTransportError("provider down")),
            self.assertLogs("llm_connector.serializers", "WARNING"),
        ):
            r = self.client.patch(
                f"{CONFIGS_URL}{row.pk}/", {"api_key": "sk-new"}, format="json"
            )
        self.assertEqual(r.status_code, 200, r.data)
        row.refresh_from_db()
        self.assertEqual((row.models, row.models_checked_at), ([], None))

    def test_a_save_without_a_key_keeps_the_list_and_asks_nobody(self):
        row = keyed_row(self.alice, models=ANTHROPIC_MODELS)
        with patch(self.LIST) as listing:
            r = self.client.patch(
                f"{CONFIGS_URL}{row.pk}/", {"default": True}, format="json"
            )
        self.assertEqual(r.status_code, 200, r.data)
        listing.assert_not_called()
        self.assertEqual(r.data["model_count"], len(ANTHROPIC_MODELS))


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
        keyed_row(self.user, default=True, models=ANTHROPIC_MODELS)
        self.client.force_login(self.user)
        with patch(self.REQUEST) as prewarm:
            r = self.client.post(self.URL)
        self.assertEqual(r.status_code, 204)
        prewarm.assert_not_called()

    def test_a_default_key_without_a_model_list_still_isnt_the_tower(self):
        keyed_row(self.user, default=True)
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

    def test_unconfigured_providers_offer_no_models(self):
        rows = self._rows()
        for provider in COMMERCIAL_PROVIDERS:
            row = rows[provider]
            self.assertFalse(row["configured"])  # nothing configured yet
            self.assertFalse(row["default"])
            self.assertEqual(row["models"], [])
            self.assertEqual(row["modes"], ["standard", "high"])

    def test_a_configured_key_serves_its_stored_list(self):
        keyed_row(self.alice, models=ANTHROPIC_MODELS)
        models = self._rows()["anthropic"]["models"]
        self.assertEqual([m["id"] for m in models], [m["id"] for m in ANTHROPIC_MODELS])
        self.assertEqual([m["id"] for m in models if m.get("default")], ["claude-sonnet-5"])
        self.assertEqual(models[0]["label"], "Claude Opus 4.8")
        for model in models:
            with self.subTest(model["id"]):
                self.assertNotIn("thinking", model)  # the adapter's, not the SPA's

    def test_the_list_is_read_never_fetched(self):
        """The SPA polls this every 30 s: it must never cost a provider call."""
        keyed_row(self.alice, models=ANTHROPIC_MODELS)
        with patch.object(AnthropicAdapter, "list_models") as listing:
            self._rows()
        listing.assert_not_called()

    def test_a_key_without_consent_offers_no_models(self):
        keyed_row(self.alice, consent=False, models=ANTHROPIC_MODELS)
        self.assertEqual(self._rows()["anthropic"]["models"], [])

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
        FakeAdapter.instances.clear()

    def _check(self, pk):
        return self.client.post(f"{CONFIGS_URL}{pk}/check/")

    def test_success_returns_ok_latency_and_the_model_count(self):
        r = self._check(self.row.pk)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["ok"])
        self.assertIn("latency_ms", r.data)
        self.assertEqual(r.data["models"], 1)
        row = LLMConfig.objects.get(pk=self.row.pk)
        self.assertEqual([m["id"] for m in row.models], ["fake-1"])
        self.assertIsNotNone(row.models_checked_at)

    def test_failure_is_a_result_not_an_http_error(self):
        with patch(
            "llm_connector.views.LLMClient", side_effect=RuntimeError("boom")
        ):
            r = self._check(self.row.pk)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["ok"])
        self.assertIn("boom", r.data["error"])
        self.assertEqual(r.data["models"], 1)  # the listing before it went through

    def test_a_listing_that_fails_keeps_the_last_list(self):
        row = fake_row(self.alice, provider="fakesearch")
        row.models = [{"id": "fs-old", "label": "Old", "knobs": {}}]
        row.save()
        with patch.object(
            FakeAdapter, "list_models", side_effect=RuntimeError("listing down")
        ):
            r = self._check(row.pk)
        self.assertFalse(r.data["ok"])
        self.assertIn("listing down", r.data["error"])
        self.assertEqual(r.data["models"], 1)
        row.refresh_from_db()
        self.assertEqual([m["id"] for m in row.models], ["fs-old"])

    def test_a_commercial_key_pongs_on_the_default_pick_of_its_fresh_list(self):
        row = keyed_row(self.alice)
        with (
            patch.object(AnthropicAdapter, "list_models", return_value=ANTHROPIC_MODELS),
            patch.object(
                AnthropicAdapter, "complete", autospec=True, return_value="pong"
            ) as pong,
        ):
            r = self._check(row.pk)
        self.assertTrue(r.data["ok"], r.data)
        self.assertEqual(r.data["models"], len(ANTHROPIC_MODELS))
        self.assertEqual(pong.call_args.args[0]._model, "claude-sonnet-5")

    def test_other_users_config_is_404(self):
        self.client.force_login(self.bob)
        self.assertEqual(self._check(self.row.pk).status_code, 404)

    def test_a_key_without_consent_is_not_tried(self):
        row = fake_row(self.alice, provider="fakesearch", consent=False)
        r = self._check(row.pk)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["ok"])
        self.assertIn("agree to the transfer", r.data["error"])
        self.assertEqual(FakeAdapter.instances, [])  # not even listed


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
    """[fullstack]-model-knobs: the executors endpoint advertises each MODEL's knob spec
    (from the provider's capability data) so the generate panel renders controls from data,
    never hardcoded. The row-level `knobs` field is gone."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="pw")
        keyed_row(cls.user, models=ANTHROPIC_MODELS)

    def setUp(self):
        self.client.force_login(self.user)

    def test_knobs_ride_on_each_model_and_hirschai_has_none(self):
        with patch("llm_connector.views.hirschai_state", return_value=True):
            rows = {r["provider"]: r for r in self.client.get(EXECUTORS_URL).data}
        for row in rows.values():
            with self.subTest(row["provider"]):
                self.assertNotIn("knobs", row)
        self.assertEqual(rows["ollama"]["models"], [])
        knobs = {m["id"]: m["knobs"] for m in rows["anthropic"]["models"]}
        self.assertEqual(
            knobs["claude-opus-4-8"]["effort"]["choices"],
            ["low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(
            knobs["claude-haiku-4-5-20251001"]["effort"]["choices"],
            ["low", "medium", "high"],
        )
        for model_knobs in knobs.values():
            self.assertNotIn("temperature", model_knobs)


class KeptConsentTests(APITestCase):
    """The consent to the transfer is given once and kept with its time (Art. 7(1)), like a cookie
    choice. Withdrawing is as easy, and deletes the keys it covered: nothing may go out on a
    consent that's gone."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")

    def setUp(self):
        self.client.force_login(self.alice)
        _no_listing(self)

    def _key(self, **body):
        return self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "sk-x", **body},
            format="json",
        )

    def test_there_is_none_to_begin_with(self):
        r = self.client.get(CONSENT_URL)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["given_at"])

    def test_agreeing_once_is_kept(self):
        r = self.client.post(CONSENT_URL)
        self.assertEqual(r.status_code, 201, r.data)
        given = r.data["given_at"]
        self.assertTrue(given)
        self.assertEqual(self.client.get(CONSENT_URL).data["given_at"], given)
        again = self.client.post(CONSENT_URL)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(
            again.data["given_at"], given, "a second click keeps the first time"
        )

    def test_a_key_needs_no_flag_once_agreed(self):
        self.client.post(CONSENT_URL)
        r = self._key()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNotNone(LLMConfig.objects.get(user=self.alice).transfer_consent_at)

    def test_the_flag_on_a_key_records_the_consent(self):
        self.assertEqual(self._key(transfer_consent=True).status_code, 201)
        self.assertIsNotNone(self.client.get(CONSENT_URL).data["given_at"])

    def test_withdrawing_deletes_the_keys_and_asks_again(self):
        self._key(transfer_consent=True)
        r = self.client.delete(CONSENT_URL)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(LLMConfig.objects.filter(user=self.alice).exists())
        self.assertIsNone(self.client.get(CONSENT_URL).data["given_at"])
        self.assertEqual(self._key().status_code, 400)

    def test_withdrawing_touches_no_one_else(self):
        TransferConsent.give(self.bob, TransferConsent.Scope.providers)
        LLMConfig.objects.create(user=self.bob, provider="anthropic")
        self.client.post(CONSENT_URL)
        self.client.delete(CONSENT_URL)
        self.assertTrue(LLMConfig.objects.filter(user=self.bob).exists())
        self.assertIsNotNone(
            TransferConsent.given(self.bob, TransferConsent.Scope.providers)
        )

    def test_it_is_the_sessions(self):
        self.client.logout()
        for method in ("get", "post", "delete"):
            with self.subTest(method=method):
                r = getattr(self.client, method)(CONSENT_URL)
                self.assertIn(r.status_code, (401, 403))
