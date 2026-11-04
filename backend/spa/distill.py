import logging

from llm_connector import complete

logger = logging.getLogger(__name__)


class PersonalityDistiller:
    """Turn raw questionnaire answers into a compact, reusable personality dossier (1 LLM call).

    Output is free prose (not line-format): a short factual character sketch the paragraph writer
    can draw on. Any failure -> '' so callers fall back to no personal paragraph.
    """

    _INSTRUCTION = (
        "Below are a candidate's own answers to a short questionnaire about how they work and what "
        "they value. Distil them into a compact 'personality dossier': 4-6 sentences capturing their "
        "values, working style, motivations, and what they look for in an employer. Write positiv AND factual, "
        "third-person prose grounded ONLY in the answers — invent nothing, add no skills or "
        "achievements. No headers, no markdown, no preamble."
    )

    def __init__(self, answers: dict, *, labels: dict | None = None, executor):
        self.answers = answers or {}
        self.labels = (
            labels or {}
        )  # {slug: prompt}; falls back to the slug when missing
        self.executor = executor

    def distill(self) -> str:
        if not any(self.answers.values()):
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("PersonalityDistiller: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        blocks = "\n\n".join(
            f"Q: {self.labels.get(qid, qid)}\nA: {ans}"
            for qid, ans in self.answers.items()
            if ans
        )
        return f"{self._INSTRUCTION}\n\n{blocks}\n\nDOSSIER:"


class StyleDistiller:
    """Turn a pasted writing sample into a compact, reusable WRITING-STYLE dossier (1 LLM call).

    Describes HOW the person writes — sentence rhythm, register, vocabulary, characteristic
    constructions — never WHAT the sample was about, so no facts can leak from here into a letter.
    Free prose out; any failure -> '' so the writer just falls back to no style hint.
    """

    _INSTRUCTION = (
        "Below is a sample of a person's own writing. Describe their WRITING STYLE so another writer "
        "could imitate their voice: sentence length and rhythm, register (formal↔casual), vocabulary, "
        "punctuation habits, and any characteristic turns of phrase. 3-5 sentences, factual and "
        "instructional. Describe ONLY the style — never restate the sample's topic or any fact from "
        "it. No headers, no markdown, no preamble."
    )
    _MAX_SAMPLE_CHARS = 6000

    def __init__(self, sample: str, *, executor):
        self.sample = sample or ""
        self.executor = executor

    def distill(self) -> str:
        if not self.sample.strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("StyleDistiller: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\n\n"
            f"WRITING SAMPLE:\n{self.sample[: self._MAX_SAMPLE_CHARS]}\n\nSTYLE:"
        )
