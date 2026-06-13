"""JAC-specific LLM prompt wrappers. All calls go through llm_connector.

The wire format is deliberately **line-oriented plain text, never JSON**. Small
local models mangle JSON envelopes (trailing commas, stray braces, unterminated
strings); a line protocol can't hit those failure modes. Each output row is
self-contained and id-first, so a mangled row is independently skippable and a
garbled reason never costs us the pick. The pipeline only ever needs the entry's
opaque `type:pk` id echoed back — it re-hydrates everything else from the DB — so
there is nothing JSON-shaped to carry.
"""

import re

from llm_connector import complete


# A line that is just a code fence (``` or ```lang) — models sometimes wrap
# plain-text output in one. Stripped wherever it appears, not only at the edges.
_FENCE_LINE = re.compile(r"^```[A-Za-z0-9]*$")
# Leading bullet / numbered-list marker on a line.
_LIST_MARKER = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def _clean_lines(raw: str) -> list[str]:
    """Split a raw completion into non-empty, stripped lines, dropping fence lines.
    No other normalisation — callers handle markers / delimiters themselves."""
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or _FENCE_LINE.match(s):
            continue
        out.append(s)
    return out


def _strip_marker(line: str) -> str:
    """Drop a leading `- ` / `* ` / `1. ` list marker, if any."""
    return _LIST_MARKER.sub("", line).strip()


def _parse_keyword_lines(raw: str) -> list[str]:
    """One keyword per line → ordered, de-duplicated list. Surface form preserved
    (downstream does substring matching, so casing/affixes matter)."""
    seen: set[str] = set()
    out: list[str] = []
    for line in _clean_lines(raw):
        kw = _strip_marker(line)
        if kw and kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def _parse_scored_lines(raw: str) -> list[dict]:
    """`id | score | reason` rows → [{"id", "score", "reason"}, ...].

    id-first and tolerant: a row with a junk/absent score still yields the pick
    (score defaults 0.0), and a reason may itself contain `|`. Rows with no `|`
    or an empty id are skipped — that drops any stray prose the model emits."""
    out: list[dict] = []
    for line in _clean_lines(raw):
        line = _strip_marker(line)
        if "|" not in line:
            continue
        parts = line.split("|", 2)
        sid = parts[0].strip()
        if not sid:
            continue
        try:
            score = float(parts[1].strip())
        except (IndexError, ValueError):
            score = 0.0
        reason = parts[2].strip() if len(parts) > 2 else ""
        out.append({"id": sid, "score": score, "reason": reason})
    return out


def _parse_selection_lines(raw: str) -> list[dict]:
    """`id | reason` rows → [{"id", "reason"}, ...] in order. A bare id with no
    `|` is accepted (reason ""); rows with an empty id are skipped."""
    out: list[dict] = []
    for line in _clean_lines(raw):
        line = _strip_marker(line)
        parts = line.split("|", 1)
        sid = parts[0].strip()
        if not sid:
            continue
        reason = parts[1].strip() if len(parts) > 1 else ""
        out.append({"id": sid, "reason": reason})
    return out


# analyze_job's labeled-block contract. Header keyword -> result key.
_ANALYSIS_HEADERS = {
    "ROLE": "role_title",
    "SENIORITY": "seniority",
    "MUST_HAVE": "must_have",
    "NICE_TO_HAVE": "nice_to_have",
    "DOMAINS": "domains",
    "SOFT_SKILLS": "soft_skills",
    "COMPANY_SIGNALS": "company_signals",
    "SUMMARY": "summary",
}
_ANALYSIS_SCALARS = {"role_title", "seniority", "summary"}


def _empty_analysis() -> dict:
    return {
        "role_title": "",
        "seniority": "",
        "must_have": [],
        "nice_to_have": [],
        "domains": [],
        "soft_skills": [],
        "company_signals": [],
        "summary": "",
    }


