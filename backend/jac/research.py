import logging

from llm_connector import can_web_search, web_search

logger = logging.getLogger(__name__)


class CompanyResearcher:
    """Research the employer with a web-search-capable LLM and return a factual dossier + sources.

    The posting is given only as light context (so the model knows which company/role) — the goal is
    facts found ONLINE, beyond the ad. Degrades gracefully: an empty company, a non-web-capable alias
    (can_web_search False, no call made), blank text, or any failure -> {"ok": False, "dossier": "",
    "sources": []}.
    """

    _INSTRUCTION = (
        "Research the company named below using web search. Write a concise factual dossier (4-7 "
        "sentences) covering: what the company does, its mission/values, notable recent news or "
        "direction, and its culture or reputation. State ONLY what you can find online; if something "
        "is uncertain, omit it. Do not mention the job posting. No headers, no markdown."
    )

    def __init__(
        self,
        company,
        posting_text,
        *,
        alias="default",
        user=None,
        language="en",
        max_uses=5,
    ):
        self.company = (company or "").strip()
        self.posting_text = posting_text or ""
        self.alias = alias
        self.user = user
        self.language = language
        self.max_uses = max_uses

    def research(self) -> dict:
        if not self.company:
            return self._empty()
        if not can_web_search(self.alias, self.user):
            logger.info(
                "CompanyResearcher: alias %s has no web search; skipping", self.alias
            )
            return self._empty()
        try:
            res = web_search(
                prompt=self._prompt(),
                alias=self.alias,
                user=self.user,
                max_uses=self.max_uses,
            )
        except NotImplementedError:  # backstop if a flag is wrong
            return self._empty()
        except Exception:
            logger.exception("CompanyResearcher: web search failed")
            return self._empty()
        text = (res.get("text") or "").strip()
        if not text:
            return self._empty()
        return {"ok": True, "dossier": text, "sources": res.get("sources", [])}

    @staticmethod
    def _empty() -> dict:
        return {"ok": False, "dossier": "", "sources": []}

    def _prompt(self) -> str:
        ctx = self.posting_text[:600]
        return (
            f"{self._INSTRUCTION}\nWrite the dossier in {self.language}.\n\n"
            f"COMPANY: {self.company}\n\n"
            f"(role context, do not quote): {ctx}\n\nDOSSIER:"
        )
