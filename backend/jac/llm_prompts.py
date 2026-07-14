import logging
import math
import re

from llm_connector import can_web_search, complete, embed, web_search

logger = logging.getLogger(__name__)


def _parse_unsupported(raw: str, count_re, claim_re) -> dict:
    """Parse a line-format faithfulness audit into {'count': int | None, 'claims': [str]}.

    Shared by FaithfulnessCheck (snippets) and ParagraphGroundingCheck (research + personality).
    Honesty rule: listed claim lines win (trust their length over the declared n); only an explicit
    'UNSUPPORTED 0' is a clean verdict; anything unreadable -> count=None ('not checked'), never 0.
    """
    text = raw or ""
    cm = count_re.search(text)
    claims: list[str] = []
    for line in text.splitlines():
        if count_re.search(line):  # don't read the count line as a claim
            continue
        m = claim_re.match(line)
        if m:
            claims.append(m.group(1).strip()[:200])
    if claims:
        return {"count": len(claims), "claims": claims}
    if cm and cm.group(1) == "0":
        return {"count": 0, "claims": []}
    return {"count": None, "claims": []}


_LANGUAGE_NAMES = {"en": "English", "de": "German"}


def _language_name(code: str) -> str:
    """ISO-639-1 -> the language's English name for prompt text. 'Write in German.'
    is a real instruction; 'Write in de.' is a shrug a small model will ignore.
    Unknown codes pass through unchanged."""
    return _LANGUAGE_NAMES.get((code or "").strip().lower(), code)


