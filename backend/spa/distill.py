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
        "values, working style, motivations, and what they look for in an employer. Write factual, "
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
