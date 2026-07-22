"""Connector API surface: per-provider credential CRUD, the executors endpoint
(the generate panel's single source), the connectivity check, the spend audit.

Target API = `[backend]-executor-connector`.
"""

import unittest
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from llm_connector.catalog import CATALOG, models_for
from llm_connector.conf import HIRSCHAI_PROVIDER, hirschai_row
from llm_connector.models import LLMConfig, LLMRequestLog

from ._helpers import TEST_HIRSCHAI, fake_row

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
            {"provider": "anthropic", "api_key": "sk-x", "default": True},
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
            CONFIGS_URL, {"provider": "ollama", "api_key": "k"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_duplicate_provider_is_rejected(self):
        self.client.post(
            CONFIGS_URL, {"provider": "anthropic", "api_key": "a"}, format="json"
        )
        r = self.client.post(
            CONFIGS_URL, {"provider": "anthropic", "api_key": "b"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_default_is_exclusive_through_the_api(self):
        self.client.post(
            CONFIGS_URL,
            {"provider": "anthropic", "api_key": "a", "default": True},
            format="json",
        )
        self.client.post(
            CONFIGS_URL,
            {"provider": "openai", "api_key": "b", "default": True},
            format="json",
        )
        rows = self.client.get(CONFIGS_URL).data
        defaults = [row["provider"] for row in rows if row["default"]]
        self.assertEqual(defaults, ["openai"])

    def test_patch_without_key_preserves_it(self):
        create = self.client.post(
            CONFIGS_URL, {"provider": "anthropic", "api_key": "sk-keep"}, format="json"
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
        rows = self.client.get(CONFIGS_URL).data
        self.assertEqual([row["id"] for row in rows], [mine.pk])

    def test_cross_user_detail_is_404(self):
        theirs = fake_row(self.bob, provider="anthropic")
        r = self.client.get(f"{CONFIGS_URL}{theirs.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_unauthenticated_request_is_forbidden(self):
        self.client.logout()
        self.assertEqual(self.client.get(CONFIGS_URL).status_code, 403)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ExecutorListApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pw")
        cls.bob = User.objects.create_user(username="bob", password="pw")

    def _rows(self, *, reachable=True):
        self.client.force_login(self.alice)
        with patch(
            "llm_connector.views.hirschai_reachable", return_value=reachable
        ):
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
        row = LLMConfig(user=self.alice, provider="anthropic", default=True)
        row.api_key = "sk-a"
        row.save()
        rows = self._rows()
        self.assertTrue(rows["anthropic"]["configured"])
        self.assertTrue(rows["anthropic"]["default"])
        self.assertFalse(rows[HIRSCHAI_PROVIDER]["default"])

    def test_hirschai_is_default_without_a_commercial_default(self):
        self.assertTrue(self._rows()[HIRSCHAI_PROVIDER]["default"])

    def test_configs_do_not_leak_across_users(self):
        row = LLMConfig(user=self.bob, provider="openai", default=True)
        row.api_key = "sk-b"
        row.save()
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
        self.assertEqual([row["id"] for row in r.data], [self.mine.pk])
        self.assertEqual(r.data[0]["provider"], "fake")

    def test_retrieve_other_users_log_is_404(self):
        self.client.force_login(self.alice)
        other = LLMRequestLog.objects.get(user=self.bob)
        r = self.client.get(f"/api/llm/request-logs/{other.pk}/")
        self.assertEqual(r.status_code, 404)


@unittest.skip("[fullstack]-model-knobs — unskip when starting that guide")
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
        with patch("llm_connector.views.hirschai_reachable", return_value=True):
            rows = {r["provider"]: r for r in self.client.get("/api/llm/executors/").data}
        self.assertEqual(rows["ollama"]["knobs"], {})
        self.assertIn("effort", rows["anthropic"]["knobs"])
        self.assertIn("choices", rows["anthropic"]["knobs"]["effort"])
        self.assertIn("temperature", rows["openai"]["knobs"])
