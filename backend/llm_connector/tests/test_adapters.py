"""Provider adapters — Ollama behaviour + web-search capability (base flag, Anthropic, OpenAI)."""

import io
import json
from unittest.mock import MagicMock, patch
from urllib import error

import anthropic
import httpx
import openai
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from llm_connector.base import (
    LLMAdapter,
    LLMTransportError,
    LLMUnavailable,
    LLMWarmingUp,
)
from llm_connector.providers.anthropic import AnthropicAdapter
from llm_connector.providers.ollama import OllamaAdapter
from llm_connector.providers.openai import OpenAIAdapter
from llm_connector.registry import get_adapter_class


class OllamaAdapterTests(TestCase):
    """Native /api/chat adapter — the key win over /v1 is honouring `think`."""

    def _adapter(self, **over):
        from llm_connector.providers.ollama import OllamaAdapter

        cfg = {"url": "http://localhost:11434/v1", "model": "qwen3.5:0.8b", **over}
        return OllamaAdapter(cfg)

    @staticmethod
    def _fake_resp(body):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps(body).encode("utf-8")
        return resp

    def _capture_request(self, adapter, body, method="complete", **call_kwargs):
        import llm_connector.providers.ollama as mod

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return self._fake_resp(body)

        with patch.object(mod.request, "urlopen", fake_urlopen):
            out = getattr(adapter, method)(
                [{"role": "user", "content": "hi"}]
                if method == "complete"
                else ["a", "b"],
                **call_kwargs,
            )
        return out, captured

    def test_endpoint_strips_v1_and_targets_api_chat(self):
        adapter = self._adapter()
        out, cap = self._capture_request(adapter, {"message": {"content": "hello"}})
        self.assertTrue(cap["url"].endswith("/api/chat"))
        self.assertNotIn("/v1", cap["url"])
        self.assertEqual(out, "hello")

    def test_think_false_sent_top_level(self):
        adapter = self._adapter(think=False)
        _, cap = self._capture_request(adapter, {"message": {"content": "x"}})
        self.assertIs(cap["payload"]["think"], False)

    def test_think_omitted_when_unset(self):
        adapter = self._adapter()
        _, cap = self._capture_request(adapter, {"message": {"content": "x"}})
        self.assertNotIn("think", cap["payload"])

    def test_max_tokens_maps_to_options_num_predict(self):
        adapter = self._adapter(max_tokens=256)
        _, cap = self._capture_request(adapter, {"message": {"content": "x"}})
        self.assertEqual(cap["payload"]["options"]["num_predict"], 256)

    def test_missing_url_raises(self):
        from llm_connector.providers.ollama import OllamaAdapter

        with self.assertRaises(ImproperlyConfigured):
            OllamaAdapter({"model": "x"})

    def test_embed_uses_embed_model_and_endpoint(self):
        adapter = self._adapter(embed_model="qwen3-embedding:0.6b")
        out, cap = self._capture_request(
            adapter, {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}, method="embed"
        )
        self.assertTrue(cap["url"].endswith("/api/embed"))
        self.assertEqual(cap["payload"]["model"], "qwen3-embedding:0.6b")
        self.assertEqual(cap["payload"]["input"], ["a", "b"])
        self.assertEqual(out, [[0.1, 0.2], [0.3, 0.4]])

    def test_embed_empty_inputs_skips_request(self):
        adapter = self._adapter(embed_model="qwen3-embedding:0.6b")
        self.assertEqual(adapter.embed([]), [])

    def test_registered_as_ollama_provider(self):
        from llm_connector.providers.ollama import OllamaAdapter

        self.assertIs(get_adapter_class("ollama"), OllamaAdapter)


# ---------------------------------------------------------------------------
# web_search capability — base flag + Anthropic native search
# ---------------------------------------------------------------------------


