"""Viewset user-scoping — LLMConfig + LLMRequestLog endpoints."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from llm_connector.models import LLMConfig, LLMRequestLog

from ._helpers import _muted


class LLMConfigViewSetScopingTests(APITestCase):
    """LLMConfigViewSet never leaks user A's configs to user B."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_llm", password="pass")
        cls.bob = User.objects.create_user(username="bob_llm", password="pass")
        cls.alice_config = LLMConfig.objects.create(
            user=cls.alice,
            alias="default",
            provider=LLMConfig.Provider.openai,
            model="gpt-4o",
        )

    def test_list_returns_only_own_configs(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/llm/configs/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.alice_config.pk, [row["id"] for row in r.data["results"]])

        self.client.force_login(self.bob)
        r = self.client.get("/api/llm/configs/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)

    def test_retrieve_other_users_config_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/llm/configs/{self.alice_config.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_patch_other_users_config_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.patch(
            f"/api/llm/configs/{self.alice_config.pk}/",
            {"model": "gpt-3.5"},
            format="json",
        )
        self.assertEqual(r.status_code, 404)

    def test_delete_other_users_config_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.delete(f"/api/llm/configs/{self.alice_config.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_unauthenticated_request_is_forbidden(self):
        r = self.client.get("/api/llm/configs/")
        self.assertIn(r.status_code, (401, 403))


class LLMRequestLogViewSetScopingTests(APITestCase):
    """LLMRequestLogViewSet (read-only) never leaks user A's logs to user B."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_log", password="pass")
        cls.bob = User.objects.create_user(username="bob_log", password="pass")
        cls.alice_log = LLMRequestLog.objects.create(
            user=cls.alice,
            alias="default",
            provider="openai",
            model="gpt-4o",
            request_messages=[],
        )

    def test_list_returns_only_own_logs(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/llm/request-logs/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.alice_log.pk, [row["id"] for row in r.data["results"]])

        self.client.force_login(self.bob)
        r = self.client.get("/api/llm/request-logs/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)

    def test_retrieve_other_users_log_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/llm/request-logs/{self.alice_log.pk}/")
        self.assertEqual(r.status_code, 404)


@override_settings(LLM_URL_ALLOWLIST=[], LLM_URL_ALLOW_PRIVATE=False)
class LLMConfigSSRFValidationTests(APITestCase):
    """`[backend]-ssrf-signup-gate`: the API refuses to store a custom/ollama config whose url
    resolves to an internal address. Pinned to the deny-by-default policy (no operator allowance)
    so it's independent of the DEBUG-seeded LLM_URL_ALLOWLIST. Red until LLMConfigSerializer.validate
    calls the validator."""

    def setUp(self):
        self.user = User.objects.create_user(username="ssrf_user", password="pass")
        self.client.force_login(self.user)

    def _post(self, url):
        return self.client.post(
            "/api/llm/configs/",
            json.dumps(
                {
                    "alias": "local",
                    "provider": "ollama",
                    "model": "llama3",
                    "url": url,
                }
            ),
            content_type="application/json",
        )

    def test_internal_url_is_rejected(self):
        r = self._post("http://127.0.0.1:11434/v1")
        self.assertEqual(r.status_code, 400)
        self.assertIn("url", r.data)

    def test_public_url_is_accepted(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            r = self._post("https://ollama.example.com/v1")
        self.assertEqual(r.status_code, 201, r.data)


class LLMConfigCheckActionTests(APITestCase):
    """`[fullstack]-llm-config-check`: POST /api/llm/configs/<pk>/check/ round-trips a
    pong completion through the row's alias — the API twin of the `llm_check` command.
    A failed probe is a result ({ok: false}), not an HTTP error. Red until the `check`
    action (and the module-level LLMClient import it needs) lands in views.py."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_check", password="pass")
        cls.bob = User.objects.create_user(username="bob_check", password="pass")
        cls.config = LLMConfig.objects.create(
            user=cls.alice,
            alias="writer",
            provider=LLMConfig.Provider.openai,
            model="gpt-4o",
        )

    def _check(self):
        return self.client.post(f"/api/llm/configs/{self.config.pk}/check/")

    def test_success_returns_ok_and_latency(self):
        self.client.force_login(self.alice)
        with patch("llm_connector.views.LLMClient") as client_cls:
            client_cls.return_value.complete.return_value = "pong"
            r = self._check()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["ok"])
        self.assertIsInstance(r.data["latency_ms"], int)
        # resolves by alias with the requesting user — what the pipeline will do
        client_cls.assert_called_once_with("writer", user=self.alice)

    def test_failure_is_a_result_not_an_http_error(self):
        self.client.force_login(self.alice)
        with patch("llm_connector.views.LLMClient") as client_cls:
            client_cls.return_value.complete.side_effect = RuntimeError(
                "connection refused"
            )
            r = self._check()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["ok"])
        self.assertIn("connection refused", r.data["error"])
        self.assertNotIn("latency_ms", r.data)

    def test_client_construction_failure_is_also_a_result(self):
        # unknown provider / missing optional SDK / decrypt failure — never a 500
        self.client.force_login(self.alice)
        with patch("llm_connector.views.LLMClient", side_effect=ImportError("no sdk")):
            r = self._check()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["ok"])
        self.assertIn("no sdk", r.data["error"])

    def test_other_users_config_is_404(self):
        self.client.force_login(self.bob)
        with patch("llm_connector.views.LLMClient") as client_cls:
            r = self._check()
        self.assertEqual(r.status_code, 404)
        client_cls.assert_not_called()

    def test_unauthenticated_request_is_forbidden(self):
        r = self._check()
        self.assertIn(r.status_code, (401, 403))


