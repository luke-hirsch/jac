from collections.abc import Generator
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase, override_settings

import llm_connector
from llm_connector import complete, get_client, stream
from llm_connector.base import LLMAdapter
from llm_connector.client import LLMClient, _normalise_messages
from llm_connector.conf import (
    get_alias_config,
    get_alias_strength,
    get_llm_settings,
    logging_enabled,
)
from llm_connector.crypto import _fernet, decrypt, encrypt
from llm_connector.models import LLMConfig, LLMRequestLog
from llm_connector.registry import _registry, get_adapter_class, register


class FakeAdapter(LLMAdapter):
    """In-memory adapter used to exercise the client without hitting a real API."""

    instances: list["FakeAdapter"] = []

    def __init__(self, config: dict):
        super().__init__(config)
        self.complete_calls: list[tuple[list[dict], dict]] = []
        self.stream_calls: list[tuple[list[dict], dict]] = []
        self.response = config.get("_response", "pong")
        self.chunks = config.get("_chunks", ["pi", "ng"])
        self.raise_on_complete: Exception | None = config.get("_raise")
        FakeAdapter.instances.append(self)

    def complete(self, messages: list[dict], **kwargs) -> str:
        self.complete_calls.append((messages, kwargs))
        if self.raise_on_complete:
            raise self.raise_on_complete
        return self.response

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        self.stream_calls.append((messages, kwargs))
        for chunk in self.chunks:
            yield chunk


register("fake")(FakeAdapter)


FAKE_LLM = {
    "default": {"provider": "fake", "model": "fake-1"},
    "other": {"provider": "fake", "model": "fake-2", "_response": "hello"},
}


class NormaliseMessagesTests(TestCase):
    def test_prompt_wraps_in_user_message(self):
        self.assertEqual(
            _normalise_messages("hi", None),
            [{"role": "user", "content": "hi"}],
        )

    def test_messages_pass_through_unchanged(self):
        msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.assertIs(_normalise_messages(None, msgs), msgs)

    def test_messages_take_precedence_over_prompt(self):
        msgs = [{"role": "user", "content": "from messages"}]
        self.assertIs(_normalise_messages("from prompt", msgs), msgs)

    def test_neither_raises(self):
        with self.assertRaises(ValueError):
            _normalise_messages(None, None)


class ConfTests(TestCase):
    @override_settings()
    def test_missing_llm_setting_raises(self):
        from django.conf import settings as dj_settings

        del dj_settings.LLM
        with self.assertRaises(ImproperlyConfigured):
            get_llm_settings()

    @override_settings(LLM=FAKE_LLM)
    def test_get_alias_config_returns_alias(self):
        self.assertEqual(get_alias_config("default")["model"], "fake-1")
        self.assertEqual(get_alias_config("other")["model"], "fake-2")

    @override_settings(LLM=FAKE_LLM)
    def test_unknown_alias_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_alias_config("nope")

    @override_settings(LLM={"broken": {"model": "x"}})
    def test_alias_without_provider_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_alias_config("broken")

    @override_settings(
        LLM={"default": {"provider": "fake", "model": "fake-1", "strength": "light"}}
    )
    def test_get_alias_strength_reads_config(self):
        self.assertEqual(get_alias_strength("default"), "light")

    @override_settings(LLM=FAKE_LLM)
    def test_get_alias_strength_defaults_to_strong_when_unset(self):
        self.assertEqual(get_alias_strength("default"), "strong")

    @override_settings(
        LLM={"default": {"provider": "fake", "model": "fake-1", "strength": "bogus"}}
    )
    def test_get_alias_strength_rejects_unknown_value(self):
        self.assertEqual(get_alias_strength("default"), "strong")

    @override_settings(LLM=FAKE_LLM)
    def test_get_alias_strength_missing_alias_is_strong(self):
        # A broken/missing config must not crash the pipeline — default strong.
        self.assertEqual(get_alias_strength("nope"), "strong")

    @override_settings(LLM_LOGGING=True)
    def test_logging_enabled_true(self):
        self.assertTrue(logging_enabled())

    @override_settings(LLM_LOGGING=False)
    def test_logging_enabled_false(self):
        self.assertFalse(logging_enabled())


