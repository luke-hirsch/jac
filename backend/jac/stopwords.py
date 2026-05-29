"""Stop-word sets for CV keyword filtering, organised by language.

Adding a new language:
  1. Define `_<LANG>_FUNCTION_WORDS` and `_<LANG>_JOB_FILLER` frozensets below.
  2. Register them in `_FUNCTION_WORDS` and `_JOB_FILLER` dicts at the bottom.
  3. `get_stopwords(language, loose)` will pick them up automatically.

Two tiers:
  loose  — function words only; never meaningful as CV filter tokens.
  strict — loose plus ubiquitous job-posting filler that matches every entry
           and therefore never discriminates between them.
"""

# ---------------------------------------------------------------------------
# English
# ---------------------------------------------------------------------------

_EN_FUNCTION_WORDS = frozenset(
    {
        "and",
        "but",
        "for",
        "nor",
        "yet",
        "the",
        "with",
        "from",
        "into",
        "onto",
        "upon",
        "than",
        "via",
        "any",
        "all",
        "such",
        "this",
        "that",
        "these",
        "those",
        "are",
        "you",
        "your",
        "our",
        "their",
        "his",
        "her",
        "its",
        "have",
        "has",
        "had",
        "will",
        "would",
        "should",
        "could",
        "can",
        "may",
        "might",
        "must",
        "not",
        "out",
        "off",
        "per",
        "also",
        "only",
        "very",
        "much",
        "well",
        "more",
        "less",
        "each",
        "other",
        "between",
        "among",
        "across",
        "within",
        "through",
        "during",
        "before",
        "after",
        "since",
        "until",
        "while",
        "where",
        "when",
        "what",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "which",
        "thus",
        "etc",
    }
)

_EN_JOB_FILLER = frozenset(
    {
        "team",
        "teams",
        "work",
        "works",
        "working",
        "job",
        "jobs",
        "role",
        "roles",
        "position",
        "positions",
        "candidate",
        "candidates",
        "applicant",
        "applicants",
        "experience",
        "experienced",
        "year",
        "years",
        "month",
        "months",
        "day",
        "days",
        "good",
        "great",
        "strong",
        "excellent",
        "proven",
        "solid",
        "able",
        "ability",
        "skill",
        "skills",
        "knowledge",
        "understanding",
        "include",
        "including",
        "various",
        "different",
        "looking",
        "seeking",
        "join",
        "joining",
        "based",
        "company",
        "companies",
        "client",
        "clients",
        "customer",
        "customers",
        "requirement",
        "requirements",
        "responsibility",
        "responsibilities",
        "task",
        "tasks",
        "offer",
        "offers",
        "offering",
        "willingness",
        "willing",
        "opportunity",
        "opportunities",
        "new",
        "make",
        "made",
        "get",
        "got",
        "support",
        "supporting",
    }
)

# ---------------------------------------------------------------------------
# German
# ---------------------------------------------------------------------------

_DE_FUNCTION_WORDS = frozenset(
    {
        "und",
        "oder",
        "aber",
        "denn",
        "doch",
        "sondern",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "einen",
        "einem",
        "eines",
        "von",
        "vom",
        "zur",
        "zum",
        "bei",
        "auf",
        "mit",
        "fuer",
        "für",
        "über",
        "ueber",
        "unter",
        "neben",
        "ohne",
        "gegen",
        "ist",
        "sind",
        "war",
        "waren",
        "wird",
        "werden",
        "wirst",
        "werdet",
        "wurde",
        "wurden",
        "hat",
        "haben",
        "hatte",
        "hatten",
        "kann",
        "können",
        "koennen",
        "soll",
        "sollen",
        "muss",
        "muessen",
        "müssen",
        "wollen",
        "darf",
        "dürfen",
        "duerfen",
        "dich",
        "dir",
        "dein",
        "deine",
        "deinen",
        "deinem",
        "deiner",
        "ihr",
        "ihre",
        "ihren",
        "ihrem",
        "ihrer",
        "uns",
        "unsere",
        "unseren",
        "auch",
        "noch",
        "nur",
        "schon",
        "sehr",
        "viel",
        "viele",
        "vielen",
        "nicht",
        "kein",
        "keine",
        "keinen",
        "alle",
        "alles",
        "jeder",
        "jede",
        "diesem",
        "dieser",
        "diese",
        "diesen",
        "dieses",
    }
)

_DE_JOB_FILLER = frozenset(
    {
        "arbeit",
        "arbeiten",
        "arbeitest",
        "stelle",
        "stellen",
        "beruf",
        "berufe",
        "karriere",
        "jahr",
        "jahre",
        "monat",
        "monate",
        "tag",
        "tage",
        "kollege",
        "kollegen",
        "kollegin",
        "kolleginnen",
        "unternehmen",
        "firma",
        "kunde",
        "kunden",
        "kundin",
        "kundinnen",
        "anforderung",
        "anforderungen",
        "aufgabe",
        "aufgaben",
        "fähigkeit",
        "fähigkeiten",
        "faehigkeit",
        "faehigkeiten",
        "kenntnis",
        "kenntnisse",
        "erfahrung",
        "erfahrungen",
        "möglichkeit",
        "möglichkeiten",
        "moeglichkeit",
        "moeglichkeiten",
        "bewerbung",
        "bewerber",
        "bewerberin",
        "geben",
        "gibt",
        "bieten",
        "bietet",
        "suchen",
        "suchst",
        "freuen",
        "gemeinsam",
        "verschiedene",
        "wichtig",
        "wichtige",
        "wichtigen",
        "neue",
        "neuer",
        "neues",
        "neuen",
    }
)

# ---------------------------------------------------------------------------
# add new languages here
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_FUNCTION_WORDS: dict[str, frozenset] = {
    "en": _EN_FUNCTION_WORDS,
    "de": _DE_FUNCTION_WORDS,
}

_JOB_FILLER: dict[str, frozenset] = {
    "en": _EN_JOB_FILLER,
    "de": _DE_JOB_FILLER,
}

# Combined fallback (all languages) used when language is unknown.
_LOOSE_STOPWORDS = frozenset().union(*_FUNCTION_WORDS.values())
_STRICT_STOPWORDS = _LOOSE_STOPWORDS | frozenset().union(*_JOB_FILLER.values())


def get_stopwords(language: str | None = None, *, loose: bool = False) -> frozenset:
    """Return the appropriate stop-word set.

    Args:
        language: ISO 639-1 code ("en", "de", …). None means use all languages.
        loose: True → function words only. False (default) → also include job-posting filler.

    Returns:
        A frozenset of lowercase stop words.
    """
    if language is None:
        return _LOOSE_STOPWORDS if loose else _STRICT_STOPWORDS

    lang = language.lower()
    func = _FUNCTION_WORDS.get(lang, frozenset())
    if loose:
        return func
    filler = _JOB_FILLER.get(lang, frozenset())
    return func | filler