class Embed:
    # Every prompt class declares the model tier it ideally runs on. The orchestrators
    # resolve it through `llm_connector.conf.pick_alias`: when the user pinned a
    # favourite model for that tier the rung runs there, otherwise on the run's main
    # alias — so a strong run keeps its support rungs off the expensive model.
    # None = the rung IS the grade (writers/selectors): it always runs the run's alias.
    PREFERRED_GRADE: str | None = "light"

    DOC_KIND = "cv"

    _EMBED_INSTRUCT = (
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
        return [
            {"id": r["id"], "score": r["score"], "reason": ""}
            for r in self.ranked_vectors()
        ]

    def ranked_vectors(self) -> list[dict]:
        """Like ranked_entries, but keeps each entry's raw vector (`vec`) so callers
        can measure entry-to-entry similarity (the cover-letter MMR pick).

        Store-first: when the vector store is enabled, doc vectors come from Qdrant
        and only the query is embedded (jac/vectors.py); on None — store off, no
        user, or any store failure — the classic full per-run embed below runs."""
        from jac.vectors import ranked_via_store

        stored = ranked_via_store(
            self._query_text(),
            self.entries,
            doc=self.DOC_KIND,
            user=self.user,
            alias=self.alias,
        )
        if stored is not None:
            return stored

        vectors = self._query()

        if len(vectors) != len(self.entries) + 1:
            return []
        query_vec, doc_vecs = vectors[0], vectors[1:]
        return [
            {"id": e.get("id"), "score": self._cos(query_vec, dv), "vec": dv}
            for e, dv in zip(self.entries, doc_vecs)
        ]

    def _query_text(self) -> str:
        """The instructed query string — the store path embeds ONLY this."""
        return f"Instruct: {self._EMBED_INSTRUCT}\nQuery:{self._cap_job_post()}\n"

    def _query(self) -> list:
        """Classic path: embed the query + every entry text in one batch."""
        return embed(
            inputs=[self._query_text()] + self.flatten_entries,
            alias=self.alias,
            user=self.user,
        )

    def _cap_job_post(self) -> str:
        """Cap the job-post text so (entries + post) fits _MAX_TOKENS. Hard char-truncation
        (~4 chars/token) — crude but real; the previous version returned the full text unchanged."""
        tokens_of_entries = 80 * len(self.flatten_entries)
        tokens_of_job_post = len(self.job_post_text.split()) * 4
        if tokens_of_entries + tokens_of_job_post <= self._MAX_TOKENS:
            return self.job_post_text
        room_tokens = max(self._MAX_TOKENS - tokens_of_entries, 0)
        return self.job_post_text[: room_tokens * 4]

    @staticmethod
    def _cos(a, b) -> float:
        """Cosine similarity of two vectors. 0.0 if either is empty/zero-norm."""
        d = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return d / (na * nb) if na and nb else 0.0


class SnippetEmbed(Embed):
    """Embed rung for cover-letter snippet selection: identical mechanics to the CV
    Embed, retrieval instruction retargeted at ResumeSnippets. Entries are
    `{"id": "<kind>:<pk>", "text": <content>}` dicts."""

    _EMBED_INSTRUCT = (
        "Given a job posting, retrieve the resume snippets most relevant to it."
    )
    DOC_KIND = "snippet"


class Conversational:
    """`strong` rung: a conversational LLM selects the CV holistically. It returns an
    ORDERED list of chosen entry ids (priority order, best first) each with a short `why`,
    rather than per-entry scores — CVFilter applies only guardrails (favourites, min_keep)
    on top, so the model's judgment drives the selection.

    Provider-agnostic (no provider-specific kwargs). The reply is a **line format**
    (`<id> — <why>`, one pick per line), not JSON — token-cheap and robust to truncation
    (see the `no-json-llm-io` memory). Any failure returns [] -> CVFilter degrades to the
    standard rung.
    """

    PREFERRED_GRADE: str | None = None  # the strong selector IS the grade

    _INSTRUCTION = (
        "You are a senior CV editor tailoring a ONE-PAGE CV to a specific job posting.\n"
        "From the candidate's full entry list below, choose the entries that make the "
        "strongest, most relevant CV for THIS posting and drop the rest. Use judgment:\n"
        "  - prefer entries the posting actually calls for; drop weak or off-topic ones;\n"
        "  - keep a skill if a job/project you are keeping clearly relies on it;\n"
        "  - it is fine to keep few entries for a poorly-matched posting, or many for a "
        "strong match — fit should decide the count, not a fixed quota.\n"
        "Output the entries you are KEEPING, best first, ONE per line: the entry id, "
        "then ' — ', then a short reason (≤12 words) grounded in THIS posting.\n"
        "Format only (invented id and reason — take yours from the list and posting below):\n"
        "job:17 — daily work matches the role's core responsibility\n"
        "Use the exact ids given below; include only ids you are keeping. "
        "No prose, no markdown, no other text."
    )
    _MAX_POST_CHARS = 12000

    # entry ids are  type:pk  (e.g. job:2); anchor on a leading id, the rest of the line is why.
    _PICK_RE = re.compile(r"([a-z]+:\d+)\s*[-—:.)\]]*\s*(.*)")

    def __init__(
        self, job_post_text: str, entries: list[dict], user=None, alias: str = "default"
    ):
        self.job_post_text = job_post_text
        self.entries = entries
        self.user = user
        self.alias = alias

    def selection(self) -> list[dict]:
        """Return an ordered [{id, why}] of chosen entries. [] on any failure."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("Conversational selector: LLM call failed")
            return []
        chosen = self._parse(raw)
        if not chosen:
            logger.warning("Conversational selector: no parseable selection in reply")
        return chosen

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"CANDIDATE ENTRIES (id — text):\n{self._grouped_entries()}\n\nSELECTION:"
        )

    def _grouped_entries(self) -> str:
        """List entries grouped by type for readability, ids verbatim."""
        by_type: dict[str, list[dict]] = {}
        for e in self.entries:
            by_type.setdefault(e["type"], []).append(e)
        blocks = []
        for etype, items in by_type.items():
            lines = "\n".join(f"  {e['id']} — {e.get('text') or ''}" for e in items)
            blocks.append(f"{etype.upper()}S:\n{lines}")
        return "\n\n".join(blocks)

    def _parse(self, raw: str) -> list[dict]:
        """Extract an ordered [{id, why}] from a line format (`<id> — <why>`, best first).

        Scans each line for a leading id pattern, taking the rest of the line as the reason,
        and ignores lines it can't read (so a truncated reply still yields every complete pick
        in order). Keeps only ids in this entry set, de-dupes preserving order, truncates
        `why`. Returns [] when nothing usable is found.
        """
        valid = {e["id"] for e in self.entries}
        out: list[dict] = []
        seen: set[str] = set()
        for line in (raw or "").splitlines():
            m = self._PICK_RE.search(line)
            if not m:
                continue
            eid = m.group(1)
            if eid in valid and eid not in seen:
                seen.add(eid)
                out.append({"id": eid, "why": m.group(2).strip()[:200]})
        return out


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

    PREFERRED_GRADE: str | None = None  # the standard scorer IS the grade

    _INSTRUCTION = (
        "You are screening CV entries for relevance to a job posting.\n"
        "Rate EVERY entry from 0 to 3:\n"
        "  3 = directly required by the posting / strong match\n"
        "  2 = clearly relevant, worth showing\n"
        "  1 = weakly or tangentially relevant\n"
        "  0 = not relevant to this posting\n"
        "Output ONE line per entry, formatted `<id> <rating>` — the id, one space, one "
        "digit. Format only (invented ids — rate the ids listed below, not these):\n"
        "skill:12 3\n"
        "job:17 0\n"
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


class TheJudge:
    """Selection-quality grader for one eval run: a fixed strong LLM reads the job posting plus
    what the pipeline KEPT vs DROPPED and critiques the choice — a letter grade for the run, then
    id-anchored notes on questionable keeps/drops. Used by `cv_eval --analyze` to turn raw
    counts/scores into a judgment and to feed the cross-run `Analyst` summary.

    Provider-agnostic. Line-format I/O (never JSON — see the `no-json-llm-io` memory): the reply's
    `GRADE <A-F>` line is parsed for the table; `<id> — <note>` lines are collected as critique,
    ids validated against this run's entry set, unreadable lines skipped. Any failure yields a null
    grade + empty notes so the analysis degrades instead of crashing.
    """

    _INSTRUCTION = (
        "You are auditing how well an automated system tailored a ONE-PAGE CV to a job posting.\n"
        "Below are the job posting, the entries the system KEPT (best first), and the entries it "
        "DROPPED. Judge the SELECTION quality for THIS posting:\n"
        "  - did it keep what the posting actually calls for, and drop the off-topic?\n"
        "  - flag any KEPT entry that is weak or irrelevant, and any DROPPED entry that should "
        "have stayed (e.g. a required skill).\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: the word GRADE, a space, and ONE letter A-F — overall selection "
        "quality (A best);\n"
        "  - then ONE line per problem, '<id> — <short note>' (<=15 words), worst first;\n"
        "  - if the selection is sound, emit no problem lines.\n"
        "Use the exact ids given below. No prose, no markdown, no JSON."
    )
    _MAX_POST_CHARS = 12000

    _GRADE_RE = re.compile(r"\bGRADE\s+([A-Fa-f])\b")
    # entry ids are  type:pk  (e.g. job:2); anchor on a leading id, the rest of the line is the note.
    _NOTE_RE = re.compile(r"([a-z]+:\d+)\s*[-—:.)\]]*\s*(.*)")

    def __init__(
        self,
        job_post_text: str,
        kept: list[dict],
        dropped: list[dict],
        user=None,
        alias: str = "default",
    ):
        self.job_post_text = job_post_text
        self.kept = kept  # [{id, text}], ranked best-first
        self.dropped = dropped  # [{id, text}]
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        """Return {'grade': 'A'..'F' | None, 'notes': [{id, note}]}. Safe defaults on any failure."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("Judge: LLM call failed")
            return {"grade": None, "notes": []}
        return self._parse(raw)

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        kept = (
            "\n".join(f"{e['id']} — {e.get('text') or ''}" for e in self.kept)
            or "(none)"
        )
        dropped = (
            "\n".join(f"{e['id']} — {e.get('text') or ''}" for e in self.dropped)
            or "(none)"
        )
        return (
            f"{self._INSTRUCTION}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"KEPT (best first):\n{kept}\n\n"
            f"DROPPED:\n{dropped}\n\n"
            f"VERDICT:"
        )

    def _parse(self, raw: str) -> dict:
        text = raw or ""
        gm = self._GRADE_RE.search(text)
        grade = gm.group(1).upper() if gm else None
        valid = {e["id"] for e in (self.kept + self.dropped)}
        notes: list[dict] = []
        seen: set[str] = set()
        for line in text.splitlines():
            if self._GRADE_RE.search(line):  # don't read the grade line as a note
                continue
            m = self._NOTE_RE.search(line)
            if not m:
                continue
            eid = m.group(1)
            if eid in valid and eid not in seen:
                seen.add(eid)
                notes.append({"id": eid, "note": m.group(2).strip()[:200]})
        return {"grade": grade, "notes": notes}