LLM_TEST_SETTINGS = {
    "default": {
        "provider": "ollama",
        "url": "http://localhost:11434",
        "model": "llama3.2:1b",
        "embed_model": "qwen3-embedding:0.6b",
        "strength": "light",
    }
}


@override_settings(LLM=LLM_TEST_SETTINGS)
class LLMAliasListViewTests(APITestCase):
    """/api/llm/aliases/ — resolved capabilities the generation UI pairs grades and
    models with. The 'default' fallback is always present; per-user rows carry their
    autodetected (or explicit) strength and adapter capabilities; nothing leaks
    across users."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_alias", password="pass")
        cls.bob = User.objects.create_user(username="bob_alias", password="pass")
        LLMConfig.objects.create(
            user=cls.alice,
            alias="opus",
            provider=LLMConfig.Provider.anthropic,
            model="claude-opus-4-8",
        )
        LLMConfig.objects.create(
            user=cls.alice,
            alias="local-embed",
            provider=LLMConfig.Provider.ollama,
            model="llama3.2:1b",
            url="http://localhost:11434",
            extra={"embed_model": "qwen3-embedding:0.6b", "strength": "light"},
        )

    def _rows(self, user):
        # Listing aliases resolves the always-present "default" fallback; for a
        # user without a personal LLMConfig that logs an expected "falling back
        # to settings" warning — mute it so the run stays a clean wall of dots.
        self.client.force_login(user)
        with _muted():
            r = self.client.get("/api/llm/aliases/")
        self.assertEqual(r.status_code, 200)
        return {row["alias"]: row for row in r.data}

    def test_default_fallback_is_always_present(self):
        rows = self._rows(self.bob)
        self.assertEqual(list(rows), ["default"])
        default = rows["default"]
        self.assertEqual(default["provider"], "ollama")
        self.assertEqual(default["strength"], "light")
        self.assertTrue(default["supports_embed"])
        self.assertFalse(default["supports_web_search"])

    def test_user_rows_carry_strength_and_capabilities(self):
        rows = self._rows(self.alice)
        self.assertEqual(set(rows), {"default", "opus", "local-embed"})
        opus = rows["opus"]
        self.assertEqual(opus["strength"], "strong")  # autodetected from the model id
        self.assertFalse(opus["supports_embed"])
        self.assertTrue(opus["supports_web_search"])
        local = rows["local-embed"]
        self.assertEqual(local["strength"], "light")  # explicit in extra
        self.assertTrue(local["supports_embed"])

    def test_no_cross_user_leak(self):
        rows = self._rows(self.bob)
        self.assertNotIn("opus", rows)

    def test_unauthenticated_request_is_forbidden(self):
        self.client.logout()
        r = self.client.get("/api/llm/aliases/")
        self.assertIn(r.status_code, (401, 403))


class LLMPinViewTests(APITestCase):
    """/api/llm/pins/ — the per-strength favourite-model pins. GET returns all three
    tiers (null when unset); PUT upserts one tier, a blank alias clears it; only the
    user's own aliases (plus "default") are pinnable; nothing leaks across users."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_pin", password="pass")
        cls.bob = User.objects.create_user(username="bob_pin", password="pass")
        LLMConfig.objects.create(
            user=cls.alice,
            alias="local",
            provider=LLMConfig.Provider.ollama,
            model="qwen3:8b",
            url="http://localhost:11434",
        )

    def _put(self, body):
        return self.client.put("/api/llm/pins/", body, format="json")

    def test_get_returns_all_tiers_with_nulls(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/llm/pins/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"light": None, "standard": None, "strong": None})

    def test_put_upserts_and_clears_a_tier(self):
        self.client.force_login(self.alice)
        r = self._put({"strength": "standard", "alias": "local"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["standard"], "local")

        r = self._put({"strength": "standard", "alias": "default"})  # overwrite
        self.assertEqual(r.data["standard"], "default")

        r = self._put({"strength": "standard", "alias": ""})  # clear
        self.assertEqual(r.data["standard"], None)

    def test_unknown_alias_is_rejected(self):
        self.client.force_login(self.alice)
        r = self._put({"strength": "strong", "alias": "not-mine"})
        self.assertEqual(r.status_code, 400)

    def test_another_users_alias_is_rejected(self):
        self.client.force_login(self.bob)
        r = self._put({"strength": "standard", "alias": "local"})  # alice's row
        self.assertEqual(r.status_code, 400)

    def test_bad_strength_is_rejected(self):
        self.client.force_login(self.alice)
        r = self._put({"strength": "mega", "alias": "local"})
        self.assertEqual(r.status_code, 400)

    def test_pins_do_not_leak_across_users(self):
        self.client.force_login(self.alice)
        self._put({"strength": "light", "alias": "local"})
        self.client.force_login(self.bob)
        r = self.client.get("/api/llm/pins/")
        self.assertEqual(r.data, {"light": None, "standard": None, "strong": None})

    def test_unauthenticated_request_is_forbidden(self):
        r = self.client.get("/api/llm/pins/")
        self.assertIn(r.status_code, (401, 403))
