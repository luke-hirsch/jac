"""Viewset user-scoping — LLMConfig + LLMRequestLog endpoints."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from llm_connector.models import LLMConfig, LLMRequestLog


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


class LLMConfigSSRFValidationTests(APITestCase):
    """`[backend]-ssrf-signup-gate`: the API refuses to store a custom/ollama config whose url
    resolves to an internal address. Red until LLMConfigSerializer.validate calls the validator."""

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