class TheAnalyst:
    """Cross-run summariser for `cv_eval --analyze`: a strong LLM reads the whole evaluation —
    every posting×model run's counts-vs-target, score range, elapsed time, and the per-run `Judge`
    grade + notes — and writes a human-readable analysis (which models/grades pick well, where they
    over/under-shoot, speed/quality trade-offs, recurring mistakes, next steps).

    Unlike the other rungs this output is read by a human (written to `analysis.md`), not parsed, so
    it returns free-form prose. Any failure returns '' so the caller can note the analysis was
    skipped.
    """

    _INSTRUCTION = (
        "You are analysing an evaluation of an automated CV-tailoring pipeline run over several job "
        "postings with several models/grades. Each run below shows how many entries it KEPT per "
        "section (vs a one-page target), the relevance-score range, elapsed time, and an auditor's "
        "letter grade plus notes on questionable keeps/drops.\n"
        "Write a concise COMPARATIVE summary for the engineer tuning the pipeline. Lead with how "
        "the models relate to each other:\n"
        "  - group the models that behave similarly, and call out any model that is off track;\n"
        "  - name postings (or sections) where ALL models do badly, and where they disagree most;\n"
        "  - note the speed/quality trade-off and which model/grade selects best overall;\n"
        "  - recurring selection mistakes across postings (cite ids);\n"
        "  - one or two concrete next steps.\n"
        "Open with a 2-3 sentence verdict: which models behave alike, which (if any) is off "
        "track, and where the models struggle most — claim only what the data shows; if no "
        "model is off track, or they all struggle nowhere, say exactly that. Then back it "
        "up. Be specific; cite postings, models, and ids."
    )

    def __init__(self, report: str, user=None, alias: str = "default"):
        self.report = report
        self.user = user
        self.alias = alias

    def analyse(self) -> str:
        try:
            return complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("Analyst: summary call failed")
            return ""

    def _prompt(self) -> str:
        return f"{self._INSTRUCTION}\n\nEVALUATION DATA:\n{self.report}\n\nANALYSIS:"


