"""LIVE statistical prompt-quality tests against HirschAI (local ollama / the
tower). These are the acceptance tests for the prompts themselves — structure
AND quality. We test a statistical model, so we test statistically: each check
runs RUNS times and passes at PASS_RATE (Lukas, 2026-07-16).

- Skips loudly ("HirschAi offline …") when the tower doesn't answer the probe.
- Knobs: JAC_PROMPT_RUNS (default 5), JAC_PROMPT_PASS_RATE (default 0.6).
- Cost/time: ~RUNS runs × 7 prompts on the tower's small model — minutes, not
  seconds. For a quick smoke: JAC_PROMPT_RUNS=1 python manage.py test
  jac.tests.test_prompts
- Commercial executors are deliberately NOT exercised here (no paid keys in the
  suite); the high-mode prompts get their live check in the guide's manual smoke.

Target API = `[backend]-pipeline-single-executor`.
"""

import os
import unittest

from django.test import TestCase

from jac.llm_prompts import (
    AddressExtract,
    CoverLetterWriter,
    Embed,
    FaithfulnessCheck,
    Instruct,
    LetterCritic,
)
from jac.models import ResumeSnippet
from llm_connector.executor import Executor
from llm_connector.probe import hirschai_reachable

RUNS = int(os.getenv("JAC_PROMPT_RUNS", "5"))
PASS_RATE = float(os.getenv("JAC_PROMPT_PASS_RATE", "0.6"))

POSTING = (
    "Acme GmbH is hiring a Python Backend Engineer in Berlin.\n"
    "You will build Django REST services and PostgreSQL data models and deploy "
    "them on Linux servers.\n"
    "Contact: Jane Doe, Acme GmbH, Musterstrasse 1, 10115 Berlin.\n"
    "Apply by 2026-09-01."
)

RELEVANT = "skill:1"
IRRELEVANT = "skill:2"

ENTRIES = [
    {"id": "job:1", "type": "job",
     "text": "Backend engineer at DataCorp (2020-2024) | skills: Python, Django",
     "refs": [RELEVANT], "favourite": False},
    {"id": "job:2", "type": "job",
     "text": "Barista at CoffeeCo (2018-2019)", "refs": [], "favourite": False},
    {"id": RELEVANT, "type": "skill", "text": "Python (expert, technical)",
     "refs": [], "favourite": False},
    {"id": IRRELEVANT, "type": "skill", "text": "Basket weaving (beginner, other)",
     "refs": [], "favourite": False},
    {"id": "language:1", "type": "language", "text": "English (native)",
     "refs": [], "favourite": False},
]

# Unsaved instances — the writer prompts only read attributes.
SNIPPETS = [
    ResumeSnippet(
        kind=ResumeSnippet.Kind.intro, title="intro", language="en",
        content=(
            "I am a backend engineer with six years of Python and Django "
            "experience, building REST services used by thousands of customers."
        ),
    ),
    ResumeSnippet(
        kind=ResumeSnippet.Kind.achievement, title="pipeline", language="en",
        content=(
            "At DataCorp I designed a PostgreSQL reporting pipeline that cut "
            "nightly batch times from four hours to twenty minutes."
        ),
    ),
    ResumeSnippet(
        kind=ResumeSnippet.Kind.closing, title="closing", language="en",
        content="I would be glad to discuss how I can contribute to your team.",
    ),
]

REFUSAL_MARKERS = ("i can't", "i cannot", "i'm sorry", "as an ai")


class LivePromptTestCase(TestCase):
    """Base class: every subclass skips as one block when the tower is down."""

    executor = Executor("ollama")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not hirschai_reachable(refresh=True):
            raise unittest.SkipTest(
                "HirschAi offline — prompt-quality tests skipped"
            )

    def assertPasses(self, check, msg: str):
        """Statistical assertion: `check()` (bool; exceptions count as failure)
        must succeed in >= PASS_RATE of RUNS runs."""
        ok = 0
        for _ in range(RUNS):
            try:
                ok += bool(check())
            except Exception:  # noqa: BLE001 — a crashed run is a failed run
                pass
        self.assertGreaterEqual(
            ok / RUNS, PASS_RATE, f"{msg}: {ok}/{RUNS} runs passed"
        )