class RegistryTests(TestCase):
    def test_fake_provider_is_registered(self):
        self.assertIs(get_adapter_class("fake"), FakeAdapter)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_adapter_class("does-not-exist")

    def test_register_decorator_adds_to_registry(self):
        @register("temp-provider")
        class Temp(LLMAdapter):
            def complete(self, messages, **kwargs):
                return ""

            def stream(self, messages, **kwargs):
                yield ""

        try:
            self.assertIs(get_adapter_class("temp-provider"), Temp)
        finally:
            _registry.pop("temp-provider", None)


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=False)
class LLMClientTests(TestCase):
    def setUp(self):
        FakeAdapter.instances.clear()

    def test_complete_with_prompt_calls_adapter(self):
        client = LLMClient("default")
        result = client.complete("hello")
        self.assertEqual(result, "pong")
        adapter = FakeAdapter.instances[-1]
        self.assertEqual(
            adapter.complete_calls[0][0], [{"role": "user", "content": "hello"}]
        )

    def test_complete_with_messages_passes_through(self):
        msgs = [{"role": "user", "content": "x"}]
        client = LLMClient("default")
        client.complete(messages=msgs)
        adapter = FakeAdapter.instances[-1]
        self.assertEqual(adapter.complete_calls[0][0], msgs)

    def test_complete_forwards_kwargs(self):
        client = LLMClient("default")
        client.complete("hello", temperature=0.7)
        adapter = FakeAdapter.instances[-1]
        self.assertEqual(adapter.complete_calls[0][1], {"temperature": 0.7})

    def test_complete_returns_alias_specific_response(self):
        self.assertEqual(LLMClient("other").complete("hi"), "hello")

    def test_stream_yields_chunks(self):
        client = LLMClient("default")
        chunks = list(client.stream("hello"))
        self.assertEqual(chunks, ["pi", "ng"])

    def test_complete_propagates_adapter_errors(self):
        boom = RuntimeError("boom")
        with override_settings(LLM={"default": {"provider": "fake", "_raise": boom}}):
            client = LLMClient("default")
            with self.assertRaises(RuntimeError):
                client.complete("hi")

    def test_unknown_alias_raises_at_construction(self):
        with self.assertRaises(ImproperlyConfigured):
            LLMClient("missing")


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=True)
class LLMClientLoggingTests(TestCase):
    def test_complete_writes_log_row(self):
        LLMClient("default").complete("hello")
        log = LLMRequestLog.objects.get()
        self.assertEqual(log.alias, "default")
        self.assertEqual(log.provider, "fake")
        self.assertEqual(log.model, "fake-1")
        self.assertEqual(log.response_text, "pong")
        self.assertEqual(log.request_messages, [{"role": "user", "content": "hello"}])
        self.assertEqual(log.error, "")
        self.assertIsNotNone(log.latency_ms)

    def test_stream_writes_log_with_joined_chunks(self):
        list(LLMClient("default").stream("hello"))
        log = LLMRequestLog.objects.get()
        self.assertEqual(log.response_text, "ping")
        self.assertEqual(log.error, "")

    def test_complete_logs_error_and_reraises(self):
        with override_settings(
            LLM={"default": {"provider": "fake", "_raise": RuntimeError("nope")}}
        ):
            with self.assertRaises(RuntimeError):
                LLMClient("default").complete("hi")
        log = LLMRequestLog.objects.get()
        self.assertEqual(log.error, "nope")
        self.assertEqual(log.response_text, "")


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=False)
class ModuleLevelHelperTests(TestCase):
    def setUp(self):
        FakeAdapter.instances.clear()

    def test_get_client_returns_llmclient(self):
        client = get_client("default")
        self.assertIsInstance(client, LLMClient)
        self.assertEqual(client.alias, "default")

    def test_complete_helper(self):
        self.assertEqual(complete("hello"), "pong")

    def test_complete_helper_with_alias(self):
        self.assertEqual(complete("hello", alias="other"), "hello")

    def test_stream_helper(self):
        self.assertEqual(list(stream("hi")), ["pi", "ng"])

    def test_public_api_exports(self):
        self.assertTrue(callable(llm_connector.get_client))
        self.assertTrue(callable(llm_connector.complete))
        self.assertTrue(callable(llm_connector.stream))


