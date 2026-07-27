"""LLMClient mechanics on the provider/model axis: construction + resolution,
per-call model override, transport retries, request logging, public helpers.
Adapter wire formats live in test_adapters; resolution rules in test_config.

Target API = `[backend]-executor-connector`.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from llm_connector import complete
from llm_connector.base import LLMAdapter, LLMTransportError
from llm_connector.client import (
    RETRY_DELAY_S,
    LLMClient,
    _normalise_messages,
    retry_reporter,
)
from llm_connector.conf import HIRSCHAI_PROVIDER, ExecutorError
from llm_connector.models import LLMRequestLog
from llm_connector.registry import get_adapter_class, register

from ._helpers import TEST_HIRSCHAI, FakeAdapter, fake_row


def client_for(config: dict, *, user=None, model=None) -> LLMClient:
    """Build a client over an in-memory config dict — for configs that can't live
    in a DB row (exception objects for the retry tests; extra is a JSONField)."""
    with patch("llm_connector.client.resolve_config", return_value=dict(config)):
        return LLMClient("fake", user=user, model=model)


class NormaliseMessagesTests(TestCase):
    def test_prompt_wraps_in_user_message(self):
        self.assertEqual(
            _normalise_messages("hi", None), [{"role": "user", "content": "hi"}]
        )

    def test_messages_pass_through_unchanged(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        self.assertEqual(_normalise_messages(None, msgs), msgs)

    def test_messages_take_precedence_over_prompt(self):
        msgs = [{"role": "user", "content": "m"}]
        self.assertEqual(_normalise_messages("p", msgs), msgs)

    def test_neither_raises(self):
        with self.assertRaises(ValueError):
            _normalise_messages(None, None)


class RegistryTests(TestCase):
    def test_fake_provider_is_registered(self):
        self.assertIs(get_adapter_class("fake"), FakeAdapter)

    def test_unknown_provider_raises(self):
        with self.assertRaises(Exception):
            get_adapter_class("definitely-not-a-provider")

    def test_register_decorator_adds_to_registry(self):
        @register("temp-provider")
        class Temp(LLMAdapter):
            def complete(self, messages, **kwargs):
                return ""

            def stream(self, messages, **kwargs):
                yield ""

        self.assertIs(get_adapter_class("temp-provider"), Temp)


@override_settings(HIRSCHAI=TEST_HIRSCHAI, LLM_LOGGING=False)
class LLMClientTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")

    def setUp(self):
        FakeAdapter.instances.clear()

    def test_complete_with_prompt_calls_adapter(self):
        fake_row(self.alice)
        out = LLMClient("fake", user=self.alice).complete("hi")
        self.assertEqual(out, "pong")
        adapter = FakeAdapter.instances[-1]
        self.assertEqual(
            adapter.complete_calls[0][0], [{"role": "user", "content": "hi"}]
        )

    def test_per_call_model_override_reaches_the_adapter(self):
        fake_row(self.alice, model="fake-1")
        client = LLMClient("fake", user=self.alice, model="fake-9")
        self.assertEqual(client.model, "fake-9")
        self.assertEqual(FakeAdapter.instances[-1].config["model"], "fake-9")

    def test_no_provider_resolves_the_default_row(self):
        fake_row(self.alice, default=True, model="fake-d")
        client = LLMClient(user=self.alice)
        self.assertEqual(client.provider, "fake")
        self.assertEqual(client.model, "fake-d")

    def test_no_provider_without_rows_resolves_hirschai(self):
        # Construction only — the ollama adapter doesn't touch the network in
        # __init__, so this asserts resolution without needing a tower.
        client = LLMClient(user=self.alice)
        self.assertEqual(client.provider, HIRSCHAI_PROVIDER)
        self.assertEqual(client.model, TEST_HIRSCHAI["model"])

    def test_missing_commercial_row_raises_at_construction(self):
        with self.assertRaises(ExecutorError):
            LLMClient("anthropic", user=self.alice)

    def test_complete_forwards_kwargs(self):
        fake_row(self.alice)
        LLMClient("fake", user=self.alice).complete("hi", options={"temperature": 0})
        _, kwargs = FakeAdapter.instances[-1].complete_calls[0]
        self.assertEqual(kwargs, {"options": {"temperature": 0}})

    def test_stream_yields_chunks(self):
        fake_row(self.alice, _chunks=["a", "b"])
        chunks = list(LLMClient("fake", user=self.alice).stream("hi"))
        self.assertEqual(chunks, ["a", "b"])

    def test_complete_propagates_adapter_errors(self):
        client = client_for(
            {"provider": "fake", "model": "m", "_raise": RuntimeError("boom")}
        )
        with self.assertRaisesRegex(RuntimeError, "boom"):
            client.complete("hi")

    def test_complete_helper_threads_provider_and_user(self):
        fake_row(self.alice, _response="personal")
        self.assertEqual(complete("hi", provider="fake", user=self.alice), "personal")


@override_settings(LLM_LOGGING=False)
class LLMClientRetryTests(TestCase):
    """One retry on transport-level failures, with the reporter hook. Uses
    client_for — exception objects can't live in a JSON extra."""

    def setUp(self):
        FakeAdapter.instances.clear()

    def _client(self, **cfg):
        return client_for({"provider": "fake", "model": "m", **cfg})

    @patch("llm_connector.client.time.sleep")
    def test_transport_error_is_retried_once(self, mock_sleep):
        client = self._client(_raise=LLMTransportError("net down"), _raise_times=1)
        self.assertEqual(client.complete("hi"), "pong")
        mock_sleep.assert_called_once_with(RETRY_DELAY_S)

    @patch("llm_connector.client.time.sleep")
    def test_second_transport_failure_propagates(self, _mock_sleep):
        client = self._client(_raise=LLMTransportError("still down"))
        with self.assertRaises(LLMTransportError):
            client.complete("hi")

    @patch("llm_connector.client.time.sleep")
    def test_plain_runtime_error_is_not_retried(self, mock_sleep):
        client = self._client(_raise=RuntimeError("http 500"), _raise_times=1)
        with self.assertRaises(RuntimeError):
            client.complete("hi")
        mock_sleep.assert_not_called()

    @patch("llm_connector.client.time.sleep")
    def test_retry_reporter_is_notified(self, _mock_sleep):
        client = self._client(_raise=LLMTransportError("net down"), _raise_times=1)
        seen = []
        with retry_reporter(lambda op, delay, err: seen.append((op, delay, err))):
            client.complete("hi")
        self.assertEqual(seen, [("completion", RETRY_DELAY_S, "net down")])

    @patch("llm_connector.client.time.sleep")
    def test_broken_reporter_does_not_kill_the_call(self, _mock_sleep):
        client = self._client(_raise=LLMTransportError("net down"), _raise_times=1)

        def broken(*_args):
            raise RuntimeError("reporter bug")

        with retry_reporter(broken):
            self.assertEqual(client.complete("hi"), "pong")

    @patch("llm_connector.client.time.sleep")
    def test_reporter_scope_ends_with_the_context(self, _mock_sleep):
        seen = []
        with retry_reporter(lambda *a: seen.append(a)):
            pass
        client = self._client(_raise=LLMTransportError("net down"), _raise_times=1)
        client.complete("hi")  # outside the context — reporter must not fire
        self.assertEqual(seen, [])