class _Block:
    """A bare attribute bag standing in for an Anthropic SDK response/content block."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class WebSearchCapabilityTests(TestCase):
    def test_base_flag_false_and_method_raises(self):
        class Dummy(LLMAdapter):
            def complete(self, messages, **kw):
                return ""

            def stream(self, messages, **kw):
                yield ""

        d = Dummy({})
        self.assertFalse(d.supports_web_search)
        with self.assertRaises(NotImplementedError):
            d.web_search([{"role": "user", "content": "x"}])

    def test_anthropic_flag_true(self):
        self.assertTrue(AnthropicAdapter.supports_web_search)

    def test_openai_flag_true(self):
        self.assertTrue(OpenAIAdapter.supports_web_search)

    def test_ollama_flag_false(self):
        # HirschAI never web-searches — the personal paragraph's capability gate
        # (Executor.supports_web_search, tested in test_config) rests on this.
        from llm_connector.providers.ollama import OllamaAdapter

        self.assertFalse(getattr(OllamaAdapter, "supports_web_search", False))


class AnthropicWebSearchTests(TestCase):
    def _adapter(self):
        return AnthropicAdapter({"api_key": "test", "model": "claude-sonnet-4-6"})

    def test_concats_text_and_collects_sources(self):
        adapter = self._adapter()
        text_block = _Block(
            type="text",
            text="Acme builds rockets.",
            citations=[_Block(url="https://acme.com/about")],
        )
        ws_block = _Block(
            type="web_search_tool_result",
            content=[_Block(url="https://news.example/acme")],
        )
        adapter._client = MagicMock()
        adapter._client.messages.create.return_value = _Block(
            content=[text_block, ws_block]
        )

        out = adapter.web_search([{"role": "user", "content": "research Acme"}])

        self.assertEqual(out["text"], "Acme builds rockets.")
        self.assertIn("https://acme.com/about", out["sources"])
        self.assertIn("https://news.example/acme", out["sources"])

    def test_requests_the_web_search_tool(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        adapter._client.messages.create.return_value = _Block(
            content=[_Block(type="text", text="x", citations=[])]
        )
        adapter.web_search([{"role": "user", "content": "q"}])
        kwargs = adapter._client.messages.create.call_args.kwargs
        self.assertTrue(any(t.get("name") == "web_search" for t in kwargs["tools"]))

    def test_dedupes_sources_preserving_order(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        block = _Block(
            type="text",
            text="t",
            citations=[_Block(url="https://a"), _Block(url="https://a")],
        )
        adapter._client.messages.create.return_value = _Block(content=[block])
        out = adapter.web_search([{"role": "user", "content": "q"}])
        self.assertEqual(out["sources"], ["https://a"])


class OpenAIWebSearchTests(TestCase):
    """OpenAI web search rides the Responses API (responses.create), not chat.completions:
    output_text for the prose, url_citation annotations on the message blocks for sources."""

    def _adapter(self, **cfg):
        return OpenAIAdapter({"api_key": "test", "model": "gpt-5.4", **cfg})

    @staticmethod
    def _response(text, urls):
        msg = _Block(
            type="message",
            content=[
                _Block(
                    type="output_text",
                    text=text,
                    annotations=[_Block(type="url_citation", url=u) for u in urls],
                )
            ],
        )
        return _Block(output_text=text, output=[msg])

    def test_collects_text_and_citation_sources(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        adapter._client.responses.create.return_value = self._response(
            "Acme builds rockets.", ["https://acme.com/about", "https://news.example/acme"]
        )

        out = adapter.web_search([{"role": "user", "content": "research Acme"}])

        self.assertEqual(out["text"], "Acme builds rockets.")
        self.assertIn("https://acme.com/about", out["sources"])
        self.assertIn("https://news.example/acme", out["sources"])

    def test_requests_web_search_tool_via_responses_api(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        adapter._client.responses.create.return_value = self._response("x", [])
        adapter.web_search([{"role": "user", "content": "q"}])
        self.assertTrue(adapter._client.responses.create.called)
        kwargs = adapter._client.responses.create.call_args.kwargs
        self.assertTrue(any(t.get("type") == "web_search" for t in kwargs["tools"]))

    def test_forwards_reasoning_effort_when_configured(self):
        adapter = self._adapter(reasoning_effort="high")
        adapter._client = MagicMock()
        adapter._client.responses.create.return_value = self._response("x", [])
        adapter.web_search([{"role": "user", "content": "q"}])
        kwargs = adapter._client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["reasoning"], {"effort": "high"})

    def test_omits_reasoning_when_unset(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        adapter._client.responses.create.return_value = self._response("x", [])
        adapter.web_search([{"role": "user", "content": "q"}])
        self.assertNotIn("reasoning", adapter._client.responses.create.call_args.kwargs)

    def test_dedupes_sources_preserving_order(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        adapter._client.responses.create.return_value = self._response(
            "t", ["https://a", "https://a"]
        )
        out = adapter.web_search([{"role": "user", "content": "q"}])
        self.assertEqual(out["sources"], ["https://a"])


class AnthropicCompleteTests(TestCase):
    """`[backend]-correctness-bugs`: complete() must skip a leading non-text block instead of
    indexing content[0].text (which raises with tools/thinking enabled)."""

    def _adapter(self):
        return AnthropicAdapter({"api_key": "test", "model": "claude-sonnet-4-6"})

    def test_complete_returns_text_past_a_tool_block(self):
        adapter = self._adapter()
        adapter._client = MagicMock()
        adapter._client.messages.create.return_value = _Block(
            content=[
                _Block(type="tool_use", name="calc", input={}),
                _Block(type="text", text="Final answer."),
            ]
        )
        self.assertEqual(
            adapter.complete([{"role": "user", "content": "hi"}]), "Final answer."
        )


class CommercialAvailabilityTests(TestCase):
    """An API that can't be reached is the connector's transport error: the client's one retry
    fires, its "retrying" message shows, and a rung can't mistake it for a bad reply. An API that
    answered "no" stays its own error. Both SDKs raise the same names."""

    MESSAGES = [{"role": "user", "content": "hi"}]
    REQUEST = httpx.Request("POST", "https://api.example.com/v1")

    def _down(self, sdk):
        return (
            sdk.APIConnectionError(request=self.REQUEST),
            sdk.APITimeoutError(request=self.REQUEST),
            sdk.InternalServerError(
                "overloaded", response=httpx.Response(529, request=self.REQUEST), body=None
            ),
        )

    def _refused(self, sdk):
        return sdk.BadRequestError(
            "no", response=httpx.Response(400, request=self.REQUEST), body=None
        )

    def _anthropic(self):
        adapter = AnthropicAdapter({"api_key": "test", "model": "m"})
        adapter._client = MagicMock()
        return adapter

    def _openai(self):
        adapter = OpenAIAdapter({"api_key": "test", "model": "m"})
        adapter._client = MagicMock()
        return adapter

    def test_anthropic_complete(self):
        for exc in self._down(anthropic):
            adapter = self._anthropic()
            adapter._client.messages.create.side_effect = exc
            with self.subTest(type(exc).__name__), self.assertRaises(LLMTransportError):
                adapter.complete(self.MESSAGES)

    def test_anthropic_stream(self):
        adapter = self._anthropic()
        adapter._client.messages.stream.side_effect = anthropic.APIConnectionError(
            request=self.REQUEST
        )
        with self.assertRaises(LLMTransportError):
            list(adapter.stream(self.MESSAGES))

    def test_anthropic_web_search(self):
        adapter = self._anthropic()
        adapter._client.messages.create.side_effect = anthropic.APIConnectionError(
            request=self.REQUEST
        )
        with self.assertRaises(LLMTransportError):
            adapter.web_search(self.MESSAGES)

    def test_openai_complete(self):
        for exc in self._down(openai):
            adapter = self._openai()
            adapter._client.chat.completions.create.side_effect = exc
            with self.subTest(type(exc).__name__), self.assertRaises(LLMTransportError):
                adapter.complete(self.MESSAGES)

    def test_openai_stream(self):
        adapter = self._openai()
        adapter._client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=self.REQUEST
        )
        with self.assertRaises(LLMTransportError):
            list(adapter.stream(self.MESSAGES))

    def test_openai_web_search(self):
        adapter = self._openai()
        adapter._client.responses.create.side_effect = openai.APIConnectionError(
            request=self.REQUEST
        )
        with self.assertRaises(LLMTransportError):
            adapter.web_search(self.MESSAGES)

    def test_a_refusal_stays_its_own_error(self):
        adapter = self._anthropic()
        adapter._client.messages.create.side_effect = self._refused(anthropic)
        with self.assertRaises(anthropic.BadRequestError):
            adapter.complete(self.MESSAGES)
        adapter = self._openai()
        adapter._client.chat.completions.create.side_effect = self._refused(openai)
        with self.assertRaises(openai.BadRequestError):
            adapter.complete(self.MESSAGES)

    def test_the_timeout_reaches_the_sdk(self):
        """The SDKs default to 10 minutes: a hung chat would outlive its one-stream-per-user lock."""
        for cls in (AnthropicAdapter, OpenAIAdapter):
            with self.subTest(cls.__name__):
                adapter = cls({"api_key": "test", "model": "m", "timeout": 7})
                self.assertEqual(adapter._client.timeout, 7)


# ---------------------------------------------------------------------------
# [fullstack]-model-knobs — per-run knob mapping (skip-marked until that guide)
# ---------------------------------------------------------------------------


class AdapterKnobMappingTests(TestCase):
    """Generic per-run knobs ({effort, temperature}) → provider-native kwargs via
    `map_params`. Base = {} so a provider without knobs (ollama — whose payload
    builder forwards EVERY kwarg onto the wire) can never leak them."""

    def test_base_adapter_ignores_params(self):
        class Dummy(LLMAdapter):
            def complete(self, messages, **kw):
                return ""

            def stream(self, messages, **kw):
                yield ""

        self.assertEqual(Dummy({}).map_params({"effort": "high"}), {})

    def test_ollama_has_no_mapping_override(self):
        from llm_connector.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter({"url": "http://localhost:11434", "model": "m"})
        self.assertEqual(adapter.map_params({"effort": "high"}), {})

    def test_anthropic_effort_becomes_a_thinking_block_below_max_tokens(self):
        adapter = AnthropicAdapter(
            {"api_key": "test", "model": "claude-sonnet-5", "max_tokens": 4096}
        )
        out = adapter.map_params({"effort": "high"})
        self.assertEqual(out["thinking"]["type"], "enabled")
        self.assertLess(out["thinking"]["budget_tokens"], out["max_tokens"])

    def test_anthropic_temperature_alone_passes_through(self):
        adapter = AnthropicAdapter({"api_key": "test", "model": "claude-sonnet-5"})
        self.assertEqual(adapter.map_params({"temperature": 0.3}), {"temperature": 0.3})

    def test_anthropic_mapped_kwargs_override_instead_of_colliding(self):
        # complete() must build params then update(kwargs) — dict(..., **kwargs)
        # raises a duplicate-keyword TypeError on max_tokens.
        adapter = AnthropicAdapter(
            {"api_key": "test", "model": "claude-sonnet-5", "max_tokens": 4096}
        )
        adapter._client = MagicMock()
        adapter._client.messages.create.return_value = _Block(
            content=[_Block(type="text", text="ok")]
        )
        adapter.complete(
            [{"role": "user", "content": "hi"}], **adapter.map_params({"effort": "low"})
        )
        kwargs = adapter._client.messages.create.call_args.kwargs
        self.assertIn("thinking", kwargs)
        self.assertGreater(kwargs["max_tokens"], 4096)

    def test_openai_assumes_reasoning_semantics(self):
        # The catalog is reasoning-only (gpt-5.6-*), so the adapter deletes the old
        # ^o\d gate and always uses max_completion_tokens (never max_tokens).
        adapter = OpenAIAdapter(
            {"api_key": "test", "model": "gpt-5.6-terra", "max_tokens": 4096}
        )
        params: dict = {}
        adapter._apply_model_params(params)
        self.assertEqual(params.get("max_completion_tokens"), 4096)
        self.assertNotIn("max_tokens", params)

    def test_openai_effort_maps_to_reasoning_effort(self):
        adapter = OpenAIAdapter({"api_key": "test", "model": "gpt-5.6-terra"})
        self.assertEqual(
            adapter.map_params({"effort": "high"}), {"reasoning_effort": "high"}
        )

    def test_openai_ignores_temperature(self):
        # OpenAI is reasoning-only: temperature is not a knob, so even if one slipped
        # past validation it never reaches the wire (map_params drops it).
        adapter = OpenAIAdapter({"api_key": "test", "model": "gpt-5.6-terra"})
        self.assertEqual(adapter.map_params({"temperature": 0.7}), {})


class OllamaMultiTurnStreamTests(TestCase):
    """[fullstack]-chat-assistant-rework leans on multi-turn `stream(messages=…)` —
    pinned here (live, expected green) BEFORE that guide starts, so a regression
    surfaces now and not mid-rework."""

    def _adapter(self):
        from llm_connector.providers.ollama import OllamaAdapter

        return OllamaAdapter({"url": "http://localhost:11434", "model": "m"})

    def test_stream_passes_multi_turn_messages_and_yields_chunks(self):
        import llm_connector.providers.ollama as mod

        msgs = [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "tighten my letter"},
        ]
        lines = [
            json.dumps({"message": {"content": "Su"}}),
            json.dumps({"message": {"content": "re."}}),
            json.dumps({"done": True}),
        ]
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return iter(line.encode("utf-8") + b"\n" for line in lines)

        def fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResp()

        with patch.object(mod.request, "urlopen", fake_urlopen):
            out = list(self._adapter().stream(msgs))

        self.assertEqual(out, ["Su", "re."])
        self.assertEqual(captured["payload"]["messages"], msgs)  # roles intact
        self.assertIs(captured["payload"]["stream"], True)


class OllamaAvailabilityTests(TestCase):
    """What the model server said, in the connector's vocabulary. Plain HTTP semantics only: any
    503 + Retry-After means "on its way up", whoever sits in front of the server."""

    URLOPEN = "llm_connector.providers.ollama.request.urlopen"

    def _adapter(self):
        return OllamaAdapter({"url": "http://model:11434", "model": "m", "embed_model": "e"})

    def _http_error(self, code, headers=None):
        return error.HTTPError(
            "http://model:11434/api/chat", code, "nope", headers or {}, io.BytesIO(b"{}")
        )

    def test_503_with_retry_after_is_warming_up(self):
        with (
            patch(self.URLOPEN, side_effect=self._http_error(503, {"Retry-After": "30"})),
            self.assertRaises(LLMWarmingUp) as caught,
        ):
            self._adapter().complete([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.retry_after, 30.0)

    def test_an_unreadable_retry_after_still_means_soon(self):
        with (
            patch(
                self.URLOPEN,
                side_effect=self._http_error(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            ),
            self.assertRaises(LLMWarmingUp) as caught,
        ):
            self._adapter().embed(["hi"])
        self.assertGreater(caught.exception.retry_after, 0)

    def test_503_without_retry_after_is_unavailable_and_not_a_transport_error(self):
        with (
            patch(self.URLOPEN, side_effect=self._http_error(503)),
            self.assertRaises(LLMUnavailable),
        ):
            self._adapter().complete([{"role": "user", "content": "hi"}])
        self.assertFalse(issubclass(LLMUnavailable, LLMTransportError))

    def test_a_proxy_with_no_server_behind_it_is_a_transport_error(self):
        """Any TLS proxy in front of a model server that's off answers 502 or 504. That's the same
        "not there yet" as a refused connection, so a run waits instead of failing on the spot."""
        for code in (502, 504):
            with (
                self.subTest(code),
                patch(self.URLOPEN, side_effect=self._http_error(code)),
                self.assertRaises(LLMTransportError),
            ):
                self._adapter().complete([{"role": "user", "content": "hi"}])

    def test_other_http_errors_stay_runtime_errors(self):
        with (
            patch(self.URLOPEN, side_effect=self._http_error(500)),
            self.assertRaises(RuntimeError) as caught,
        ):
            self._adapter().complete([{"role": "user", "content": "hi"}])
        self.assertNotIsInstance(caught.exception, LLMTransportError)

    def test_a_slow_first_byte_is_a_transport_error(self):
        # urllib raises a bare TimeoutError here, outside any URLError.
        with (
            patch(self.URLOPEN, side_effect=TimeoutError("timed out")),
            self.assertRaises(LLMTransportError),
        ):
            self._adapter().complete([{"role": "user", "content": "hi"}])

    def test_a_timeout_while_reading_the_body_is_a_transport_error(self):
        with patch(self.URLOPEN) as urlopen:
            urlopen.return_value.__enter__.return_value.read.side_effect = TimeoutError
            with self.assertRaises(LLMTransportError):
                self._adapter().complete([{"role": "user", "content": "hi"}])

    def test_the_stream_reports_the_same_way(self):
        with (
            patch(self.URLOPEN, side_effect=self._http_error(503, {"Retry-After": "5"})),
            self.assertRaises(LLMWarmingUp),
        ):
            list(self._adapter().stream([{"role": "user", "content": "hi"}]))
