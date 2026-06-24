"""LLM rungs — tolerant line-format parsers + safe public calls (no network)."""

from unittest.mock import patch

from django.test import TestCase

from jac.llm_prompts import (
    AddressExtract,
    Conversational,
    CoverLetterWriter,
    Embed,
    FaithfulnessCheck,
    Instruct,
    ParagraphGroundingCheck,
    PersonalParagraphWriter,
    TheAnalyst,
    TheJudge,
    _parse_unsupported,
)

from ._helpers import _muted, _entry, _StubSnippet


class InstructScorerParseTests(TestCase):
    """Instruct._parse: tolerant line parsing, validating, clamping — no network."""

    def _scorer(self):
        entries = [
            _entry("skill:1", "skill", text="Python"),
            _entry("job:1", "job", text="Dev at X"),
        ]
        return Instruct("posting", entries)

    def test_parses_clean_lines(self):
        self.assertEqual(
            self._scorer()._parse("skill:1 3\njob:1 1"),
            {"skill:1": 3, "job:1": 1},
        )

    def test_tolerates_markdown_and_separator_drift(self):
        # bullets, em-dash, colon, code fences, blank lines — all survive.
        raw = "```\n- skill:1: 2\n1. job:1 — 0\n```"
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_extracts_lines_amid_prose(self):
        raw = "Sure! Here are the ratings:\nskill:1 2\njob:1 0\nHope that helps."
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_partial_reply_keeps_complete_lines(self):
        # truncated mid-reply: skill:1 parses, the dangling line is ignored.
        self.assertEqual(self._scorer()._parse("skill:1 3\njob"), {"skill:1": 3})

    def test_unknown_ids_dropped_and_labels_clamped(self):
        raw = "skill:1 9\njob:1 0\nskill:999 2"
        # 9 -> clamped to _LABEL_MAX(3); unknown id dropped.
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 3, "job:1": 0})

    def test_parses_single_line_json(self):
        # Regression: a model that ignores the line format and emits compact one-line
        # JSON must still yield EVERY pair. The old per-line parser grabbed only the first,
        # leaving a truthy-but-near-empty label map that masked the light fallback and
        # collapsed selection to the min_keep skeleton for every posting.
        raw = '{"skill:1": 2, "job:1": 0}'
        self.assertEqual(self._scorer()._parse(raw), {"skill:1": 2, "job:1": 0})

    def test_parses_multiple_pairs_on_one_line(self):
        self.assertEqual(
            self._scorer()._parse("skill:1 3 job:1 1"), {"skill:1": 3, "job:1": 1}
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(self._scorer()._parse("no ratings here at all"), {})

    def test_ranked_entries_empty_on_parse_failure(self):
        with _muted(), patch("jac.llm_prompts.complete", return_value="garbage"):
            self.assertEqual(self._scorer().ranked_entries(), [])

    def test_ranked_entries_empty_on_llm_error(self):
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._scorer().ranked_entries(), [])

    def test_ranked_entries_maps_labels(self):
        with patch("jac.llm_prompts.complete", return_value="skill:1 3\njob:1 1"):
            ranked = self._scorer().ranked_entries()
        self.assertEqual(
            {r["id"]: r["score"] for r in ranked}, {"skill:1": 3, "job:1": 1}
        )


class ConversationalSelectorTests(TestCase):
    """Conversational._parse / selection(): tolerant, validating, ordered — no network."""

    def _selector(self):
        entries = [
            _entry("skill:1", "skill", text="Python"),
            _entry("job:1", "job", text="Dev at X"),
        ]
        return Conversational("posting", entries)

    def test_parses_ordered_selection(self):
        raw = "job:1 — core\nskill:1 — req"
        self.assertEqual(
            self._selector()._parse(raw),
            [{"id": "job:1", "why": "core"}, {"id": "skill:1", "why": "req"}],
        )

    def test_tolerates_markdown_and_extracts_amid_prose(self):
        # bullets, code fences, a reasonless pick, and an unknown id -> only valid kept.
        raw = "Here is my pick:\n```\n- skill:1\n2. skill:999 — x\n```"
        self.assertEqual(self._selector()._parse(raw), [{"id": "skill:1", "why": ""}])

    def test_dedupes_preserving_order(self):
        raw = "job:1 — a\njob:1 — b\nskill:1 — c"
        self.assertEqual(
            [s["id"] for s in self._selector()._parse(raw)], ["job:1", "skill:1"]
        )

    def test_partial_reply_keeps_complete_picks(self):
        # truncated mid-reply: job:1 parses in order, dangling line ignored.
        self.assertEqual(
            self._selector()._parse("job:1 — core\nski"),
            [{"id": "job:1", "why": "core"}],
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(self._selector()._parse("no picks here"), [])

    def test_selection_empty_on_llm_error(self):
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._selector().selection(), [])


