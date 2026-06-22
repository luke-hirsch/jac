"""Scraper / company-research test suite for the cover-letter personal paragraph.

Covers the whole research → paragraph path:
  - the new web_search capability on the LLM connector (Anthropic adapter + base stub),
  - CompanyResearcher (jac.research),
  - PersonalParagraphWriter + ParagraphGroundingCheck (jac.llm_prompts),
  - CoverLetter.build() integration (gating, exclusion from snippet grounding, render).

All LLM/web I/O is mocked — no live calls. Wrap only the deliberate error-path tests in _muted().
See the personality side in spa/tests/test_personality.py.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from jac.cover_letter import CoverLetter
from jac.llm_prompts import ParagraphGroundingCheck, PersonalParagraphWriter
from jac.models import Job, JobPostAddress, ResumeSnippet
from jac.research import CompanyResearcher
from llm_connector.base import LLMAdapter
from llm_connector.providers.anthropic import AnthropicAdapter

from ._helpers import _CoverLetterCVMixin, _muted


class _Block:
    """A bare attribute bag standing in for an Anthropic SDK response/content block."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ===========================================================================
# web_search capability (llm_connector)
# ===========================================================================


class WebSearchCapabilityTests(TestCase):
    def test_base_adapter_has_no_web_search(self):
        class Dummy(LLMAdapter):
            def complete(self, messages, **kw):
                return ""

            def stream(self, messages, **kw):
                yield ""

        with self.assertRaises(NotImplementedError):
            Dummy({}).web_search([{"role": "user", "content": "x"}])


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


# ===========================================================================
# CompanyResearcher (jac.research)
# ===========================================================================


class CompanyResearcherTests(TestCase):
    def test_ok_dossier(self):
        with patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds X.", "sources": ["https://a"]},
        ):
            out = CompanyResearcher("Acme", "posting", alias="strong").research()
        self.assertTrue(out["ok"])
        self.assertIn("Acme", out["dossier"])
        self.assertEqual(out["sources"], ["https://a"])

    def test_empty_company_skips_search(self):
        with patch("jac.research.web_search") as m:
            out = CompanyResearcher("", "posting").research()
        self.assertFalse(out["ok"])
        m.assert_not_called()

    def test_no_web_search_backend_is_graceful(self):
        with patch("jac.research.web_search", side_effect=NotImplementedError):
            out = CompanyResearcher("Acme", "p").research()
        self.assertFalse(out["ok"])

    def test_failure_returns_empty(self):
        with _muted(), patch(
            "jac.research.web_search", side_effect=RuntimeError("down")
        ):
            out = CompanyResearcher("Acme", "p").research()
        self.assertFalse(out["ok"])
        self.assertEqual(out["dossier"], "")

    def test_blank_text_is_not_ok(self):
        with patch(
            "jac.research.web_search", return_value={"text": "   ", "sources": []}
        ):
            out = CompanyResearcher("Acme", "p").research()
        self.assertFalse(out["ok"])


# ===========================================================================
# PersonalParagraphWriter + ParagraphGroundingCheck (jac.llm_prompts)
# ===========================================================================


class PersonalParagraphWriterTests(TestCase):
    def test_returns_prose_with_both_dossiers(self):
        with patch(
            "jac.llm_prompts.complete", return_value="  I admire Acme.  "
        ) as m:
            txt = PersonalParagraphWriter(
                company_dossier="Acme builds X",
                personality_dossier="Loves building",
                title="Dev",
            ).write()
        self.assertEqual(txt, "I admire Acme.")
        m.assert_called_once()

    def test_empty_without_company_dossier(self):
        with patch("jac.llm_prompts.complete") as m:
            txt = PersonalParagraphWriter(
                company_dossier="", personality_dossier="P"
            ).write()
        self.assertEqual(txt, "")
        m.assert_not_called()

    def test_empty_without_personality_dossier(self):
        with patch("jac.llm_prompts.complete") as m:
            txt = PersonalParagraphWriter(
                company_dossier="C", personality_dossier=""
            ).write()
        self.assertEqual(txt, "")
        m.assert_not_called()

    def test_llm_failure_returns_empty(self):
        with _muted(), patch(
            "jac.llm_prompts.complete", side_effect=RuntimeError("x")
        ):
            txt = PersonalParagraphWriter(
                company_dossier="C", personality_dossier="P"
            ).write()
        self.assertEqual(txt, "")


