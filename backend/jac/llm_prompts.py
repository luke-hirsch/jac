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
    """`strong` rung: a conversational LLM selects the CV holistically. It returns an
    ORDERED list of chosen entry ids (priority order, best first) each with a short `why`,
    rather than per-entry scores — CVFilter applies only guardrails (favourites, min_keep)
    on top, so the model's judgment drives the selection.

    Provider-agnostic (no provider-specific kwargs). The reply is a **line format**
    (`<id> — <why>`, one pick per line), not JSON — token-cheap and robust to truncation
    (see the `no-json-llm-io` memory). Any failure returns [] -> CVFilter degrades to the
    standard rung.
    """

    _INSTRUCTION = (
        "You are a senior CV editor tailoring a ONE-PAGE CV to a specific job posting.\n"
        "From the candidate's full entry list below, choose the entries that make the "
        "strongest, most relevant CV for THIS posting and drop the rest. Use judgment:\n"
        "  - prefer entries the posting actually calls for; drop weak or off-topic ones;\n"
        "  - keep a skill if a job/project you are keeping clearly relies on it;\n"
        "  - it is fine to keep few entries for a poorly-matched posting, or many for a "
        "strong match — fit should decide the count, not a fixed quota.\n"
        "Output the entries you are KEEPING, best first, ONE per line: the entry id, "
        "then ' — ', then a short reason (≤12 words).\n"
        "Example:\n"
        "job:2 — leads the relevant backend story\n"
        "skill:7 — required stack\n"
        "Include only ids you are keeping. No prose, no markdown, no other text."
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
        "  - first line: 'GRADE <A-F>' — overall selection quality (A best);\n"
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
        "Open with a 2-3 sentence verdict of the form 'X and Y behave alike; Z is off track; all "
        "models struggle on …', then back it up. Be specific; cite postings, models, and ids."
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
    )
    _INSTRUCTION = (
        "Extract the EMPLOYER's contact details from the job posting below. Output one\n"
        "'<field>: <value>' per line, using exactly these field names:\n"
        "  company, contact_name, street, address_line2, zip, city, country, email, phone,\n"
        "  title, language\n"
        "  - title = the role being advertised.\n"
        "  - language = ISO-639-1 code of the posting language (en, de, …).\n"
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


class CoverLetterWriter:
    """Weave selected `ResumeSnippet`s into cover-letter body prose with the chat model.

    Snippet content is authoritative: the model stitches and smooths, it does not invent facts.
    `grade` tunes only how much rewriting is allowed (light = glue; standard = smooth
    transitions; strong = polished, reordered for impact) — never the content. Output is free
    prose (the body), the one place structured line-format I/O does not apply. Any failure
    -> '' so the caller falls back to the raw stitched snippets.
    """

    _GRADE_CLAUSE = {
        "light": (
            "Join the snippets into one letter body. Keep their wording where you can; add only "
            "minimal connective phrases so it reads as one piece. Do not rewrite or embellish."
        ),
        "standard": (
            "Weave the snippets into a smooth, cohesive letter body. You may lightly rephrase "
            "for flow and transitions, but preserve every concrete claim and the candidate's "
            "voice. Do not invent facts the snippets do not state."
        ),
        "strong": (
            "Compose a polished, persuasive letter body from the snippets, ordered for impact "
            "against THIS posting. Improve prose and transitions freely, but every factual "
            "claim must come from the snippets — invent nothing."
        ),
    }
    _COMMON = (
        "Write ONLY the body paragraphs of a cover letter — no date, no addresses, no subject "
        "line, no salutation, no sign-off, no markdown, no placeholders. Write in {language}."
    )
    _MAX_POST_CHARS = 8000

    def __init__(
        self,
        job_post_text: str,
        snippets: list,
        *,
        candidate_name: str = "",
        title: str = "",
        language: str = "en",
        grade: str = "standard",
        alias: str = "default",
        user=None,
    ):
        self.job_post_text = job_post_text
        self.snippets = snippets
        self.candidate_name = candidate_name
        self.title = title
        self.language = language
        self.grade = grade
        self.alias = alias
        self.user = user

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
        common = self._COMMON.format(language=self.language)
        post = self.job_post_text[: self._MAX_POST_CHARS]
        blocks = "\n\n".join(
            f"[{s.get_kind_display()}] {s.title}\n{s.content}" for s in self.snippets
        )
        return (
            f"{clause}\n{common}\n\n"
            f"CANDIDATE: {self.candidate_name}\n"
            f"ROLE: {self.title}\n\n"
            f"JOB POSTING:\n{post}\n\n"
            f"SNIPPETS (in order, use them all):\n{blocks}\n\nLETTER BODY:"
        )
