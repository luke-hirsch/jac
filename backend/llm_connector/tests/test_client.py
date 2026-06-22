"""LLM client core — message normalisation, conf/registry, client dispatch,
request logging, module-level helpers, the llm_check command."""

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
    get_embed_floors,
    get_llm_settings,
    logging_enabled,
)
from llm_connector.models import LLMConfig, LLMRequestLog
from llm_connector.registry import _registry, get_adapter_class, register

from ._helpers import FakeAdapter, FAKE_LLM


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
    @override_settings(LLM={"default": {"provider": "custom", "model": "qwen3.5:0.8b"}})
    def test_get_alias_strength_autodetects_small_local_as_light(self):
        self.assertEqual(get_alias_strength("default"), "light")

    @override_settings(LLM={"default": {"provider": "openai", "model": "gpt-4o-mini"}})
    def test_get_alias_strength_autodetects_mini_as_standard(self):
        self.assertEqual(get_alias_strength("default"), "standard")

    @override_settings(
        LLM={"default": {"provider": "anthropic", "model": "claude-opus-4-8"}}
    )
    def test_get_alias_strength_autodetects_large_as_strong(self):
        self.assertEqual(get_alias_strength("default"), "strong")

    @override_settings(
        LLM={
            "default": {
                "provider": "custom",
                "model": "qwen3.5:0.8b",
                "strength": "strong",
            }
        }
    )
    def test_get_alias_strength_explicit_overrides_autodetect(self):
        self.assertEqual(get_alias_strength("default"), "strong")

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

    @override_settings(
        LLM={"default": {"provider": "ollama", "model": "nomic-embed-text"}}
    )
    def test_get_alias_strength_autodetects_embedder_as_light(self):
        self.assertEqual(get_alias_strength("default"), "light")

    @override_settings(
        LLM={"default": {"provider": "ollama", "model": "e5-mistral-7b"}}
    )
    def test_get_alias_strength_embedder_light_despite_large_size(self):
        # The embedding-name hint wins over the 7b size token (would be 'standard').
        self.assertEqual(get_alias_strength("default"), "light")

    @override_settings(LLM={"default": {"provider": "ollama", "model": "bge-large"}})
    def test_get_alias_strength_embedder_light_without_size(self):
        self.assertEqual(get_alias_strength("default"), "light")

    @override_settings(LLM=FAKE_LLM)
    def test_get_embed_floors_empty_when_unset(self):
        self.assertEqual(get_embed_floors("default"), {})

    @override_settings(LLM=FAKE_LLM)
    def test_get_embed_floors_empty_on_missing_alias(self):
        # A broken/missing config must not crash the light path — empty dict.
        self.assertEqual(get_embed_floors("nope"), {})

    @override_settings(
        LLM={
            "default": {
                "provider": "fake",
                "model": "fake-1",
                "embed_floors": {"skill": 0.55, "job": 0.45},
            }
        }
    )
    def test_get_embed_floors_reads_config(self):
        self.assertEqual(get_embed_floors("default"), {"skill": 0.55, "job": 0.45})

    @override_settings(
        LLM={"default": {"provider": "fake", "model": "fake-1", "embed_floors": "nope"}}
    )
    def test_get_embed_floors_ignores_non_dict(self):
        self.assertEqual(get_embed_floors("default"), {})

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

    def test_reports_strength_for_working_alias(self):
        out = StringIO()
        call_command("llm_check", "default", stdout=out)
        # fake-1 has no size/name hint -> autodetects to the full ladder.
        self.assertIn("strength=strong", out.getvalue())

    def test_strength_respects_explicit_config(self):
        with override_settings(
            LLM={
                "default": {"provider": "fake", "model": "fake-1", "strength": "light"}
            }
        ):
            out = StringIO()
            call_command("llm_check", "default", stdout=out)
            self.assertIn("strength=light", out.getvalue())

    def test_strength_autodetects_small_model(self):
        with override_settings(
            LLM={"default": {"provider": "fake", "model": "llama3.2:1b"}}
        ):
            out = StringIO()
            call_command("llm_check", "default", stdout=out)
            self.assertIn("strength=light", out.getvalue())


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
