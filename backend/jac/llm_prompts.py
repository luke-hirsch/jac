import math

from llm_connector import embed


class Embed:
    _EMBED_INTSTRUCT = (
        "Given a job posting, retrieve the CV entries most relevant to it."
    )
    _MAX_TOKENS = 30000  # could/should be derived from config/settings

    def __init__(self, job_post_text: str, entries: list[dict]):
        self.job_post_text = job_post_text
        self.entries = entries
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
        return embed(inputs=inputs)

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
    pass


# cd /Users/lukas/Projects/jac/backend && python manage.py shell -c "
# import json, urllib.request, math, statistics
# from jac.cv import CV


# posting = open('/Users/lukas/Projects/jac/data/test_job.md').read()
# flat = CV(user_pk=1)._entries_for_llm()
# docs = [e['text'][:300] for e in flat]
# INSTR = 'Instruct: Given a job posting, retrieve the CV entries most relevant to it.\nQuery:'
# for label, q, dd in [('qwen3-instruct', INSTR + posting[:4000], docs), ('raw', posting[:4000], docs)]:
#     vecs = embed([q] + dd); dim = len(vecs[0]); qv = vecs[0]
#     sims = [(cos(qv, v), flat[i]['id'], flat[i]['text'][:42]) for i, v in enumerate(vecs[1:])]
#     sims.sort(reverse=True); vals = [s[0] for s in sims]
#     print(f'=== {label}  dim={dim}  min={min(vals):.3f} med={statistics.median(vals):.3f} max={max(vals):.3f} p75={vals[len(vals)//4]:.3f} ===')
#     for s in sims[:7]: print('  %.3f %-14s %s' % s)
#     print('  ...bottom:', '  '.join('%.3f %s' % (s[0], s[1]) for s in sims[-3:]))
# "
