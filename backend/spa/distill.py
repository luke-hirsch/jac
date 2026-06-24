import logging

from llm_connector import complete

from spa.personality_questions import _QUESTION_LABEL

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

    def __init__(self, answers: dict, *, alias: str = "default", user=None):
        self.answers = answers or {}
        self.alias = alias
        self.user = user

    def distill(self) -> str:
        if not any(self.answers.values()):
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("PersonalityDistiller: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        blocks = "\n\n".join(
            f"Q: {_QUESTION_LABEL.get(qid, qid)}\nA: {ans}"
            for qid, ans in self.answers.items()
            if ans
        )
        return f"{self._INSTRUCTION}\n\n{blocks}\n\nDOSSIER:"
