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
