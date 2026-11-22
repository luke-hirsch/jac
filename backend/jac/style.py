"""The user's writing rules, and the two jac checks behind them.

The rules are free text, one per line (`PersonalityProfile.style_rules`): every letter writer
follows them, jac's own and Claude's. Two of them jac can also check, each behind its own toggle:
no dash as punctuation, and no "not X but Y" antithesis. Deliberately regex, like jac.register:
the check that catches a slip has to be cheaper and more reliable than the model that slipped.

A finding is a warning, never a rewrite: every hit is quoted, so the user (or Claude) decides.

Standard library only: the skill zip ships this file as `scripts/jac_style.py`, so the skill's
script runs the same checks in file mode, in a sandbox without jac (`jac.skill.skill_bundle`).
The letter editor runs a mirror, frontend/src/lib/style.ts: keep the two in step (their tests
share their cases).
"""

from __future__ import annotations

import re

NO_DASHES = "no_dashes"
NO_ANTITHESIS = "no_antithesis"
# Each one is a toggle on the profile, `style_<name>`.
CHECKS = (NO_DASHES, NO_ANTITHESIS)

_RULE = {
    NO_DASHES: "no em or en dashes as punctuation",
    NO_ANTITHESIS: "no 'not X but Y' antithesis",
}
# The parts of a letter a check reads, as a warning names them.
_PART = {
    "body": "The body",
    "subject": "The subject",
    "salutation": "The salutation",
    "closing": "The closing",
}

EM_DASH = "—"
EN_DASH = "–"
_DASH = re.compile(r"[—–-]")
# Space on the line: a newline ends a clause, it doesn't separate one.
_HSPACE = " \t\u00a0\u202f"  # space, tab, no-break space, narrow no-break space

# Within one sentence, across its clauses. Specific before general: "nicht nur … sondern"
# quotes better than "nicht … sondern", which also matches it. "isn't X, it's Y" needs the
# clause break: without one ("it isn't hard to see why it's a fit") nothing is turned around.
_ANTITHESES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bnicht\s+nur\b.*?\bsondern\b(?:\s+auch\b)?",
        r"\bkein(?:e[mnr]?)?\b.*?\bsondern\b(?:\s+auch\b)?",
        r"\bnicht\b.*?\bsondern\b(?:\s+auch\b)?",
        r"\bnot\s+(?:just|only|merely)\b.*?\bbut\b",
        r"\b(?:isn['’]t|is\s+not|(?:it|this|that)['’]s\s+not)\b.*?(?:[,;:—–]|\s-\s)\s*it['’]s\b",
        r"\bnot\s+an?\b.*?\bbut\s+an?\b",
    )
)

# A full stop, !, ? or … before space ends a sentence, and so does a line break; a full stop
# after an abbreviation, an initial or a day ("z. B.", "bzw.", "e.g.", "J. Smith", "am 1. März")
# doesn't. A year does ("seit 2023."). Splitting too early could hide a hit, too late invent one.
_BREAK = re.compile(r"(?<=[.!?…])\s+|\n+")
_ABBREVIATIONS = frozenset(
    {
        # German
        "abs",
        "bzw",
        "ca",
        "d.h",
        "dipl",
        "dr",
        "evtl",
        "ggf",
        "inkl",
        "ing",
        "nr",
        "o.ä",
        "prof",
        "s.o",
        "s.u",
        "sog",
        "u.a",
        "u.u",
        "vgl",
        "z.b",
        "z.t",
        "zzgl",
        # English
        "approx",
        "e.g",
        "i.e",
        "mr",
        "mrs",
        "ms",
        "no",
        "vs",
    }
)
_FRAGMENT_MAX = 120


def rule_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def style_rules(profile) -> list[str]:
    """The user's rules, one per line. Without a profile, the ones every account starts with."""
    if profile is not None:
        return rule_lines(profile.style_rules)
    # Imported here: this file also runs without Django, in the skill's sandbox.
    from spa.models import DEFAULT_STYLE_RULES

    return rule_lines(DEFAULT_STYLE_RULES)


def style_checks(profile) -> list[str]:
    """The checks the user has on. Without a profile, all of them (the defaults)."""
    return [c for c in CHECKS if getattr(profile, f"style_{c}", True)]