def _parse_analysis_block(raw: str) -> dict:
    """Parse the analyze_job labeled block into a dict. Tolerant: unknown lines
    are ignored, every key is always present (scalars '', lists []). List headers
    accept inline comma items and/or following `- item` bullet lines."""
    result = _empty_analysis()
    current_list: str | None = None
    for line in _clean_lines(raw):
        header, sep, rest = line.partition(":")
        key = (
            _ANALYSIS_HEADERS.get(header.strip().upper().replace(" ", "_"))
            if sep
            else None
        )
        if key:
            if key in _ANALYSIS_SCALARS:
                result[key] = rest.strip()
                current_list = None
            else:
                current_list = key
                inline = rest.strip()
                if inline:
                    result[key].extend(
                        p.strip() for p in inline.split(",") if p.strip()
                    )
            continue
        if current_list:
            item = _strip_marker(line)
            if item:
                result[current_list].append(item)
    return result


def _entries_block(entries: list[dict]) -> str:
    """Render entries as compact `id | text` lines (not JSON): ~30-40% fewer
    tokens and easier for small models to read. The id leads each line so the
    model can echo it verbatim in its output."""
    return "\n".join(f"{e.get('id')} | {e.get('text', '')}" for e in entries)


def _analysis_block(analysis: dict) -> str:
    """Render an analysis dict as the labeled block — the inverse of
    _parse_analysis_block — so we feed the next prompt plain text, not JSON."""
    lines = [
        f"ROLE: {analysis.get('role_title', '')}",
        f"SENIORITY: {analysis.get('seniority', '')}",
    ]
    for key, header in (
        ("must_have", "MUST_HAVE"),
        ("nice_to_have", "NICE_TO_HAVE"),
        ("domains", "DOMAINS"),
        ("soft_skills", "SOFT_SKILLS"),
        ("company_signals", "COMPANY_SIGNALS"),
    ):
        lines.append(f"{header}:")
        for item in analysis.get(key, []) or []:
            lines.append(f"- {item}")
    lines.append(f"SUMMARY: {analysis.get('summary', '')}")
    return "\n".join(lines)


# ---------- Non-agentic primitives (single LLM call each) ----------


def extract_job_keywords(job_text: str, llm: str = "default", user=None) -> list[str]:
    """Free-form keyword extraction from a job posting. SLM-friendly.

    Keywords are returned in the SAME LANGUAGE as the posting so they can be
    used directly as needles for substring matching against a CV.
    """
    system = (
        "You extract a broad set of keywords from a job posting for substring "
        "matching against a CV. Return them ONE PER LINE.\n"
        "\n"
        "RULES:\n"
        "1. LANGUAGE: Return keywords in the SAME LANGUAGE as the posting. "
        "Do NOT translate. If the posting is in German, return German tokens. "
        "If English, English tokens. Preserve the surface form of the posting "
        "exactly — the downstream consumer does substring matching, so "
        "'Serveradministration' and 'server administration' are NOT "
        "interchangeable.\n"
        "2. COUNT: Return 25–50 keywords. Err on the side of more rather than "
        "fewer. Single words and short multi-word phrases (2–3 words max) only.\n"
        "3. COVERAGE: Include keywords from ALL of these categories. Postings "
        "come from every kind of role — technical, creative, administrative, "
        "operational, care/teaching, customer-facing, trades — so adapt the "
        "specific terms to whatever this posting actually is:\n"
        "   - Hard skills, tools, methods, certifications (software, hardware, "
        "protocols, frameworks, design tools, office suites, machinery, "
        "licences, qualifications).\n"
        "   - Role concepts and job-title fragments (e.g. 'engineer', "
        "'developer', 'designer', 'copywriter', 'editor', 'assistant', "
        "'coordinator', 'manager', 'analyst', 'Techniker', 'Sachbearbeiter', "
        "'Erzieher', 'Pfleger').\n"
        "   - Domain and industry terms (e.g. 'data center', 'healthcare', "
        "'finance', 'publishing', 'fashion', 'logistics', 'education').\n"
        "   - Adjacent / transferable terms — broader categories the posting "
        "implies even if not stated verbatim. Examples across role families:\n"
        "       * data-center field tech: also include 'Server', 'Hardware', "
        "'Netzwerk', 'Verkabelung', 'IT-Support', 'Linux'.\n"
        "       * graphic designer: also include 'Typography', 'Branding', "
        "'Layout', 'Adobe', 'Print', 'Web'.\n"
        "       * executive assistant: also include 'Calendar', 'Travel', "
        "'Stakeholder', 'Coordination', 'Vertraulichkeit'.\n"
        "       * sales / customer-success: also include 'Pipeline', 'CRM', "
        "'Account', 'Onboarding', 'Renewal'.\n"
        "4. NO duplicates, no stopwords, no sentences, no prose, no markdown, "
        "no numbering, no bullets, no JSON.\n"
        "\n"
        "Output: one keyword or short phrase per line. Nothing else."
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": job_text},
        ],
        alias=llm,
        user=user,
    )
    return _parse_keyword_lines(raw)


