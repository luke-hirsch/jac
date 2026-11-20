"""Provider adapters — Ollama behaviour, web-search capability (base flag, Anthropic, OpenAI),
the live model lists and how each model's knobs reach the wire."""

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

    def test_listing_models(self):
        for adapter, sdk in ((self._anthropic(), anthropic), (self._openai(), openai)):
            with self.subTest(sdk.__name__):
                adapter._client.models.list.side_effect = sdk.APIConnectionError(
                    request=self.REQUEST
                )
                with self.assertRaises(LLMTransportError):
                    adapter.list_models()

    def test_max_retries_reaches_the_sdk(self):
        # A listing on a request thread gets one try (catalog.refresh_models).
        for cls in (AnthropicAdapter, OpenAIAdapter):
            with self.subTest(cls.__name__):
                adapter = cls({"api_key": "test", "timeout": 10, "max_retries": 0})
                self.assertEqual(adapter._client.max_retries, 0)

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
    """Generic per-run knobs ({effort}) → provider-native kwargs via `map_params`. Base = {}
    so a provider without knobs (ollama — whose payload builder forwards EVERY kwarg onto
    the wire) can never leak them. Anthropic maps by the model's stored thinking mode."""

    def _anthropic(self, thinking, **cfg):
        return AnthropicAdapter(
            {"api_key": "test", "model": "m", "max_tokens": 4096, "thinking": thinking, **cfg}
        )

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

    def test_adaptive_effort_sends_adaptive_thinking_and_output_config(self):
        """The live bug: Opus 4.8 answered budget_tokens with a 400. Effort goes inside
        output_config, and thinking is asked for, since Opus 4.7/4.8 don't think unasked."""
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort):
                out = self._anthropic("adaptive").map_params({"effort": effort})
                self.assertEqual(out["thinking"], {"type": "adaptive"})
                self.assertEqual(out["output_config"], {"effort": effort})
                self.assertNotIn("effort", out)  # never top level
                self.assertGreater(out["max_tokens"], 4096)

    def test_budget_effort_keeps_budget_tokens_below_max_tokens(self):
        # Haiku 4.5: thinking "enabled" only, on a budget the API wants >= 1024 and < max_tokens.
        for effort in ("low", "medium", "high"):
            with self.subTest(effort):
                out = self._anthropic("budget").map_params({"effort": effort})
                self.assertEqual(out["thinking"]["type"], "enabled")
                self.assertGreaterEqual(out["thinking"]["budget_tokens"], 1024)
                self.assertLess(out["thinking"]["budget_tokens"], out["max_tokens"])
                self.assertNotIn("output_config", out)

    def test_no_effort_sends_neither(self):
        # Today's plain call, which works on every model.
        self.assertEqual(self._anthropic("adaptive").map_params({}), {})
        self.assertEqual(self._anthropic("budget").map_params({"effort": ""}), {})

    def test_a_model_without_thinking_drops_effort(self):
        self.assertEqual(self._anthropic(None).map_params({"effort": "high"}), {})

    def test_temperature_never_reaches_the_wire(self):
        # A 400 on every current model, and on all of them from API version 2026-10-21.
        for thinking in ("adaptive", "budget", None):
            with self.subTest(thinking):
                out = self._anthropic(thinking).map_params({"temperature": 0.3})
                self.assertNotIn("temperature", out)

    def test_max_tokens_stays_under_the_sdks_non_streaming_estimate(self):
        """The SDK refuses a call without streaming whose max_tokens it expects to take over
        ten minutes (3600 s per 128k tokens)."""
        for thinking, efforts in (
            ("adaptive", ("low", "medium", "high", "xhigh", "max")),
            ("budget", ("low", "medium", "high")),
        ):
            for effort in efforts:
                with self.subTest(thinking=thinking, effort=effort):
                    out = self._anthropic(thinking).map_params({"effort": effort})
                    self.assertLessEqual(out["max_tokens"] * 3600 / 128_000, 600)

    def test_anthropic_mapped_kwargs_override_instead_of_colliding(self):
        # complete() must build params then update(kwargs) — dict(..., **kwargs)
        # raises a duplicate-keyword TypeError on max_tokens.
        adapter = self._anthropic("adaptive")
        adapter._client = MagicMock()
        adapter._client.messages.create.return_value = _Block(
            content=[_Block(type="text", text="ok")]
        )
        adapter.complete(
            [{"role": "user", "content": "hi"}], **adapter.map_params({"effort": "low"})
        )
        kwargs = adapter._client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(kwargs["output_config"], {"effort": "low"})
        self.assertGreater(kwargs["max_tokens"], 4096)
        self.assertNotIn("temperature", kwargs)

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
        # Temperature is not a knob, so even if one slipped past validation it never
        # reaches the wire (map_params drops it).
        adapter = OpenAIAdapter({"api_key": "test", "model": "gpt-5.6-terra"})
        self.assertEqual(adapter.map_params({"temperature": 0.7}), {})


