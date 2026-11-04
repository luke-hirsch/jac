"""German address register: which form of "you" the letter uses, and whether it slipped.

Deliberately regex, not LLM. Detecting `du`/`ihr`/`Sie` in a posting is a lexical question
with an exact answer, and the audit that catches a slipped pronoun has to be cheaper and
more reliable than the model that slipped — otherwise it's just a second coin flip.

Passing the *detected form* into the writer is not the same as passing the posting: it is a
single token of metadata, not a source of facts, so the standard-mode fabrication rule
(the writer never sees the ad) is untouched.
"""

from __future__ import annotations

import re

SIE = "sie"
IHR = "ihr"
DU = "du"
# Every form a letter can carry ("" = none / not German). The editor sends one back with
# rewrite / shorten / chat, and the views 400 on anything else — it lands in a prompt.
ADDRESS_FORMS = (SIE, IHR, DU)

# "Sie"/"Ihnen"/"Ihr"/"Ihre" capitalised mid-sentence is the formal you; lowercase "sie" is
# she/they and is deliberately NOT counted. Case-sensitive on purpose.
_FORMAL = re.compile(r"\b(?:Sie|Ihnen|Ihr|Ihre[mnrs]?)\b")
# At the start of a sentence capitalisation says nothing: "Sie" may be "they", and "Ihr" is
# just as often the plural you ("Ihr sucht …") as the formal your. Those two hits are skipped
# there; "Ihnen"/"Ihre…" keep counting — they don't change meaning by position.
_AMBIGUOUS_AT_START = {"Sie", "Ihr"}
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s*|\n\s*)$")
# How far back it looks: the mark sits right before the word, whitespace aside. Searching the whole
# prefix per hit made a long posting quadratic. `^` still means the text's start, not the window's.
_SENTENCE_START_WINDOW = 64
# "euch"/"euer"/"eure" are unambiguous. Bare "ihr" is not (it is also "her"/"their"), so it
# is left out — a false positive here would flip the whole letter's register. Spelled out
# rather than `eue?re?…` so the currency "EUR" in a salary line can't match.
_PLURAL = re.compile(r"\b(?:euch|euer|eure[mnrs]?|euere[mnrs]?)\b", re.IGNORECASE)
_SINGULAR = re.compile(r"\b(?:du|dich|dir|dein|deine[mnrs]?)\b", re.IGNORECASE)


def _hits(pattern: re.Pattern, text: str) -> list[tuple[int, str]]:
    """(position, word) for every hit, minus the ambiguous sentence-initial formal ones."""
    hits = []
    for m in pattern.finditer(text):
        word = m.group(0)
        if (
            pattern is _FORMAL
            and word in _AMBIGUOUS_AT_START
            and _SENTENCE_START.search(
                text, max(0, m.start() - _SENTENCE_START_WINDOW), m.start()
            )
        ):
            continue
        hits.append((m.start(), word))
    return hits


def detect_address_form(text: str) -> str:
    """The form a German job posting addresses its reader with: "sie" / "ihr" / "du", or ""
    when there is no signal (an English ad, a terse one, a PDF that lost its text layer)."""
    if not text:
        return ""
    counts = {
        SIE: len(_hits(_FORMAL, text)),
        IHR: len(_hits(_PLURAL, text)),
        DU: len(_hits(_SINGULAR, text)),
    }
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] else ""


def resolve_address_form(tone: str, posting_text: str, language: str) -> str:
    """The form the letter should use: the user's explicit tone wins at both extremes, and
    only `neutral` mirrors the company.

        formal   → always "sie"
        neutral  → the posting's "sie"/"ihr" if detectable, else "sie"
        personal → always "ihr" (never "du": a company is not one person)
    """
    if (language or "").lower()[:2] != "de":
        return ""
    if tone == "personal":
        return IHR
    if tone == "formal":
        return SIE
    detected = detect_address_form(posting_text)
    # A posting that says "du" to one applicant still gets "Sie" from a neutral letter —
    # mirroring familiarity is a choice the `personal` tone exists to make.
    return detected if detected in (SIE, IHR) else SIE


def register_leaks(body: str, language: str, form: str) -> list[str]:
    """Pronouns in `body` that contradict `form`, deduped, in order of appearance.

    A flag, never an auto-rewrite: German is full of edge cases (a sentence opening with
    "Sie" meaning "they") and silently rewriting a letter on a regex would be worse than
    the slip.
    """
    if (language or "").lower()[:2] != "de" or not form:
        return []
    wrong = {
        IHR: (_FORMAL, _SINGULAR),
        SIE: (_PLURAL, _SINGULAR),
        DU: (_FORMAL, _PLURAL),
    }.get(form, ())
    hits = sorted(h for pattern in wrong for h in _hits(pattern, body or ""))
    found: list[str] = []
    for _, word in hits:
        if word not in found:
            found.append(word)
    return found