def _is_separator(text: str, i: int) -> bool:
    """Whether the dash at `i` separates clauses. Every em dash does. An en dash does with space
    beside it; a hyphen only as " - ". Neither does in a range (2020–2023, S. 4–6, 10 - 12), a
    compound (Django-App, E-Commerce, Front- und Backend), before a number (–20 %) or as a bullet
    at the start of a line."""
    dash = text[i]
    if dash == EM_DASH:
        return True
    if not text[text.rfind("\n", 0, i) + 1 : i].strip():
        return False  # a bullet; it also means there is a character before the dash
    after = text[i + 1 : i + 2]  # "" at the end of the text
    space_before = text[i - 1] in _HSPACE
    space_after = bool(after) and after in _HSPACE
    if dash == "-":
        if not (space_before and space_after):
            return False
    else:
        if after.isdigit():
            return False  # a minus sign, or the end of a range
        if not (space_before or space_after or after in ("", "\n")):
            return False
    prev = text[:i].rstrip(_HSPACE)[-1:]
    nxt = text[i + 1 :].lstrip(_HSPACE)[:1]
    return not (prev.isdigit() and nxt.isdigit())


def _around(text: str, start: int, end: int, words: int = 3) -> str:
    """The hit with a few words either side, on one line."""
    left, right = text[:start].split(), text[end:].split()
    head = " ".join(left[-words:])
    tail = " ".join(right[:words])
    glue_left = " " if text[start - 1 : start].isspace() else ""
    glue_right = " " if text[end : end + 1].isspace() else ""
    quote = f"{head}{glue_left}{text[start:end]}{glue_right}{tail}".strip()
    if len(left) > words:
        quote = f"…{quote}"
    if len(right) > words:
        quote = f"{quote}…"
    return " ".join(quote.split())


def dash_fragments(text: str) -> list[str]:
    """Every dash used as punctuation, quoted with its neighbours, deduped, in order."""
    found: list[str] = []
    for m in _DASH.finditer(text or ""):
        if _is_separator(text, m.start()):
            quote = _around(text, m.start(), m.end())
            if quote not in found:
                found.append(quote)
    return found


def _ends_sentence(before: str) -> bool:
    """Whether the full stop, !, ? or … that closes `before` ends a sentence."""
    if not before.endswith("."):
        return True
    words = before[:-1].split()
    word = words[-1].lstrip("([{\"'„“‚‘»«").lower() if words else ""
    if word in _ABBREVIATIONS:
        return False
    if len(word) == 1 and word.isalpha():
        return False  # an initial, or half of "z. B."
    return not (word.isdigit() and len(word) <= 2)  # a day or a rank, not a year


def sentences(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _BREAK.finditer(text or ""):
        if "\n" in m.group(0) or _ends_sentence(text[start : m.start()]):
            out.append(text[start : m.start()])
            start = m.end()
    out.append((text or "")[start:])
    return out


def _shorten(fragment: str) -> str:
    if len(fragment) <= _FRAGMENT_MAX:
        return fragment
    half = (_FRAGMENT_MAX - 3) // 2
    return f"{fragment[:half].rstrip()} … {fragment[-half:].lstrip()}"


def antithesis_fragments(text: str) -> list[str]:
    """One quote per sentence that builds a "not X but Y" antithesis, deduped, in order."""
    found: list[str] = []
    for sentence in sentences(text):
        for pattern in _ANTITHESES:
            m = pattern.search(sentence)
            if m:
                # The hit and the word it turns to: "nicht nur X, sondern auch Y".
                tail = " ".join(sentence[m.end() :].split()[:2])
                quote = _shorten(" ".join(f"{m.group(0)} {tail}".split()))
                if quote not in found:
                    found.append(quote)
                break
    return found


_FINDERS = {NO_DASHES: dash_fragments, NO_ANTITHESIS: antithesis_fragments}


def style_warnings(parts: dict[str, str], checks) -> list[str]:
    """A warning per hit: which part, which rule, and the words that break it. `parts` maps a
    letter part ("body", "subject", "salutation", "closing") to its text; `checks` are the ones
    the user has on."""
    warnings: list[str] = []
    for part, text in parts.items():
        for check in CHECKS:
            if check not in checks:
                continue
            for fragment in _FINDERS[check](text):
                # No full stop after the quote: the fragment often ends in its own.
                warnings.append(
                    f"{_PART.get(part, part)} breaks the {check} rule ({_RULE[check]}): "
                    f'"{fragment}"'
                )
    return warnings