class AdapterModelRequiredTests(TestCase):
    """No fallback model id in code: a call without one is a config error, and nothing
    reaches the provider. Listing models needs no model."""

    MESSAGES = [{"role": "user", "content": "hi"}]

    def test_a_call_without_a_model_is_a_config_error(self):
        for cls, create in (
            (AnthropicAdapter, "messages.create"),
            (OpenAIAdapter, "chat.completions.create"),
        ):
            with self.subTest(cls.__name__):
                adapter = cls({"api_key": "test"})
                adapter._client = MagicMock()
                with self.assertRaises(ImproperlyConfigured):
                    adapter.complete(self.MESSAGES)
                with self.assertRaises(ImproperlyConfigured):
                    list(adapter.stream(self.MESSAGES))
                with self.assertRaises(ImproperlyConfigured):
                    adapter.web_search(self.MESSAGES)
                target = adapter._client
                for part in create.split("."):
                    target = getattr(target, part)
                target.assert_not_called()


def _model_info(model_id, capabilities, **over):
    """An Anthropic ModelInfo the way the SDK builds one from a response (typed, lenient)."""
    return anthropic.types.ModelInfo.construct(
        id=model_id,
        type="model",
        display_name=over.pop("display_name", model_id.upper()),
        created_at="2026-01-01T00:00:00Z",
        capabilities=capabilities,
        **over,
    )


def _caps(*, adaptive, enabled, effort=()):
    levels = {lvl: {"supported": lvl in effort} for lvl in ("low", "medium", "high", "max")}
    if "xhigh" in effort:
        levels["xhigh"] = {"supported": True}
    return {
        "effort": {"supported": bool(effort), **levels},
        "thinking": {
            "supported": adaptive or enabled,
            "types": {"adaptive": {"supported": adaptive}, "enabled": {"supported": enabled}},
        },
    }