class AddressExtract:
    """Pull the employer's contact block out of a job posting with the chat/instruct model.

    Line-format I/O (`<field>: <value>` per line, parsed with `re`, unknown/blank/placeholder
    lines skipped) — never JSON (see the `no-json-llm-io` memory). Any failure or unparseable
    reply -> {} so the caller proceeds with blanks (the renderer just omits missing lines).
    """

    # Structured extraction — a mid-tier model reads a posting fine; no need to spend
    # a strong run's tokens on it.
    PREFERRED_GRADE: str | None = "standard"

    _FIELDS = (
        "company",
        "contact_name",
        "street",
        "address_line2",
        "zip",
        "city",
        "country",
        "email",
        "phone",
        "title",
        "language",
        "deadline",
    )
    _INSTRUCTION = (
        "Extract the EMPLOYER's contact details from the job posting below. Output one\n"
        "'<field>: <value>' per line, using exactly these field names:\n"
        "  company, contact_name, street, address_line2, zip, city, country, email, phone,\n"
        "  title, language, deadline\n"
        "  - title = the role being advertised.\n"
        "  - language = ISO-639-1 code of the posting language (en, de, …).\n"
        "  - deadline = the application deadline as an ISO date (YYYY-MM-DD), if stated.\n"
        "Omit a line entirely if the posting does not state that field — never guess.\n"
        "No prose, no markdown, no JSON."
    )
    _MAX_POST_CHARS = 12000
    _PLACEHOLDERS = {"none", "n/a", "na", "-", "—", "unknown", "null"}
    # `<field>: <value>` or `<field> - <value>`; value required (blank values are dropped).
    _LINE = re.compile(r"^\s*([a-zA-Z_]+)\s*[:\-]\s*(.+?)\s*$")

    def __init__(self, job_post_text: str, *, alias: str = "default", user=None):
        self.job_post_text = job_post_text
        self.alias = alias
        self.user = user

    def extract(self) -> dict:
        """Return {field: value} for the fields the posting states. {} on any failure."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("AddressExtract: LLM call failed")
            return {}
        return self._parse(raw)

    def _prompt(self) -> str:
        post = self.job_post_text[: self._MAX_POST_CHARS]
        return f"{self._INSTRUCTION}\n\nJOB POSTING:\n{post}\n\nFIELDS:"

    def _parse(self, raw: str) -> dict:
        allowed = set(self._FIELDS)
        out: dict[str, str] = {}
        for line in (raw or "").splitlines():
            m = self._LINE.match(line)
            if not m:
                continue
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            if key in allowed and val and val.lower() not in self._PLACEHOLDERS:
                out[key] = val[:200]
        return out


class AddressSearch(AddressExtract):
    """Find the employer's postal address ONLINE with a web-search-capable model — the
    fallback when the posting itself states none. Inherits AddressExtract's line-format
    parsing (`<field>: <value>`, placeholders dropped — see `no-json-llm-io`); differs in
    transport (web_search, not complete) and in returning the cited sources so the user
    can double-check before a letter goes out.
    """

    _FIELDS = (
        "company",
        "street",
        "address_line2",
        "zip",
        "city",
        "country",
        "email",
        "phone",
    )
    _INSTRUCTION = (
        "Find the postal address of the EMPLOYER named below (their headquarters, or the\n"
        "office the role context points to) using web search. Output one\n"
        "'<field>: <value>' per line, using exactly these field names:\n"
        "  company, street, address_line2, zip, city, country, email, phone\n"
        "Omit a line entirely if you cannot find that field online — never guess.\n"
        "No prose, no markdown, no JSON."
    )
    _MAX_CONTEXT_CHARS = 600

    def __init__(
        self, company: str, posting_text: str, *, alias: str = "default", user=None
    ):
        super().__init__(posting_text, alias=alias, user=user)
        self.company = (company or "").strip()

    def search(self) -> dict:
        """{"ok": bool, "address": {field: value}, "sources": [url]}. Not capable /
        nothing found / any failure -> ok False with empties — never raises."""
        if not can_web_search(self.alias, self.user):
            logger.info("AddressSearch: alias %s has no web search", self.alias)
            return {"ok": False, "address": {}, "sources": []}
        try:
            res = web_search(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("AddressSearch: web search failed")
            return {"ok": False, "address": {}, "sources": []}
        address = self._parse(res.get("text") or "")
        return {
            "ok": bool(address),
            "address": address,
            "sources": res.get("sources", []),
        }

    def _prompt(self) -> str:
        company = self.company or "the employer in the role context"
        ctx = self.job_post_text[: self._MAX_CONTEXT_CHARS]
        return (
            f"{self._INSTRUCTION}\n\nCOMPANY: {company}\n\n"
            f"(role context, do not quote): {ctx}\n\nFIELDS:"
        )


class CoverLetterWriter:
    """Turn the embedding-selected `ResumeSnippet`s into cover-letter body prose.

    Snippet content is the only permitted source of facts about the candidate, at every
    grade. What varies is the writer's licence:
      - light: glue — join the snippets, minimal connective tissue (all a 1B model can do);
      - standard: polish — rework the snippets into good prose, claims preserved;
      - strong: compose — write an original letter; UNIQUELY, it sees the job posting
        (`posting_text`) to tailor emphasis/tone/ordering. The posting is the classic
        fabrication vector, which is why below strong it is never included, and why the
        caller runs a mandatory grounding audit (+ one repair pass) on strong output.

    `unsupported_claims` and `revision_notes` are the repair-pass channels: the
    grounding audit's findings and the LetterCritic's writing notes are fed back so
    one rewrite can fix both. `opening_paragraph` is the personal paragraph that will
    sit above this body — context for the arc, never a source of candidate facts.

    Output is free prose (the body), the one place structured line-format I/O does not
    apply. Any failure -> '' so the caller falls back to the raw stitched snippets.
    """

    # One-page body window, shared by the standard/strong clauses: standard drifted
    # short ("combined snippet length" shrinks with thin snippets), strong drifted
    # long (no bound at all) — an explicit word window evens the grades out.
    _TARGET_WORDS = (200, 280)

    _GRADE_CLAUSE = {
        "light": (
            "Join the snippets into one letter body. Keep their wording where you can; add only "
            "minimal connective phrases so it reads as one piece. Do not rewrite or embellish."
        ),
        "standard": (
            "Rework the snippets into a polished, cohesive letter body. This is a full "
            "letter, not a summary: aim for roughly {lo}-{hi} words, keep every concrete "
            "claim, and write one paragraph per theme with real transitions — write the "
            "themes out; never compress the substance away. If the snippets hold less "
            "material than that, use everything they state and stop — never pad or "
            "invent to reach the length. Close with a brief final paragraph: a "
            "call to action and genuine thanks for the consideration. Do not invent "
            "facts the snippets do not state."
        ),
        "strong": (
            "Compose an original, persuasive letter body tailored to THIS job posting. Use the "
            "posting only to choose emphasis, ordering, and tone — the posting is NEVER a "
            "source of facts about the candidate. Every factual claim — skills, employers, "
            "titles, numbers, dates, achievements, and any other checkable fact — must come "
            "from the snippets; invent nothing. State each experience, project, and "
            "achievement at most once — never "
            "retell the same fact in different words. Aim for roughly {lo}-{hi} words — "
            "the finished letter must fit one page; prefer dropping the weakest material "
            "over compressing everything. Close with a brief final paragraph: "
            "a call to action and genuine thanks for the consideration."
        ),
    }
    _COMMON = (
        "Write ONLY the body paragraphs of a cover letter — no date, no addresses, no subject "
        "line, no salutation, no sign-off, no markdown, no placeholders. Write in {language}. "
        "Use ONLY facts stated in the snippets below; do not add skills, employers, job titles, "
        "numbers, dates, achievements, or any other factual claim the snippets do not state."
    )

    def __init__(
        self,
        snippets: list,
        *,
        candidate_name: str = "",
        title: str = "",
        language: str = "en",
        grade: str = "standard",
        alias: str = "default",
        user=None,
        posting_text: str = "",
        unsupported_claims: list[str] | None = None,
        revision_notes: list[str] | None = None,
        opening_paragraph: str = "",
    ):
        self.snippets = snippets
        self.candidate_name = candidate_name
        self.title = title
        self.language = language
        self.grade = grade
        self.alias = alias
        self.user = user
        self.posting_text = posting_text
        self.unsupported_claims = unsupported_claims or []
        self.revision_notes = revision_notes or []
        self.opening_paragraph = opening_paragraph

    def write(self) -> str:
        """Return the woven body prose. '' when there are no snippets or the LLM fails."""
        if not self.snippets:
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("CoverLetterWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        clause = self._GRADE_CLAUSE.get(self.grade, self._GRADE_CLAUSE["standard"])
        lo, hi = self._TARGET_WORDS
        clause = clause.format(lo=lo, hi=hi)
        common = self._COMMON.format(language=_language_name(self.language))
        blocks = "\n\n".join(
            f"[{s.get_kind_display()}] {s.title}\n{s.content}" for s in self.snippets
        )
        posting = ""
        if self.grade == "strong" and self.posting_text:
            posting = f"JOB POSTING (context only, never a source of facts):\n{self.posting_text}\n\n"
        opening = ""
        if self.opening_paragraph:
            opening = (
                "OPENING PARAGRAPH (already written; it appears directly above your "
                "text): context only, never a source of facts about the candidate, and "
                "do not repeat it — but you may echo its theme in one clause of your "
                "closing to tie the letter together.\n"
                f"{self.opening_paragraph}\n\n"
            )
        repair = ""
        if self.unsupported_claims:
            claims = "\n".join(f"- {c}" for c in self.unsupported_claims)
            repair = (
                "A previous draft contained these unsupported claims — remove them or "
                f"replace them with claims the snippets actually state:\n{claims}\n\n"
            )
        notes = ""
        if self.revision_notes:
            flagged = "\n".join(f"- {n}" for n in self.revision_notes)
            notes = (
                "A reviewer flagged these writing problems in the previous draft — fix "
                f"them without inventing new facts:\n{flagged}\n\n"
            )
        return (
            f"{clause}\n{common}\n\n"
            f"CANDIDATE: {self.candidate_name}\n"
            f"ROLE: {self.title}\n\n"
            f"{opening}{posting}{repair}{notes}"
            f"SNIPPETS (your only source of facts):\n{blocks}\n\nLETTER BODY:"
        )


class FaithfulnessCheck:
    """Grounding auditor for a generated cover-letter body: a fixed strong LLM reads the body plus
    the candidate's authored snippets (the ONLY permitted source of fact) and lists every claim in
    the body the snippets do not support.

    `ai_share` measures PROVENANCE (how much prose the machine produced); this measures
    FAITHFULNESS (did the machine assert something untrue) — an orthogonal axis, which is why a 5%
    `ai_share` letter can still hallucinate. The job posting is deliberately NOT given: a
    requirement appearing in a posting must never be treated as a fact about the candidate.

    Provider-agnostic. Line-format I/O (never JSON — see the `no-json-llm-io` memory): the reply's
    'UNSUPPORTED <n>' line anchors the count; each following bullet line is one claim. On ANY
    failure it returns count=None ('not checked'), NEVER 0 — a failed audit must not be mistaken for
    a clean letter (the false-assurance trap this check exists to close).
    """

    # Audits prefer a mid-tier model: fact-checking against given sources is cheaper
    # work than composing, and it keeps a strong run's checks off the expensive model.
    # (A 1B writer still can't fact-check itself — the pin, or verifier_alias, must
    # point at something at least standard.)
    PREFERRED_GRADE: str | None = "standard"

    _INSTRUCTION = (
        "You are fact-checking a COVER LETTER BODY against the candidate's authored SNIPPETS.\n"
        "The snippets are the ONLY permitted source of factual claims — skills, employers, job "
        "titles, numbers, dates, achievements, and any other checkable fact about the candidate. "
        "A claim is UNSUPPORTED if the snippets do not state or clearly imply it.\n"
        "List every unsupported factual claim in the letter body.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'UNSUPPORTED <n>' — the number of unsupported claims (0 if none);\n"
        "  - then ONE line per claim, '- <claim, quoted or paraphrased>' (<=20 words), worst "
        "first;\n"
        "  - if every claim is grounded, write 'UNSUPPORTED 0' and nothing else.\n"
        "Do not flag style, tone, opinion, or first-person framing — only checkable facts. "
        "No prose, no markdown headers, no JSON."
    )

    _COUNT_RE = re.compile(r"\bUNSUPPORTED\s+(\d+)\b", re.IGNORECASE)
    # a claim line: an optional bullet / number marker, then the claim text.
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, body: str, snippets: list, user=None, alias: str = "default"):
        self.body = body
        self.snippets = snippets
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        """Return {'count': int | None, 'claims': [str]}.

        count=None  -> audit failed / unreadable (surface as 'not checked', NOT clean).
        count=0     -> audited and fully grounded.
        count>0     -> that many claims the snippets do not support, worst first.
        """
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("FaithfulnessCheck: LLM call failed")
            return {"count": None, "claims": []}
        return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)

    def _prompt(self) -> str:
        blocks = (
            "\n\n".join(
                f"[{s.get_kind_display()}] {s.title}\n{s.content}"
                for s in self.snippets
            )
            or "(no snippets)"
        )
        return (
            f"{self._INSTRUCTION}\n\n"
            f"SNIPPETS (the only source of truth):\n{blocks}\n\n"
            f"LETTER BODY:\n{self.body}\n\n"
            f"AUDIT:"
        )


class LetterCritic:
    """Prose-quality reviewer for a generated cover-letter body: reads the snippets and
    the body and flags WRITING problems — redundancy, lost substance, compression, flow.

    Advisory, not safety: findings feed ONE repair rewrite (CoverLetterWriter's
    `revision_notes`), and on any failure the caller just skips the repair —
    count=None is never surfaced as "unchecked" the way the grounding audit's is,
    because no safety claim is being made. Faithfulness stays FaithfulnessCheck's job;
    this rung is told not to fact-check. Same line format, same shared parser, same
    honesty rule (listed lines win, unreadable -> None) — see the `no-json-llm-io`
    memory.
    """

    PREFERRED_GRADE: str | None = (
        "standard"  # review, not composition — mid tier is enough
    )

    _INSTRUCTION = (
        "You are reviewing the BODY of a job-application cover letter that was written "
        "from the candidate's authored SNIPPETS.\n"
        "Flag WRITING-QUALITY problems only:\n"
        "  - redundancy: the same experience, achievement, or fact told more than once;\n"
        "  - lost substance: a concrete snippet claim (skill, employer, number, "
        "achievement) missing from the body;\n"
        "  - compression: the body reads like a summary of the snippets instead of a "
        "full letter;\n"
        "  - flow: abrupt jumps, paragraphs without connective tissue.\n"
        "Do NOT fact-check (a separate audit does that) and do NOT flag tone or opinion.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'ISSUES <n>' — the number of problems (0 if none);\n"
        "  - then ONE line per problem, '- <issue>' (<=20 words), worst first;\n"
        "  - if the body is sound, write 'ISSUES 0' and nothing else.\n"
        "No prose, no markdown headers, no JSON."
    )

    _COUNT_RE = re.compile(r"\bISSUES\s+(\d+)\b", re.IGNORECASE)
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, body: str, snippets: list, user=None, alias: str = "default"):
        self.body = body
        self.snippets = snippets
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        """Return {'count': int | None, 'claims': [str]}. None = critic unavailable —
        the caller skips the repair, nothing more."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("LetterCritic: LLM call failed")
            return {"count": None, "claims": []}
        return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)

    def _prompt(self) -> str:
        blocks = (
            "\n\n".join(
                f"[{s.get_kind_display()}] {s.title}\n{s.content}"
                for s in self.snippets
            )
            or "(no snippets)"
        )
        return (
            f"{self._INSTRUCTION}\n\n"
            f"SNIPPETS (what the body was written from):\n{blocks}\n\n"
            f"LETTER BODY:\n{self.body}\n\n"
            f"REVIEW:"
        )