@override_settings(LLM_LOGGING=True)
class LLMClientLoggingTests(TestCase):
    def setUp(self):
        FakeAdapter.instances.clear()

    def test_complete_writes_a_provider_model_log_row(self):
        client_for({"provider": "fake", "model": "fake-m"}).complete("hi")
        log = LLMRequestLog.objects.get()
        self.assertEqual(log.provider, "fake")
        self.assertEqual(log.model, "fake-m")
        self.assertEqual(log.response_text, "pong")
        self.assertIsNone(log.user)

    def test_log_attributes_the_user(self):
        alice = User.objects.create(username="alice")
        fake_row(alice)
        LLMClient("fake", user=alice).complete("hi")
        self.assertEqual(LLMRequestLog.objects.get().user, alice)

    def test_stream_writes_log_with_joined_chunks(self):
        list(client_for({"provider": "fake", "model": "m"}).stream("hi"))
        self.assertEqual(LLMRequestLog.objects.get().response_text, "ping")

    def test_complete_logs_error_and_reraises(self):
        client = client_for(
            {"provider": "fake", "model": "m", "_raise": RuntimeError("boom")}
        )
        with self.assertRaises(RuntimeError):
            client.complete("hi")
        self.assertIn("boom", LLMRequestLog.objects.get().error)

    @override_settings(LLM_LOGGING=False)
    def test_logging_disabled_writes_nothing(self):
        client_for({"provider": "fake", "model": "m"}).complete("hi")
        self.assertEqual(LLMRequestLog.objects.count(), 0)


