"""Provider adapters — Ollama behaviour + web-search capability (base flag, Anthropic, OpenAI)."""

import json
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from llm_connector.base import LLMAdapter
from llm_connector.providers.anthropic import AnthropicAdapter
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