class PersonalParagraphWriter:
    """Write the OPENING paragraph of a cover letter: why the candidate is a genuine fit
    for THIS company.

    Sources: the company research dossier (company facts) + the personality dossier (who they are).
    The job posting is only light role context. Every company fact must come from RESEARCH, every
    trait from PERSONALITY — invent nothing, but render each trait's employer-facing side (the
    dossier stays honest; the letter shows the strength it implies). Free prose; any failure -> ''
    (caller omits it).
    """

    _INSTRUCTION = (
        "Write ONE short paragraph (3-5 sentences) that OPENS a cover letter: why this "
        "candidate is personally drawn to and a strong fit for THIS company. Connect a "
        "specific thing about the company (from RESEARCH) to who the candidate is (from "
        "PERSONALITY). Use ONLY facts from RESEARCH for company claims and ONLY traits "
        "from PERSONALITY for the candidate — invent nothing, add no skills/employers/"
        "numbers. Every trait is one side of a coin: render each PERSONALITY trait as "
        "the professional strength it implies, never worded so it could read as a "
        "liability. (That move, illustrated — NOT this candidate's traits: 'values "
        "autonomy' would become 'works independently'.) First person, genuine, "
        "not fawning. End on a short bridge that leads naturally into the "
        "qualifications below. No salutation, no sign-off, no markdown, no headers — "
        "just the paragraph."
    )

    def __init__(
        self,
        *,
        posting_text="",
        title="",
        language="en",
        company_dossier="",
        personality_dossier="",
        alias="default",
        user=None,
    ):
        self.posting_text = posting_text
        self.title = title
        self.language = language
        self.company_dossier = company_dossier
        self.personality_dossier = personality_dossier
        self.alias = alias
        self.user = user

    def write(self) -> str:
        if not self.company_dossier or not self.personality_dossier:
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("PersonalParagraphWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\nWrite in {_language_name(self.language)}.\n\n"
            f"ROLE: {self.title}\n\n"
            f"RESEARCH (company facts — the only source for company claims):\n{self.company_dossier}\n\n"
            f"PERSONALITY (the candidate — the only source for who they are):\n{self.personality_dossier}\n\n"
            f"PARAGRAPH:"
        )