@override_settings(HIRSCHAI=TEST_HIRSCHAI, LLM_LOGGING=False)
class LLMCheckCommandTests(TestCase):
    def test_offline_tower_is_reported_without_a_pong(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with patch(
            "llm_connector.management.commands.llm_check.hirschai_reachable",
            return_value=False,
        ):
            call_command("llm_check", stdout=out)
        self.assertIn("OFFLINE", out.getvalue())

    def test_users_providers_are_ponged(self):
        from io import StringIO

        from django.core.management import call_command

        alice = User.objects.create(username="alice")
        fake_row(alice, model="fake-1")
        out = StringIO()
        with patch(
            "llm_connector.management.commands.llm_check.hirschai_reachable",
            return_value=False,
        ):
            call_command("llm_check", user=alice.pk, stdout=out)
        self.assertIn("fake", out.getvalue())
        self.assertNotIn("FAILED", out.getvalue())


class KnobbyFake(FakeAdapter):
    """[fullstack]-model-knobs: a fake that understands one knob — proves the
    client maps params through `map_params` instead of forwarding them raw."""

    def map_params(self, params: dict) -> dict:
        return {"temperature": params.get("temperature", 0.7)}


register("fakeknobs")(KnobbyFake)


@override_settings(HIRSCHAI=TEST_HIRSCHAI, LLM_LOGGING=False)
class ClientParamsSeamTests(TestCase):
    """Per-run knobs travel as one `params` kwarg to the client, which pops it and
    hands the ADAPTER only what its `map_params` returns — a raw `params` blob must
    never reach an adapter (ollama would put it on the wire)."""

    def test_params_are_popped_and_mapped(self):
        client = client_for({"provider": "fakeknobs", "model": "m"})
        client.complete("hi", params={"temperature": 0.2})
        _, kwargs = KnobbyFake.instances[-1].complete_calls[-1]
        self.assertNotIn("params", kwargs)
        self.assertEqual(kwargs["temperature"], 0.2)

    def test_no_params_means_no_extra_kwargs(self):
        client = client_for({"provider": "fake", "model": "m"})
        client.complete("hi")
        _, kwargs = FakeAdapter.instances[-1].complete_calls[-1]
        self.assertEqual(kwargs, {})

    def test_executor_carries_its_params_into_every_call(self):
        from llm_connector.executor import Executor

        user = User.objects.create_user("knobs", password="pw")
        fake_row(user, provider="fakeknobs", model="m")
        FakeAdapter.instances.clear()
        Executor("fakeknobs", "m", user, {"temperature": 0.1}).complete("hi")
        _, kwargs = KnobbyFake.instances[-1].complete_calls[-1]
        self.assertEqual(kwargs["temperature"], 0.1)
