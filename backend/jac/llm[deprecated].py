"""JAC-specific LLM prompt wrappers. All calls go through llm_connector."""

import json
import re

from llm_connector import complete

_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _parse_json(raw: str, context: str):
    """Strip optional ```json fences and parse. Raise with the raw response on failure."""
    stripped = raw.strip()
    match = _JSON_FENCE.match(stripped)
    if match:
        stripped = match.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{context}: LLM returned invalid JSON. Raw response:\n{raw}"
        ) from exc


# ---------- Non-agentic primitives (single LLM call each) ----------


def extract_job_keywords(job_text: str, llm: str = "default", user=None) -> list[str]:
    """Free-form keyword extraction from a job posting. SLM-friendly.

    Keywords are returned in the SAME LANGUAGE as the posting so they can be
    used directly as needles for substring matching against a CV.
    """
    system = (
        "You extract a broad set of keywords from a job posting for substring "
        "matching against a CV. Return ONE JSON array of short strings.\n"
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
        "4. NO duplicates, no stopwords, no sentences, no prose, no markdown.\n"
        "\n"
        "Output: a single JSON array of strings. Nothing else."
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": job_text},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_json(raw, context="extract_job_keywords")
    if not isinstance(parsed, list):
        raise ValueError(f"extract_job_keywords: expected list, got {type(parsed)}")
    return [str(k) for k in parsed]


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
        "For each entry return: id (echo verbatim), score (float 0.0–1.0), "
        "reason (one short sentence).\n"
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
        "Return a JSON array with one object per input entry, in the same order. "
        "No prose, no markdown."
    )
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"CV ENTRIES (JSON):\n{json.dumps(entries, ensure_ascii=False)}"
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_json(raw, context="score_entries_for_job")
    if not isinstance(parsed, list):
        raise ValueError(f"score_entries_for_job: expected list, got {type(parsed)}")
    return parsed


def analyze_job(job_text: str, llm: str = "default", user=None) -> dict:
    """Structured analysis of a job posting."""
    system = (
        "You analyze job postings. Return a single JSON object with these keys: "
        "role_title (string), seniority (one of: junior, mid, senior, lead, principal), "
        "must_have (array of strings — hard requirements), "
        "nice_to_have (array of strings), "
        "domains (array of strings — industry/business domains), "
        "soft_skills (array of strings), "
        "company_signals (array of strings — culture/values hints), "
        "summary (string, 2-3 sentences). "
        "No prose, no markdown."
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": job_text},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_json(raw, context="analyze_job")
    if not isinstance(parsed, dict):
        raise ValueError(f"analyze_job: expected object, got {type(parsed)}")
    return parsed


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
        "For each entry return: id (echo verbatim), score (float 0.0–1.0), "
        "reason (one sentence citing what the entry contributes — a "
        "requirement it addresses directly OR a transferable signal it "
        "provides).\n"
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
        "Return a JSON array with one object per input entry, in the same "
        "order. No prose, no markdown."
    )
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"JOB ANALYSIS:\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
        f"CV ENTRIES (JSON):\n{json.dumps(entries, ensure_ascii=False)}"
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_json(raw, context="score_entries_with_analysis")
    if not isinstance(parsed, list):
        raise ValueError(
            f"score_entries_with_analysis: expected list, got {type(parsed)}"
        )
    return parsed


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
        "  2. A JSON list of every entry in the candidate's career database "
        "(skills, jobs, education, certifications, projects, languages), each "
        "with an opaque id and a short text summary.\n"
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
        "Return a single JSON array. Each item: "
        '{"id": "<echo the input id verbatim>", '
        '"reason": "<one short sentence on why this entry strengthens the application>"}. '
        "No scores, no prose around the array, no markdown fences."
    )
    user_msg = (
        f"JOB POSTING:\n{job_text}\n\n"
        f"CAREER DATABASE (JSON):\n{json.dumps(entries, ensure_ascii=False)}"
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_json(raw, context="tailor_cv_conversationally")
    if not isinstance(parsed, list):
        raise ValueError(
            f"tailor_cv_conversationally: expected list, got {type(parsed)}"
        )

    valid_ids = {e["id"] for e in entries if isinstance(e, dict) and "id" in e}
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
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