class ParagraphGroundingCheck:
    """Faithfulness audit for the personal paragraph. Mirrors FaithfulnessCheck, but the source of
    truth is RESEARCH + PERSONALITY (never snippets, never the posting). Same line format and the
    same honesty rule: count=None on any audit failure, never 0."""

    PREFERRED_GRADE: str | None = (
        "standard"  # audit, not composition — mid tier is enough
    )

    _INSTRUCTION = (
        "You are fact-checking a cover-letter PARAGRAPH against two sources: RESEARCH (company facts) "
        "and PERSONALITY (the candidate). A claim is UNSUPPORTED if neither source states or clearly "
        "implies it. A PERSONALITY trait rendered in a positive professional light is supported by "
        "the underlying trait — reframing is not fabrication. List every unsupported factual claim.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'UNSUPPORTED <n>';\n"
        "  - then ONE line per claim, '- <claim>' (<=20 words), worst first;\n"
        "  - if all grounded, write 'UNSUPPORTED 0' and nothing else.\n"
        "Do not flag tone, opinion, or first-person framing — only checkable facts. No prose, no JSON."
    )
    _COUNT_RE = re.compile(r"\bUNSUPPORTED\s+(\d+)\b", re.IGNORECASE)
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(
        self,
        paragraph,
        company_dossier,
        personality_dossier,
        user=None,
        alias="default",
    ):
        self.paragraph = paragraph
        self.company_dossier = company_dossier
        self.personality_dossier = personality_dossier
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("ParagraphGroundingCheck: LLM call failed")
            return {"count": None, "claims": []}
        return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\n\n"
            f"RESEARCH:\n{self.company_dossier or '(none)'}\n\n"
            f"PERSONALITY:\n{self.personality_dossier or '(none)'}\n\n"
            f"PARAGRAPH:\n{self.paragraph}\n\nAUDIT:"
        )