def score_entries_for_job(
    job_text: str,
    entries: list[dict],
    llm: str = "default",
    user=None,
) -> list[dict]:
    """Score each entry's relevance to the job.

    entries: [{"id": <opaque>, "type": "skill"|"job"|..., "text": "<summary>"}, ...]
    returns: [{"id": ..., "score": float, "reason": str}, ...]
    """
    system = (
        "You score CV entries by relevance to a job posting.\n"
        "\n"
        "OUTPUT FORMAT: one line per entry, exactly:\n"
        "    id | score | reason\n"
        "where id is the entry id echoed VERBATIM, score is a float 0.0–1.0, "
        "and reason is one short sentence. Separate the three fields with ' | '. "
        "One entry per line, same order as the input. No header line, no JSON, "
        "no markdown, no bullets, no blank lines.\n"
        "\n"
        "SCORING RUBRIC (use the full range — do not cluster at the bottom). "
        "The role may be technical, creative, administrative, operational, "
        "educational, care, sales, or anything else — apply the bands to "
        "whatever this posting actually is:\n"
        "  0.8–1.0  Direct match — entry names a skill/tool/domain/role from "
        "the posting.\n"
        "  0.5–0.7  Strong transferable match — adjacent skill, adjacent "
        "domain, or comparable hands-on experience. Examples across role "
        "families: server admin → data-center hardware; freelance graphic "
        "design → in-house designer role; executive assistant → office "
        "manager; retail shift lead → customer-facing team lead; physics lab "
        "→ any hands-on technical/diagnostic role; Python dev → any "
        "scripting/automation role.\n"
        "  0.3–0.4  Tangential but shows base professional competence "
        "relevant to the field at large.\n"
        "  0.0–0.2  Unrelated.\n"
        "\n"
        "Score on what the entry DEMONSTRATES, not just literal token overlap. "
        "Real hands-on experience in an adjacent domain — technical, "
        "creative, administrative, or operational — is genuinely transferable "
        "and belongs in the 0.5–0.7 band. The posting and CV may be in "
        "different languages — match on meaning, not surface tokens.\n"
        "\n"
        "Return one `id | score | reason` line per input entry, in the same "
        "order. No prose around the lines, no JSON, no markdown."
    )
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"CV ENTRIES (one per line, `id | summary`):\n{_entries_block(entries)}"
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        alias=llm,
        user=user,
    )
    return _parse_scored_lines(raw)