class EmbedAliasPassthroughTests(TestCase):
    """Embed forwards alias + user to embed() so the light rung honours --llm."""

    def _entries(self):
        return [_entry("skill:1", "skill", text="Python")]

    def test_query_passes_alias_and_user(self):
        with patch("jac.llm_prompts.embed", return_value=[[0.1]]) as m:
            Embed("posting", self._entries(), user=7, alias="reasoning")._query()
        _, kwargs = m.call_args
        self.assertEqual(kwargs["alias"], "reasoning")
        self.assertEqual(kwargs["user"], 7)

    def test_defaults_to_default_alias_no_user(self):
        with patch("jac.llm_prompts.embed", return_value=[[0.1]]) as m:
            Embed("posting", self._entries())._query()
        _, kwargs = m.call_args
        self.assertEqual(kwargs["alias"], "default")
        self.assertIsNone(kwargs["user"])


class JudgeCritiqueTests(TestCase):
    """Judge._parse / critique(): grade + id-anchored notes, tolerant, validating — no network."""

    def _judge(self):
        return TheJudge(
            "posting",
            kept=[{"id": "skill:1", "text": "Python"}],
            dropped=[{"id": "job:9", "text": "old job"}],
        )

    def test_parses_grade_and_notes(self):
        out = self._judge()._parse("GRADE B\njob:9 — required, should have stayed")
        self.assertEqual(out["grade"], "B")
        self.assertEqual(
            out["notes"], [{"id": "job:9", "note": "required, should have stayed"}]
        )

    def test_grade_only_yields_no_notes(self):
        out = self._judge()._parse("GRADE A")
        self.assertEqual(out["grade"], "A")
        self.assertEqual(out["notes"], [])

    def test_missing_grade_is_none(self):
        out = self._judge()._parse("skill:1 — weak match")
        self.assertIsNone(out["grade"])
        self.assertEqual(out["notes"], [{"id": "skill:1", "note": "weak match"}])

    def test_unknown_ids_dropped_and_deduped(self):
        out = self._judge()._parse(
            "GRADE C\nskill:99 — not in set\nskill:1 — weak\nskill:1 — dupe"
        )
        self.assertEqual(out["notes"], [{"id": "skill:1", "note": "weak"}])

    def test_tolerates_separator_drift(self):
        out = self._judge()._parse("GRADE D\njob:9: missing required stack")
        self.assertEqual(
            out["notes"], [{"id": "job:9", "note": "missing required stack"}]
        )

    def test_critique_safe_on_llm_error(self):
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._judge().critique(), {"grade": None, "notes": []})

    def test_critique_parses_reply(self):
        with patch(
            "jac.llm_prompts.complete", return_value="GRADE B\nskill:1 — weak match"
        ):
            out = self._judge().critique()
        self.assertEqual(out["grade"], "B")
        self.assertEqual(out["notes"], [{"id": "skill:1", "note": "weak match"}])


class AnalystSummaryTests(TestCase):
    """Analyst.analyse(): free-form prose passthrough, safe on failure — no network."""

    def test_prompt_includes_report(self):
        self.assertIn("REPORT-DATA", TheAnalyst("REPORT-DATA")._prompt())

    def test_analyse_returns_text(self):
        with patch("jac.llm_prompts.complete", return_value="the analysis"):
            self.assertEqual(TheAnalyst("r").analyse(), "the analysis")

    def test_analyse_empty_on_error(self):
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("x")):
            self.assertEqual(TheAnalyst("r").analyse(), "")