class LetterChat:
    """Ephemeral letter-refinement chat for the SPA — nothing persisted server-side.

    Single-prompt rendering: every rung in this app is one-shot (`complete(prompt=…)`),
    and the adapters have never been exercised with provider-native multi-turn messages,
    so the transcript is rendered as USER:/ASSISTANT: lines instead. Same fabrication
    rule as the writer: facts about the candidate come from the letter and the
    conversation; the posting is tone/emphasis context only.

    A proposed replacement for the whole letter body arrives after a line-anchored
    'REVISED BODY:' marker (same-line content accepted; never JSON — see the
    no-json-llm-io memory). `reply()` -> {"reply": str, "revision": str | None}; any LLM
    failure -> both empty so the view can 502.
    """

    _INSTRUCTION = (
        "You are helping the candidate refine the body of their job-application cover "
        "letter over chat. NEVER invent facts about the candidate — skills, employers, "
        "job titles, numbers, dates, and achievements must come from the letter or the "
        "conversation. The job posting is context only, for tone and emphasis — never a "
        "source of facts about the candidate. Answer the last USER message concisely, "
        "in {language}. If — and only if — you are proposing a complete replacement for "
        "the letter body, end your reply with a line that is exactly 'REVISED BODY:' "
        "followed by the full new body — plain prose, no markdown, no placeholders."
    )
    # The view 400s above these — a chat turn is a conversation, not a document dump.
    _MAX_TRANSCRIPT_CHARS = 6000
    _MAX_BODY_CHARS = 8000
    _REVISION_RE = re.compile(r"^[ \t]*REVISED BODY:[ \t]*\n?", re.MULTILINE)

    def __init__(
        self,
        body: str,
        transcript: list[dict],
        *,
        posting_text: str = "",
        language: str = "en",
        alias: str = "default",
        user=None,
    ):
        self.body = body
        self.transcript = transcript
        self.posting_text = posting_text
        self.language = language
        self.alias = alias
        self.user = user

    def reply(self) -> dict:
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("LetterChat: LLM call failed")
            return {"reply": "", "revision": None}
        return self._parse(raw or "")

    def _parse(self, raw: str) -> dict:
        parts = self._REVISION_RE.split(raw, maxsplit=1)
        if len(parts) == 2:
            return {"reply": parts[0].strip(), "revision": parts[1].strip() or None}
        return {"reply": raw.strip(), "revision": None}

    def _prompt(self) -> str:
        convo = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in self.transcript
        )
        return (
            f"{self._INSTRUCTION.format(language=_language_name(self.language))}\n\n"
            f"JOB POSTING (context only):\n{self.posting_text}\n\n"
            f"CURRENT LETTER BODY:\n{self.body}\n\n"
            f"CONVERSATION:\n{convo}\n\nASSISTANT:"
        )