def analyze_job(job_text: str, llm: str = "default", user=None) -> dict:
    """Structured analysis of a job posting."""
    system = (
        "You analyze job postings. Return a LABELED PLAIN-TEXT BLOCK with these "
        "lines and nothing else (no JSON, no markdown):\n"
        "ROLE: <role title>\n"
        "SENIORITY: <one of: junior, mid, senior, lead, principal>\n"
        "MUST_HAVE:\n"
        "- <hard requirement>\n"
        "(one '- item' line per hard requirement)\n"
        "NICE_TO_HAVE:\n"
        "- <nice to have>\n"
        "DOMAINS:\n"
        "- <industry/business domain>\n"
        "SOFT_SKILLS:\n"
        "- <soft skill>\n"
        "COMPANY_SIGNALS:\n"
        "- <culture/values hint>\n"
        "SUMMARY: <2-3 sentences>\n"
        "\n"
        "Keep the header keywords exactly as shown. Put each list item on its "
        "own '- ' line. No JSON, no markdown fences, no extra commentary."
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": job_text},
        ],
        alias=llm,
        user=user,
    )
    return _parse_analysis_block(raw)


def score_entries_with_analysis(
    job_text: str,
    analysis: dict,
    entries: list[dict],
    llm: str = "default",
    user=None,
) -> list[dict]:
    """Score entries against an already-extracted job analysis. Reasoning-model friendly."""
    system = (
        "You score CV entries by relevance to a job. You are given the raw "
        "posting, a structured analysis of it (must-haves, nice-to-haves, "
        "seniority, domains), and a list of CV entries (skills, jobs, "
        "education, certifications, projects, languages).\n"
        "\n"
        "OUTPUT FORMAT: one line per entry, exactly:\n"
        "    id | score | reason\n"
        "where id is the entry id echoed VERBATIM, score is a float 0.0–1.0, "
        "and reason is one sentence citing what the entry contributes — a "
        "requirement it addresses directly OR a transferable signal it "
        "provides. Separate the three fields with ' | '. One entry per line, "
        "same order as the input. No header line, no JSON, no markdown, no "
        "bullets, no blank lines.\n"
        "\n"
        "SCORING RUBRIC (use the full range — do not cluster at the bottom). "
        "The role may be technical, creative, administrative, operational, "
        "educational, care, sales, or anything else — apply the bands to "
        "whatever this posting actually is:\n"
        "  0.8–1.0  Direct match. The entry names or demonstrates a must-have "
        "skill, tool, domain, or role from the posting.\n"
        "  0.5–0.7  Strong transferable match. The entry is not a literal "
        "match but maps cleanly onto a posting requirement via an adjacent "
        "skill, adjacent domain, or directly comparable hands-on experience. "
        "Examples across role families:\n"
        "     - 'Linux server administration' for a data-center hardware role\n"
        "     - 'freelance graphic design' for an in-house designer role\n"
        "     - 'executive assistant' experience for an office-manager opening\n"
        "     - 'retail shift supervisor' for any customer-facing team-lead role\n"
        "     - 'physics lab assistant' for any hands-on technical/diagnostic role\n"
        "     - 'Python backend dev' for a role requiring scripting or automation\n"
        "     - multilingual ability for any client-facing or international role\n"
        "  0.3–0.4  Tangentially related but shows base professional "
        "competence relevant to the field at large (e.g. any relevant degree "
        "for a role in that field; any structured workplace experience for "
        "any junior+ role).\n"
        "  0.0–0.2  Unrelated. The entry has no plausible link to the posting "
        "and would not strengthen the application.\n"
        "\n"
        "GUIDANCE:\n"
        "- Transferable matches (0.5–0.7) are the MOST IMPORTANT band to use "
        "correctly. Do not collapse them down to 0.2 just because the entry "
        "doesn't name the exact term in the posting. Real hands-on experience "
        "in an adjacent domain — technical (lab work, server admin, "
        "scripting, debugging), creative (design, writing, photography, "
        "production), administrative (coordination, scheduling, vendor "
        "handling, document control), operational (logistics, supervision, "
        "client handling), or care/teaching — is genuinely valuable and must "
        "be scored in the 0.5–0.7 band, not lower.\n"
        "- Score on what the entry DEMONSTRATES, not on what it literally "
        "says. A 'Fullstack developer' role implicitly demonstrates "
        "deployment, server config, and troubleshooting; an 'art director' "
        "role implicitly demonstrates team direction, brand stewardship, and "
        "client presentation; an 'office manager' role implicitly "
        "demonstrates vendor management, budgeting, and event coordination — "
        "even when descriptions don't spell those out.\n"
        "- Languages: any language matching the posting's country or stated "
        "language requirement is 0.8+. Other languages are 0.3 (useful "
        "baseline).\n"
        "- Do not penalize entries for being from an older or different "
        "career phase — relevance is judged on content, not recency.\n"
        "- The job posting and CV may be in different languages. Match on "
        "meaning, not surface tokens.\n"
        "\n"
        "Return one `id | score | reason` line per input entry, in the same "
        "order. No prose around the lines, no JSON, no markdown."
    )
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"JOB ANALYSIS:\n{_analysis_block(analysis)}\n\n"
        f"CV ENTRIES (one per line, `id | summary`):\n{_entries_block(entries)}"
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        alias=llm,
        user=user,
    )
    return _parse_scored_lines(raw)