class LLMRequestLogModelTests(TestCase):
    def test_str_includes_alias_and_provider(self):
        log = LLMRequestLog.objects.create(
            alias="default",
            provider="fake",
            model="fake-1",
            request_messages=[{"role": "user", "content": "hi"}],
        )
        self.assertIn("default", str(log))
        self.assertIn("fake", str(log))


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=False)
class LLMCheckCommandTests(TestCase):
    def test_reports_ok_for_working_alias(self):
        out = StringIO()
        call_command("llm_check", "default", stdout=out)
        output = out.getvalue()
        self.assertIn("default", output)
        self.assertIn("OK", output)
        self.assertIn("provider=fake", output)

    def test_reports_fail_when_adapter_raises(self):
        with override_settings(
            LLM={
                "default": {
                    "provider": "fake",
                    "_raise": RuntimeError("connection refused"),
                }
            }
        ):
            out = StringIO()
            call_command("llm_check", "default", stdout=out)
            output = out.getvalue()
            self.assertIn("FAIL", output)
            self.assertIn("connection refused", output)

    def test_reports_not_found_for_unknown_alias(self):
        out = StringIO()
        call_command("llm_check", "ghost", stdout=out)
        self.assertIn("not found", out.getvalue())

    def test_defaults_to_all_aliases(self):
        out = StringIO()
        call_command("llm_check", stdout=out)
        output = out.getvalue()
        self.assertIn("default", output)
        self.assertIn("other", output)

    def test_user_flag_checks_user_configs(self):
        user = User.objects.create(username="alice")
        LLMConfig.objects.create(
            user=user,
            alias="reasoning",
            provider="fake",
            model="user-model",
        )
        out = StringIO()
        call_command("llm_check", user=user.pk, stdout=out)
        output = out.getvalue()
        self.assertIn(f"user={user}", output)
        self.assertIn("reasoning", output)
        self.assertIn("user-model", output)
        # Global default is NOT checked when --user is given.
        self.assertNotIn("fake-1", output)

    def test_user_flag_reports_no_configs(self):
        user = User.objects.create(username="empty")
        out = StringIO()
        call_command("llm_check", "reasoning", user=user.pk, stdout=out)
        self.assertIn("not configured for this user", out.getvalue())

    def test_user_flag_unknown_pk_errors(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("llm_check", user=999999, stdout=StringIO())


class OpenAIAdapterParamTests(TestCase):
    """Unit tests for the OpenAI adapter param translation. Mocks the openai
    SDK so they run without the real package or network access."""

    def setUp(self):
        import sys

        self._original_openai = sys.modules.get("openai")
        sys.modules["openai"] = MagicMock()

    def tearDown(self):
        import sys

        if self._original_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = self._original_openai

    def _make_adapter(self, **config_overrides):
        from llm_connector.providers.openai import OpenAIAdapter

        config = {"api_key": "test-key", **config_overrides}
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            adapter = OpenAIAdapter(config)
        return adapter, mock_client

    @staticmethod
    def _fake_response(text="ok"):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = text
        return resp

    def _call_complete(self, adapter, client):
        client.chat.completions.create.return_value = self._fake_response()
        adapter.complete([{"role": "user", "content": "hi"}])
        return client.chat.completions.create.call_args.kwargs

    def test_non_reasoning_model_uses_max_tokens(self):
        adapter, client = self._make_adapter(model="gpt-4o-mini", max_tokens=2048)
        kwargs = self._call_complete(adapter, client)
        self.assertEqual(kwargs["max_tokens"], 2048)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertNotIn("reasoning_effort", kwargs)

    def test_o_series_translates_to_max_completion_tokens(self):
        adapter, client = self._make_adapter(
            model="o4-mini", max_tokens=8192, reasoning_effort="medium"
        )
        kwargs = self._call_complete(adapter, client)
        self.assertEqual(kwargs["max_completion_tokens"], 8192)
        self.assertEqual(kwargs["reasoning_effort"], "medium")
        self.assertNotIn("max_tokens", kwargs)

    def test_o_series_without_reasoning_effort_omits_param(self):
        adapter, client = self._make_adapter(model="o4-mini", max_tokens=4096)
        kwargs = self._call_complete(adapter, client)
        self.assertEqual(kwargs["max_completion_tokens"], 4096)
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertNotIn("max_tokens", kwargs)

    def test_reasoning_effort_ignored_for_non_reasoning_model(self):
        adapter, client = self._make_adapter(
            model="gpt-4o-mini", max_tokens=1024, reasoning_effort="high"
        )
        kwargs = self._call_complete(adapter, client)
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertEqual(kwargs["max_tokens"], 1024)


# ---------------------------------------------------------------------------
# Crypto + LLMConfig + per-user resolution
# ---------------------------------------------------------------------------


class CryptoTests(TestCase):
    def setUp(self):
        _fernet.cache_clear()

    def tearDown(self):
        _fernet.cache_clear()

    def test_roundtrip(self):
        self.assertEqual(decrypt(encrypt("sk-secret-123")), "sk-secret-123")

    def test_encrypt_empty_returns_empty(self):
        self.assertEqual(encrypt(""), "")

    def test_decrypt_empty_returns_empty(self):
        self.assertEqual(decrypt(""), "")

    def test_ciphertext_is_not_plaintext(self):
        ct = encrypt("plain")
        self.assertNotEqual(ct, "plain")
        self.assertNotIn("plain", ct)

    @override_settings(LLM_ENCRYPTION_KEY=None)
    def test_missing_key_raises(self):
        _fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            encrypt("anything")

    @override_settings(LLM_ENCRYPTION_KEY="not-a-valid-fernet-key")
    def test_invalid_key_raises(self):
        _fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            encrypt("anything")


class LLMConfigModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")

    def test_api_key_property_roundtrips_through_encryption(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )
        cfg.api_key = "sk-ant-abc"
        cfg.save()
        cfg.refresh_from_db()
        self.assertEqual(cfg.api_key, "sk-ant-abc")
        self.assertNotEqual(cfg.api_key_encrypted, "sk-ant-abc")
        self.assertTrue(cfg.has_api_key)

    def test_empty_api_key_clears_encrypted_value(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="x",
        )
        cfg.api_key = "secret"
        cfg.api_key = ""
        self.assertEqual(cfg.api_key_encrypted, "")
        self.assertFalse(cfg.has_api_key)

    def test_unique_per_user_and_alias(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="x",
        )
        with self.assertRaises(Exception):
            LLMConfig.objects.create(
                user=self.user,
                alias="default",
                provider="openai",
                model="y",
            )

    def test_to_config_dict_includes_api_key_and_extras(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="openai",
            model="o4-mini",
            max_tokens=8192,
            extra={"reasoning_effort": "high"},
        )
        cfg.api_key = "sk-foo"
        cfg.save()
        d = cfg.to_config_dict()
        self.assertEqual(d["provider"], "openai")
        self.assertEqual(d["model"], "o4-mini")
        self.assertEqual(d["max_tokens"], 8192)
        self.assertEqual(d["api_key"], "sk-foo")
        self.assertEqual(d["reasoning_effort"], "high")

    def test_to_config_dict_omits_empty_fields(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="ollama",
            provider="custom",
            model="qwen",
            url="http://localhost:11434/v1",
        )
        d = cfg.to_config_dict()
        self.assertEqual(set(d.keys()), {"provider", "model", "url"})


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=False)
class UserScopedResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")
        cls.other = User.objects.create(username="bob")

    def setUp(self):
        FakeAdapter.instances.clear()

    def test_user_with_config_uses_their_config(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="fake",
            model="user-model",
            extra={"_response": "user-specific"},
        )
        cfg.api_key = "sk-personal"
        cfg.save()
        client = LLMClient("reasoning", user=self.user)
        self.assertEqual(client._config["model"], "user-model")
        self.assertEqual(client._config["api_key"], "sk-personal")
        self.assertEqual(client.complete("hi"), "user-specific")

    def test_user_without_config_falls_back_to_default(self):
        client = LLMClient("reasoning", user=self.user)
        self.assertEqual(client._config["model"], "fake-1")
        self.assertEqual(client.complete("hi"), "pong")

    def test_user_default_alias_can_override_global_default(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="fake",
            model="alice-default",
            extra={"_response": "alice-says-hi"},
        )
        client = LLMClient("default", user=self.user)
        self.assertEqual(client._config["model"], "alice-default")
        self.assertEqual(client.complete("hi"), "alice-says-hi")

    def test_no_user_reads_settings_directly(self):
        client = LLMClient("default")
        self.assertEqual(client._config["model"], "fake-1")

    def test_one_users_config_does_not_leak_to_another(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="fake",
            model="alice-model",
        )
        client_bob = LLMClient("reasoning", user=self.other)
        self.assertEqual(client_bob._config["model"], "fake-1")

    def test_missing_default_alias_raises_on_fallback(self):
        with override_settings(LLM={"other": {"provider": "fake", "model": "x"}}):
            with self.assertRaises(ImproperlyConfigured):
                LLMClient("anything", user=self.user)

    def test_complete_helper_threads_user(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="fake",
            model="m",
            extra={"_response": "personal"},
        )
        self.assertEqual(complete("hi", alias="reasoning", user=self.user), "personal")


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=True)
class LLMRequestLogUserAttributionTests(TestCase):
    def test_log_attributes_user_when_provided(self):
        user = User.objects.create(username="alice")
        LLMConfig.objects.create(
            user=user,
            alias="reasoning",
            provider="fake",
            model="m",
        )
        LLMClient("reasoning", user=user).complete("hi")
        log = LLMRequestLog.objects.get()
        self.assertEqual(log.user, user)

    def test_log_user_is_null_when_no_user(self):
        LLMClient("default").complete("hi")
        log = LLMRequestLog.objects.get()
        self.assertIsNone(log.user)


class LLMConfigAdminFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")

    def _form_data(self, **overrides):
        data = {
            "user": str(self.user.pk),
            "alias": "default",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "url": "",
            "max_tokens": "",
            "extra": "{}",
            "api_key": "",
        }
        data.update(overrides)
        return data

    def test_new_instance_with_api_key_encrypts_on_save(self):
        from llm_connector.admin import LLMConfigAdminForm

        form = LLMConfigAdminForm(self._form_data(api_key="sk-new"))
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        self.assertEqual(instance.api_key, "sk-new")
        self.assertTrue(instance.has_api_key)

    def test_empty_api_key_on_edit_preserves_existing(self):
        from llm_connector.admin import LLMConfigAdminForm

        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )
        cfg.api_key = "sk-original"
        cfg.save()
        form = LLMConfigAdminForm(self._form_data(api_key=""), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.api_key, "sk-original")

    def test_new_api_key_on_edit_replaces_existing(self):
        from llm_connector.admin import LLMConfigAdminForm

        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="x",
        )
        cfg.api_key = "sk-old"
        cfg.save()
        form = LLMConfigAdminForm(self._form_data(api_key="sk-new"), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.api_key, "sk-new")


# ---------------------------------------------------------------------------
# Viewset user-scoping tests
# ---------------------------------------------------------------------------

from rest_framework.test import APITestCase  # noqa: E402


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
            f"/api/llm/configs/{self.alice_config.pk}/", {"model": "gpt-3.5"}, format="json"
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