class ParagraphRewrite:
    """Rewrite ONE user-selected passage of a cover-letter body on demand.

    The SPA's letter editor sends the passage plus an optional instruction ("shorter", "more
    formal", …). The passage is authoritative — the model rephrases, it does not add facts
    (same fabrication rule as CoverLetterWriter, and the posting is again NOT given).
    Free prose out; any failure -> '' so the caller keeps the original text.
    """

    _INSTRUCTION = (
        "Rewrite the passage below from a cover letter. Keep the meaning and every factual "
        "claim — do not add skills, employers, job titles, numbers, dates, achievements, or "
        "any other factual claim the passage does not state. Keep roughly the same length "
        "unless the request says "
        "otherwise. Write in {language}. Output ONLY the rewritten passage — no quotes, no "
        "markdown, no commentary."
    )
    _MAX_CHARS = 4000  # a passage, not a document — the view 400s above this

    def __init__(
        self,
        passage: str,
        *,
        instruction: str = "",
        language: str = "en",
        alias: str = "default",
        user=None,
    ):
        self.passage = passage
        self.instruction = instruction
        self.language = language
        self.alias = alias
        self.user = user

    def rewrite(self) -> str:
        """Return the rewritten passage. '' on blank input or any LLM failure."""
        if not self.passage.strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("ParagraphRewrite: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        req = f"REQUEST: {self.instruction}\n\n" if self.instruction else ""
        return (
            f"{self._INSTRUCTION.format(language=_language_name(self.language))}\n\n"
            f"{req}PASSAGE:\n{self.passage}\n\nREWRITTEN:"
        )