# ---------- Reasoning-grade prompt (loose, selection contract) ----------


def tailor_cv_conversationally(
    job_text: str,
    entries: list[dict],
    llm: str = "default",
    user=None,
) -> list[dict]:
    """Reasoning-model tailoring: loose prompt, selection contract, ranked output.

    Contrast with score_entries_for_job / score_entries_with_analysis: those
    pin the model to a strict 0.0–1.0 rubric and demand an entry per input.
    That helps mid-tier models stay on contract but boxes in a strong model.
    Here we hand over the full career database plus the posting, let the model
    reason, and ask for a ranked recommendation of what belongs on the CV.

    entries: [{"id": <opaque>, "type": "skill"|"job"|..., "text": "<summary>"}, ...]
    returns: [{"id": <echoed verbatim>, "reason": "<one short sentence>"}, ...]
             in descending relevance order (frontmost = strongest recommendation).

    Hallucinated ids (ids not present in the input) are dropped. Raises
    ValueError if fewer than two valid recommendations survive — callers
    treat that as "this model can't handle the task" and fall back.
    """
    system = (
        "You are helping a candidate tailor their CV to a specific job posting. "
        "You will see two things:\n"
        "  1. The job posting.\n"
        "  2. A list of every entry in the candidate's career database "
        "(skills, jobs, education, certifications, projects, languages), one "
        "per line as `id | summary`, each with an opaque id and a short text "
        "summary.\n"
        "\n"
        "Decide which entries actually belong on the CV for THIS posting. "
        "The role may be technical, creative, administrative, operational, "
        "educational, care, sales, or anything else — judge each entry on "
        "what it actually demonstrates relative to this specific posting, "
        "not on surface keywords or how 'professional' it sounds in the "
        "abstract. Direct fits matter, and so do honest transferable signals "
        "— adjacent skills, comparable hands-on experience, relevant domain "
        "exposure. Drop entries that would be noise. Order what you keep so "
        "the most compelling recommendation comes first.\n"
        "\n"
        "OUTPUT FORMAT: one line per entry you keep, exactly:\n"
        "    id | reason\n"
        "where id is the input id echoed VERBATIM and reason is one short "
        "sentence on why this entry strengthens the application. Separate the "
        "two fields with ' | '. Most compelling first. No scores, no JSON, no "
        "markdown, no bullets, no prose around the lines."
    )
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"CAREER DATABASE (one per line, `id | summary`):\n{_entries_block(entries)}"
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_selection_lines(raw)

    valid_ids = {e["id"] for e in entries if isinstance(e, dict) and "id" in e}
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in parsed:
        sid = item.get("id")
        if not isinstance(sid, str) or sid not in valid_ids or sid in seen:
            continue
        seen.add(sid)
        cleaned.append({"id": sid, "reason": str(item.get("reason", ""))})

    if len(cleaned) < 2:
        raise ValueError(
            "tailor_cv_conversationally: fewer than 2 valid recommendations "
            f"survived id validation (got {len(cleaned)} of {len(parsed)} returned)"
        )
    return cleaned