class ParagraphGroundingCheckTests(TestCase):
    def test_counts_unsupported_claims(self):
        with patch(
            "jac.llm_prompts.complete",
            return_value="UNSUPPORTED 1\n- invented an award",
        ):
            out = ParagraphGroundingCheck("para", "C", "P").critique()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["claims"], ["invented an award"])

    def test_clean_when_zero(self):
        with patch("jac.llm_prompts.complete", return_value="UNSUPPORTED 0"):
            out = ParagraphGroundingCheck("para", "C", "P").critique()
        self.assertEqual(out, {"count": 0, "claims": []})

    def test_failure_is_none_not_zero(self):
        with _muted(), patch(
            "jac.llm_prompts.complete", side_effect=RuntimeError("x")
        ):
            out = ParagraphGroundingCheck("para", "C", "P").critique()
        self.assertEqual(out, {"count": None, "claims": []})


# ===========================================================================
# CoverLetter.build() integration
# ===========================================================================


class CoverLetterPersonalParagraphTests(_CoverLetterCVMixin, TestCase):
    """The paragraph is strong-grade + opt-in only, degrades gracefully, is rendered into the
    letter, and is kept OUT of the snippet faithfulness audit."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "pp_user", email="me@example.com", first_name="Ada", last_name="Lovelace"
        )
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Shipped Y.",
            kind=K.achievement,
            job=cls.job,
        )
        # A cached, fresh personality dossier so ensure_dossier() never calls the distiller here.
        from spa.models import PersonalityProfile

        prof = PersonalityProfile.objects.get(user=cls.user)
        prof.answers = {"values": "I value openness."}
        prof.answers_updated_at = timezone.now() - timedelta(hours=1)
        prof.dossier = "Ada values openness and craftsmanship."
        prof.dossier_built_at = timezone.now()
        prof.save()

    def _build(self, **kw):
        return CoverLetter(
            self.user,
            self._jp(),
            self._cv(),
            address=JobPostAddress(company="Acme"),
            **kw,
        ).build()

    def test_paragraph_present_on_strong_when_requested(self):
        with patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds rockets.", "sources": ["https://acme"]},
        ), patch(
            "jac.llm_prompts.complete",
            side_effect=["Snippet body.", "I love Acme's mission."],
        ):
            r = self._build(grade="strong", personal_paragraph=True)
        self.assertEqual(r["personal_paragraph"], "I love Acme's mission.")
        self.assertEqual(r["personal_paragraph_sources"], ["https://acme"])
        self.assertIn("I love Acme's mission.", r["text"])

    def test_no_paragraph_on_standard_grade(self):
        with patch("jac.research.web_search") as ws, patch(
            "jac.llm_prompts.complete", return_value="Body."
        ):
            r = self._build(grade="standard", personal_paragraph=True)
        self.assertEqual(r["personal_paragraph"], "")
        ws.assert_not_called()

    def test_no_paragraph_when_not_requested(self):
        with patch("jac.research.web_search") as ws, patch(
            "jac.llm_prompts.complete", return_value="Body."
        ):
            r = self._build(grade="strong", personal_paragraph=False)
        self.assertEqual(r["personal_paragraph"], "")
        ws.assert_not_called()

    def test_no_paragraph_without_personality_answers(self):
        other = User.objects.create_user("pp_nopersona")
        Job.objects.create(
            user=other, title="Dev", company="X", started=date(2020, 1, 1)
        )
        cv = self._cv()  # snippets belong to self.user; selection just yields none for `other`
        with patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds rockets.", "sources": []},
        ), patch("jac.llm_prompts.complete", side_effect=["Body."]):
            r = CoverLetter(
                other,
                self._jp(),
                cv,
                address=JobPostAddress(company="Acme"),
                grade="strong",
                personal_paragraph=True,
            ).build()
        self.assertEqual(r["personal_paragraph"], "")

    def test_paragraph_excluded_from_snippet_grounding(self):
        with patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds rockets.", "sources": []},
        ), patch("jac.cover_letter.FaithfulnessCheck") as FC, patch(
            "jac.llm_prompts.complete",
            side_effect=["Snippet body.", "I love Acme rockets.", "UNSUPPORTED 0"],
        ):
            FC.return_value.critique.return_value = {"count": 0, "claims": []}
            r = self._build(
                grade="strong", personal_paragraph=True, verify_grounding=True
            )
        body_arg = (
            FC.call_args.args[0] if FC.call_args.args else FC.call_args.kwargs["body"]
        )
        self.assertIn("Snippet body.", body_arg)
        self.assertNotIn("I love Acme rockets.", body_arg)
        self.assertEqual(r["personal_paragraph"], "I love Acme rockets.")