class AddressExtractParseTests(TestCase):
    def setUp(self):
        self.x = AddressExtract("posting")

    def test_parses_known_fields(self):
        raw = (
            "company: Acme GmbH\n"
            "contact_name: Jane Doe\n"
            "email: jobs@acme.com\n"
            "title: Backend Engineer\n"
            "language: de"
        )
        out = self.x._parse(raw)
        self.assertEqual(out["company"], "Acme GmbH")
        self.assertEqual(out["email"], "jobs@acme.com")
        self.assertEqual(out["language"], "de")

    def test_skips_unknown_blank_and_placeholder(self):
        raw = "company: Acme\nfoo: bar\ncity:\nemail: none\nphone: n/a"
        self.assertEqual(self.x._parse(raw), {"company": "Acme"})

    def test_tolerates_surrounding_prose(self):
        raw = "Here are the details:\ncompany: Acme\nThanks!"
        self.assertEqual(self.x._parse(raw), {"company": "Acme"})

    def test_extract_empty_on_llm_error(self):
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(AddressExtract("p").extract(), {})


class CoverLetterWriterPromptTests(TestCase):
    """The writer prompt carries the snippets + role, never the job posting."""

    def _writer(self):
        return CoverLetterWriter(
            [_StubSnippet("Achv", "Shipped the billing service.")],
            candidate_name="Ada Lovelace",
            title="Backend Engineer",
            language="en",
        )

    def test_prompt_includes_snippets_and_role(self):
        p = self._writer()._prompt()
        self.assertIn("Shipped the billing service.", p)
        self.assertIn("Backend Engineer", p)
        self.assertIn("Ada Lovelace", p)

    def test_prompt_omits_job_posting(self):
        p = self._writer()._prompt()
        self.assertNotIn("JOB POSTING", p)

    def test_common_clause_forbids_invention(self):
        p = self._writer()._prompt()
        self.assertIn("Use ONLY facts stated in the snippets", p)

    def test_write_returns_empty_without_snippets(self):
        w = CoverLetterWriter([], title="X")
        self.assertEqual(w.write(), "")


class FaithfulnessCheckParseTests(TestCase):
    """_parse_unsupported / FaithfulnessCheck.critique: tolerant line parsing, honest failure
    default. The parse logic is shared (module-level _parse_unsupported), driven by the class's
    own UNSUPPORTED/claim regexes."""

    def _check(self):
        return FaithfulnessCheck("some body", [_StubSnippet("A", "I ship code.")])

    def _parse(self, raw):
        return _parse_unsupported(
            raw, FaithfulnessCheck._COUNT_RE, FaithfulnessCheck._CLAIM_RE
        )

    def test_clean_verdict_is_zero(self):
        self.assertEqual(self._parse("UNSUPPORTED 0"), {"count": 0, "claims": []})

    def test_lists_claims_and_counts_them(self):
        raw = "UNSUPPORTED 2\n- Led a team of 10\n- Increased revenue 30%"
        self.assertEqual(
            self._parse(raw),
            {"count": 2, "claims": ["Led a team of 10", "Increased revenue 30%"]},
        )

    def test_trusts_listed_claims_over_declared_count(self):
        # declared 1 but two bullets present -> the bullets win.
        raw = "UNSUPPORTED 1\n- claim a\n* claim b"
        self.assertEqual(self._parse(raw)["count"], 2)

    def test_tolerates_markdown_and_prose(self):
        raw = "Here is the audit:\nUNSUPPORTED 1\n1. Managed a 5M budget\nDone."
        self.assertEqual(
            self._parse(raw), {"count": 1, "claims": ["Managed a 5M budget"]}
        )

    def test_positive_count_but_no_claims_is_not_checked(self):
        # truncated reply: count says 2 but no bullets parsed -> None, never a false 0.
        self.assertEqual(self._parse("UNSUPPORTED 2"), {"count": None, "claims": []})

    def test_garbage_is_not_checked(self):
        self.assertEqual(
            self._parse("the letter looks fine to me"),
            {"count": None, "claims": []},
        )

    def test_critique_none_on_llm_error(self):
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("down")):
            self.assertEqual(self._check().critique(), {"count": None, "claims": []})

    def test_critique_parses_live_reply(self):
        with patch(
            "jac.llm_prompts.complete", return_value="UNSUPPORTED 1\n- Fake cert"
        ):
            self.assertEqual(
                self._check().critique(), {"count": 1, "claims": ["Fake cert"]}
            )


class PersonalParagraphWriterTests(TestCase):
    def test_returns_prose_with_both_dossiers(self):
        with patch("jac.llm_prompts.complete", return_value="  I admire Acme.  ") as m:
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
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("x")):
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
        with _muted(), patch("jac.llm_prompts.complete", side_effect=RuntimeError("x")):
            out = ParagraphGroundingCheck("para", "C", "P").critique()
        self.assertEqual(out, {"count": None, "claims": []})
