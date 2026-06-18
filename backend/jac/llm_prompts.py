import logging
import math
import re

from llm_connector import complete, embed

logger = logging.getLogger(__name__)


class Embed:
    _EMBED_INTSTRUCT = (
        "Given a job posting, retrieve the CV entries most relevant to it."
    )
    _MAX_TOKENS = 30000  # could/should be derived from config/settings

    def __init__(
        self, job_post_text: str, entries: list[dict], user=None, alias: str = "default"
    ):
        self.job_post_text = job_post_text
        self.entries = entries
        self.user = user
        self.alias = alias
        self.flatten_entries = [e.get("text") or "" for e in entries]

    def ranked_entries(self) -> list[dict]:
        """rank the cv entries based on cosine similarity"""
        vectors = self._query()

        if len(vectors) != len(self.entries) + 1:
            return []
        query_vec, doc_vecs = vectors[0], vectors[1:]
        return [
            {"id": e.get("id"), "score": self._cos(query_vec, dv), "reason": ""}
            for e, dv in zip(self.entries, doc_vecs)
        ]

    def _query(self) -> list:
        """string concatonate the job post text with each entry text"""

        inputs = [
            f"Instruct: {self._EMBED_INTSTRUCT}\nQuery:{self._cap_job_post()}\n"
        ] + self.flatten_entries
        return embed(inputs=inputs, alias=self.alias, user=self.user)

    def _cap_job_post(self) -> str:
        """caps job post to _MAX_TOKENS by summerizing if necessary"""
        tokens_of_entries = 80 * len(
            self.flatten_entries
        )  # could actually be measured be tokenizer
        tokens_of_job_post_text = len(self.job_post_text.split()) * 4
        tokens = tokens_of_entries + tokens_of_job_post_text
        if tokens < self._MAX_TOKENS:
            return self.job_post_text
        else:
            try:
                # to do:
                # summerize ai job post, tokens fit
                # todo: decide if standard or embeded model summerize the job post
                return self.job_post_text
            except Exception:
                reduced_char = len(self.job_post_text) - (tokens - self._MAX_TOKENS) * 4
                return self.job_post_text[:reduced_char]

    def _cos(self, a, b) -> float:
        """Cosine similarity of two vectors. 0.0 if either is empty/zero-norm."""
        d = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return d / (na * nb) if na and nb else 0.0


class Conversational:
    pass


class Instruct:
    """`standard` rung: an instruction-tuned LLM rates each CV entry's relevance to the
    posting on a small integer scale (0–3). Mirrors `Embed`'s shape — construct, then call
    `ranked_entries()` — but returns LLM relevance *labels*, not cosine scores: `CVFilter`
    selects on the labels (keep-by-verdict), so there is no absolute floor to mis-calibrate.

    Provider-agnostic:  no provider-specific generation kwargs. It leans on a strict
    line-format instruction (one `<id> <rating>` per line — never JSON, which collapses every
    pair onto one line and defeats the per-line parser) + tolerant parsing, so the same code
    works for Ollama / OpenAI / Anthropic configs. Any failure returns [] -> CVFilter degrades
    to light.
    """

    _INSTRUCTION = (
        "You are screening CV entries for relevance to a job posting.\n"
        "Rate EVERY entry from 0 to 3:\n"
        "  3 = directly required by the posting / strong match\n"
        "  2 = clearly relevant, worth showing\n"
        "  1 = weakly or tangentially relevant\n"
        "  0 = not relevant to this posting\n"
        "Output ONE line per entry, formatted `<id> <rating>`, e.g.:\n"
        "skill:3 2\n"
        "job:1 0\n"
        "Use the exact ids given below. No prose, no markdown, no code fences, no JSON."
    )
    _MAX_POST_CHARS = (
        12000  # crude cap; entry text is already capped in _flatten_entries
    )
    _LABEL_MAX = 3
    # Match every `<id> <rating>` pair anywhere in the reply. Scanning the whole text (not
    # per line) keeps parsing robust if a model ignores the format and emits one-line JSON.
    _LABEL_PAIR = re.compile(r"([a-z]+:\d+)\D+?(\d+)")

    def __init__(
        self, job_post_text: str, entries: list[dict], user=None, alias: str = "default"
    ):
        self.job_post_text = job_post_text
        self.entries = entries
        self.user = user
        self.alias = alias

    def ranked_entries(self) -> list[dict]:
        """Return [{id, score, reason}] with score = integer relevance label (0.._LABEL_MAX)."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("Instruct scorer: LLM call failed")
            return []
        labels = self._parse(raw)
        if not labels:
            logger.warning("Instruct scorer: no parseable labels in reply")
            return []
        return [
            {"id": e["id"], "score": labels.get(e["id"], 0), "reason": ""}
            for e in self.entries
        ]

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        lines = "\n".join(f"{e['id']} — {e.get('text') or ''}" for e in self.entries)
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f'CV ENTRIES (id — text):\n{lines}\n\nRATINGS (one "<id> <0-3>" per line):'
        )

    def _parse(self, raw: str) -> dict:
        valid = {e["id"] for e in self.entries}
        out = {}
        for m in self._LABEL_PAIR.finditer(raw or ""):
            eid, score = m.group(1), m.group(2)
            if eid in valid:
                out[eid] = max(0, min(self._LABEL_MAX, int(score)))
        return out