class EmbedPromptTests(LivePromptTestCase):
    def test_cosine_ranks_the_relevant_skill_above_the_irrelevant(self):
        def check():
            ranked = Embed(POSTING, ENTRIES).ranked_entries()
            if len(ranked) != len(ENTRIES):
                return False
            scores = {r["id"]: r["score"] for r in ranked}
            return scores[RELEVANT] > scores[IRRELEVANT]

        self.assertPasses(check, "embedding relevance ranking")


class InstructPromptTests(LivePromptTestCase):
    def test_labels_parse_for_most_entries(self):
        def check():
            ranked = Instruct(POSTING, ENTRIES, executor=self.executor).ranked_entries()
            return len(ranked) == len(ENTRIES)

        self.assertPasses(check, "instruct label format compliance")

    def test_labels_rank_the_relevant_skill_above_the_irrelevant(self):
        def check():
            ranked = Instruct(POSTING, ENTRIES, executor=self.executor).ranked_entries()
            if not ranked:
                return False
            scores = {r["id"]: r["score"] for r in ranked}
            return scores.get(RELEVANT, 0) > scores.get(IRRELEVANT, 0)

        self.assertPasses(check, "instruct relevance quality")


class AddressExtractPromptTests(LivePromptTestCase):
    def test_structure_and_company(self):
        def check():
            out = AddressExtract(POSTING, executor=self.executor).extract()
            return (
                bool(out)
                and set(out) <= set(AddressExtract._FIELDS)
                and "acme" in out.get("company", "").lower()
            )

        self.assertPasses(check, "address extraction structure + company")

    def test_deadline_comes_out_iso(self):
        def check():
            out = AddressExtract(POSTING, executor=self.executor).extract()
            return out.get("deadline") == "2026-09-01"

        self.assertPasses(check, "deadline extraction")


class CoverLetterWriterPromptTests(LivePromptTestCase):
    def test_standard_body_is_a_real_letter_body(self):
        def check():
            body = CoverLetterWriter(
                SNIPPETS,
                candidate_name="Alex Example",
                title="Python Backend Engineer",
                language="en",
                mode="standard",
                executor=self.executor,
            ).write()
            if not body:
                return False
            lowered = body.lower()
            words = len(body.split())
            return (
                60 <= words <= 400  # a letter body, not a fragment or an essay
                and "dear" not in lowered[:80]  # no salutation — furniture is ours
                # Roadmap "cover-letter refusal guard": a refusal must never
                # become the letter body.
                and not any(m in lowered for m in REFUSAL_MARKERS)
            )

        self.assertPasses(check, "standard writer output shape")


class FaithfulnessPromptTests(LivePromptTestCase):
    def test_a_verbatim_body_audits_clean(self):
        body = "\n\n".join(s.content for s in SNIPPETS)

        def check():
            res = FaithfulnessCheck(body, SNIPPETS, executor=self.executor).critique()
            return res["count"] == 0

        self.assertPasses(check, "clean body audits UNSUPPORTED 0")

    def test_a_fabricated_claim_is_flagged(self):
        body = (
            "\n\n".join(s.content for s in SNIPPETS)
            + "\n\nI also won the Nobel Prize in Physics in 2020."
        )

        def check():
            res = FaithfulnessCheck(body, SNIPPETS, executor=self.executor).critique()
            return (res["count"] or 0) >= 1

        self.assertPasses(check, "fabrication is flagged")


class LetterCriticPromptTests(LivePromptTestCase):
    def test_the_critique_parses(self):
        body = "\n\n".join(s.content for s in SNIPPETS)

        def check():
            res = LetterCritic(body, SNIPPETS, executor=self.executor).critique()
            # Structure over opinion: the reply must parse (count is an int —
            # None means the line format broke), claims must be a list.
            return isinstance(res.get("count"), int) and isinstance(
                res.get("claims"), list
            )

        self.assertPasses(check, "critic line-format compliance")