class AnthropicListModelsTests(TestCase):
    """The key's live model list, with each model's knobs from the Models API's capability
    data: adaptive thinking + the effort levels it reports, else the old token budget."""

    ALL = ("low", "medium", "high", "xhigh", "max")

    def _rows(self, *infos):
        adapter = AnthropicAdapter({"api_key": "test"})  # no model: listing needs none
        adapter._client = MagicMock()
        adapter._client.models.list.return_value = iter(infos)
        return adapter.list_models(), adapter

    def test_an_adaptive_model_offers_the_levels_it_reports(self):
        rows, _ = self._rows(
            _model_info(
                "claude-opus-4-8",
                _caps(adaptive=True, enabled=False, effort=self.ALL),
                display_name="Claude Opus 4.8",
            )
        )
        self.assertEqual(
            rows,
            [
                {
                    "id": "claude-opus-4-8",
                    "label": "Claude Opus 4.8",
                    "knobs": {"effort": {"choices": list(self.ALL)}},
                    "thinking": "adaptive",
                }
            ],
        )

    def test_a_level_the_model_leaves_out_is_not_offered(self):
        # Sonnet 4.6 has no xhigh: the SDK leaves that field None.
        rows, _ = self._rows(
            _model_info(
                "claude-sonnet-4-6",
                _caps(adaptive=True, enabled=True, effort=("low", "medium", "high", "max")),
            )
        )
        self.assertEqual(rows[0]["thinking"], "adaptive")
        self.assertEqual(
            rows[0]["knobs"]["effort"]["choices"], ["low", "medium", "high", "max"]
        )

    def test_a_budget_model_gets_the_three_budgets(self):
        # Haiku 4.5: thinking "enabled" only, no effort support.
        rows, _ = self._rows(
            _model_info("claude-haiku-4-5-20251001", _caps(adaptive=False, enabled=True))
        )
        self.assertEqual(rows[0]["thinking"], "budget")
        self.assertEqual(rows[0]["knobs"], {"effort": {"choices": ["low", "medium", "high"]}})

    def test_no_capability_data_means_no_knobs(self):
        rows, _ = self._rows(
            _model_info("claude-old", None),
            _model_info("claude-plain", _caps(adaptive=False, enabled=False)),
        )
        for row in rows:
            with self.subTest(row["id"]):
                self.assertEqual(row["knobs"], {})
                self.assertIsNone(row["thinking"])

    def test_a_shape_the_sdk_could_not_type_still_reads(self):
        info = _model_info("claude-x", None)
        info.capabilities = _caps(adaptive=True, enabled=False, effort=("low", "high"))
        rows, _ = self._rows(info)
        self.assertEqual(rows[0]["knobs"], {"effort": {"choices": ["low", "high"]}})

    def test_keeps_the_providers_order_and_pages_through(self):
        rows, adapter = self._rows(
            _model_info("claude-new", None), _model_info("claude-old", None)
        )
        self.assertEqual([r["id"] for r in rows], ["claude-new", "claude-old"])
        self.assertEqual(adapter._client.models.list.call_args.kwargs, {"limit": 100})

    def test_the_label_falls_back_to_the_id(self):
        rows, _ = self._rows(_model_info("claude-x", None, display_name=""))
        self.assertEqual(rows[0]["label"], "claude-x")


class OpenAIListModelsTests(TestCase):
    """The list names every model the key reaches; the adapter speaks Chat Completions, so
    only chat text models stay, newest first, with effort on the reasoning families only."""

    LISTED = {
        "gpt-5.6-terra": 50,
        "gpt-4o": 20,
        "o3": 40,
        "chatgpt-4o-latest": 30,
        "gpt-5-chat-latest": 45,
        "text-embedding-3-large": 60,
        "whisper-1": 61,
        "tts-1": 62,
        "gpt-4o-audio-preview": 63,
        "gpt-4o-realtime-preview": 64,
        "gpt-4o-mini-transcribe": 65,
        "gpt-image-1": 66,
        "dall-e-3": 67,
        "omni-moderation-latest": 68,
        "gpt-4o-search-preview": 69,
        "gpt-3.5-turbo-instruct": 70,
        "gpt-5-codex": 71,
        "o3-pro": 72,
        "o3-deep-research": 73,
        "computer-use-preview": 74,
        "babbage-002": 75,
    }

    def _rows(self):
        adapter = OpenAIAdapter({"api_key": "test"})  # no model: listing needs none
        adapter._client = MagicMock()
        adapter._client.models.list.return_value = iter(
            openai.types.Model.construct(id=i, created=c, object="model", owned_by="openai")
            for i, c in self.LISTED.items()
        )
        return adapter.list_models()

    def test_only_chat_text_models_stay_newest_first(self):
        self.assertEqual(
            [r["id"] for r in self._rows()],
            ["gpt-5.6-terra", "gpt-5-chat-latest", "o3", "chatgpt-4o-latest", "gpt-4o"],
        )

    def test_effort_only_where_the_model_reasons(self):
        knobs = {r["id"]: r["knobs"] for r in self._rows()}
        effort = {"effort": {"choices": ["low", "medium", "high"]}}
        self.assertEqual(knobs["gpt-5.6-terra"], effort)
        self.assertEqual(knobs["o3"], effort)
        for model_id in ("gpt-4o", "chatgpt-4o-latest", "gpt-5-chat-latest"):
            with self.subTest(model_id):
                self.assertEqual(knobs[model_id], {})

    def test_the_label_is_the_id_and_nothing_private_rides_along(self):
        for row in self._rows():
            with self.subTest(row["id"]):
                self.assertEqual(row["label"], row["id"])
                self.assertEqual(set(row), {"id", "label", "knobs"})


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
