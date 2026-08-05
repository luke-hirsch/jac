# [fullstack] German letter register — ihr / Sie, and the refusal guard

> Roadmap: **cover-letter phase, item 3** — "ties in to the style matrix: the personal to formal
> tone is great. if set to personal avoid pronouns like 'Du' and 'Sie' in german. use 'Ihr' or
> 'Euch'. in formal and neutral the 'Sie' and 'Ihren' is perfect." — plus roadmap item **#2**
> (cover-letter refusal guard), which lives in the same class.
> Branch: `fullstack/letter-register-de`

## Context / goal

The tone × focus matrix shapes the *voice* of the letter but says nothing about how it **addresses
the reader**, which in German is not a stylistic detail — it's the first thing anyone notices. A
"personal" German letter should say *ihr/euch/euer*; a formal one *Sie/Ihnen/Ihre*; neither should
ever say *du* (a company is not one person). English has no such fork, so all of this is
`language == "de"` only.

Four things get in the way today:

1. **The prompt never mentions address form at all** (`llm_prompts.py:424–433` is tone only), so a
   1B model defaults to whatever its German training data leaned on — usually *Sie*.
2. **The furniture is keyed by language alone** (`cover_letter.py:38–44`). A "personal" German
   letter still opens *"Sehr geehrte Damen und Herren,"* and closes *"Mit freundlichen Grüßen"*.
   Fixing the pronouns without the furniture produces something incoherent — warm body, stiff
   greeting.
3. **Nothing checks the output.** A small model will slip. A regex costs nothing and catches it.
4. Two bugs found while reading the same file:
   - `CoverLetterWriter(… posting_text=self.job_posting)` (`cover_letter.py:120` and `:331`) passes
     the **JobPosting model instance**, not its text. In `high` mode that stringifies a Django
     object into the prompt.
   - `render_markdown` prints the closing **twice** — once from `_CLOSING` (`cover_letter.py:300`)
     and again from `r["closing"]` (`:302`), which holds the same value. The markdown letter ends
     "Mit freundlichen Grüßen / Lukas / Mit freundlichen Grüßen".

And the roadmap's standing item: `CoverLetterWriter.write()` returns `(raw or "").strip()` — any
non-empty reply becomes the letter body, so a small model's *"I'm sorry, I can't assist with that"*
ships as a cover letter.

**One design decision worth stating.** The posting itself reveals which form the company uses, and
at `standard` the writer never sees the posting (it's the fabrication vector). Detecting the form
with a **deterministic regex** and passing the *result* — one token — is not leaking the posting.
The resolution rule:

| tone | address form |
| --- | --- |
| `formal` | always `Sie` |
| `neutral` | the posting's form if detectable, else `Sie` |
| `personal` | always `ihr` — the user's explicit instruction wins over the posting, and `du` is never used |

## Affected files

| path | why |
| --- | --- |
| `backend/jac/register.py` | **new** — form detection + leak check, pure, no LLM. |
| `backend/jac/llm_prompts.py` | address-form clause; refusal guard. |
| `backend/jac/cover_letter.py` | furniture by tone, form resolution, the two bugs, `register` in the result. |
| `backend/jac/serializers.py` | pass `register` through the generation result. |
| `frontend/src/lib/queries/generations.ts` | the `register` field. |
| `frontend/src/components/applications/result-view.tsx` | one badge. |

## The code

### 1. `backend/jac/register.py` (new)

```python
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

# "Sie"/"Ihnen"/"Ihre" capitalised mid-sentence is the formal you; lowercase "sie" is
# she/they and is deliberately NOT counted.
_FORMAL = re.compile(r"\b(?:Sie|Ihnen|Ihr|Ihre[rmns]?)\b")
# "euch"/"euer"/"eure" are unambiguous. Bare "ihr" is not (it is also "her"/"their"), so it
# is left out — a false positive here would flip the whole letter's register.
_PLURAL = re.compile(r"\b(?:euch|eue?re?[rmns]?|euer)\b", re.IGNORECASE)
_SINGULAR = re.compile(r"\b(?:du|dich|dir|deine?[rmns]?|dein)\b", re.IGNORECASE)


def detect_address_form(text: str) -> str:
    """The form a German job posting addresses its reader with: "sie" / "ihr" / "du", or ""
    when there is no signal (an English ad, a terse one, a PDF that lost its text layer)."""
    if not text:
        return ""
    counts = {
        SIE: len(_FORMAL.findall(text)),
        IHR: len(_PLURAL.findall(text)),
        DU: len(_SINGULAR.findall(text)),
    }
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] else ""


def resolve_address_form(tone: str, posting_text: str, language: str) -> str:
    """The form the letter should use. See the guide's table: the user's explicit tone wins
    at both extremes, and only `neutral` mirrors the company."""
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
    """Pronouns in `body` that contradict `form`, deduped and in order of appearance.

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
    }[form]
    found: list[str] = []
    for pattern in wrong:
        for hit in pattern.findall(body or ""):
            if hit not in found:
                found.append(hit)
    return found
```

### 2. `backend/jac/llm_prompts.py` — `CoverLetterWriter`

**a.** the address clause. Next to `self._TONE` (line 424):

```python
        # German only: how the letter addresses the company. English has no such fork, so
        # an empty address_form simply adds nothing to the prompt.
        self._ADDRESS = {
            "ihr": (
                "Address the company in German with the second-person PLURAL — "
                "'ihr', 'euch', 'euer'. Never 'Sie'/'Ihnen'/'Ihre', and never the singular "
                "'du': you are writing to a team, not to one person."
            ),
            "sie": (
                "Address the company in German with the formal 'Sie' / 'Ihnen' / 'Ihre'. "
                "Never 'du' or 'ihr'."
            ),
            "du": (
                "Address the company in German with the singular 'du' / 'dich' / 'dein', "
                "mirroring the posting's own register."
            ),
        }
```

with `address_form: str = ""` added to the constructor and stored on `self`.

**b.** `_prompt()` (line 460) puts it right after the tone, where the model reads it first:

```python
        tone = self._TONE.get(self.tone, self._TONE["neutral"])
        focus = self._FOCUS.get(self.focus, self._FOCUS["balanced"])
        address = self._ADDRESS.get(self.address_form, "")
```
```python
            f"{tone} {focus}\n{address}\n{common}\n\n"
```

**c.** the refusal guard — `write()` (line 448):

```python
    # A small model asked for a cover letter sometimes answers with a refusal or a
    # one-line meta-comment. Both are non-empty strings, and `write()` used to hand them
    # straight through as the letter body.
    _REFUSAL_RE = re.compile(
        r"^\s*(?:i\s*(?:'m|am)?\s*(?:sorry|afraid)|i\s*(?:can\s*not|cannot|can't|won't|"
        r"am\s+unable)|as an ai|i'm just an ai|es tut mir leid|ich kann (?:dir|ihnen|das)"
        r"|leider kann ich)",
        re.IGNORECASE,
    )
    # The target band starts at 170 words; anything under this is not a letter, whatever
    # it is. Deliberately far below the band so a terse-but-real letter still passes.
    _MIN_WORDS = 60

    def write(self) -> str:
        """Return the composed body prose. '' when there are no CV facts, the LLM fails,
        or the reply is not a letter — the caller turns '' into LETTER_STUB."""
        if not (self.cv_facts or "").strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("CoverLetterWriter: LLM call failed")
            return ""
        text = (raw or "").strip()
        if self._REFUSAL_RE.match(text):
            logger.warning("CoverLetterWriter: model refused — %r", text[:120])
            return ""
        if len(text.split()) < self._MIN_WORDS:
            logger.warning("CoverLetterWriter: reply too short (%d words)", len(text.split()))
            return ""
        return text
```

### 3. `backend/jac/cover_letter.py`

**a.** furniture keyed by `(language, tone)` — replace lines 38–44:

```python
# Letter furniture, keyed by (language, tone). Tone is not only voice: in German it decides
# the address register, and a warm body under "Sehr geehrte Damen und Herren," reads broken.
# NOTE: German Grußformeln take NO trailing comma (Duden) — the old map had one.
_SALUTATION_NAMED = {
    ("en", "personal"): "Hi {name},",
    ("en", "neutral"): "Dear {name},",
    ("en", "formal"): "Dear {name},",
    ("de", "personal"): "Hallo {name},",
    ("de", "neutral"): "Guten Tag {name},",
    ("de", "formal"): "Sehr geehrte/r {name},",
}
_SALUTATION_GENERIC = {
    ("en", "personal"): "Hi there,",
    ("en", "neutral"): "Dear Hiring Team,",
    ("en", "formal"): "Dear Sir or Madam,",
    ("de", "personal"): "Hallo zusammen,",
    ("de", "neutral"): "Sehr geehrtes Team,",
    ("de", "formal"): "Sehr geehrte Damen und Herren,",
}
_SUBJECT = {"en": "Application for {title}", "de": "Bewerbung als {title}"}
_CLOSING = {
    ("en", "personal"): "Best,",
    ("en", "neutral"): "Kind regards,",
    ("en", "formal"): "Yours sincerely,",
    ("de", "personal"): "Viele Grüße",
    ("de", "neutral"): "Beste Grüße",
    ("de", "formal"): "Mit freundlichen Grüßen",
}


def _furniture(table: dict, language: str, tone: str) -> str:
    """(language, tone) with two fallbacks: unknown tone → neutral, unknown language → en."""
    for key in ((language, tone), (language, "neutral"), ("en", tone), ("en", "neutral")):
        if key in table:
            return table[key]
    return ""
```

**b.** `_salutation` takes the tone (line 223):

```python
    def _salutation(self, language: str, tone: str) -> str:
        name = self._recipient()["contact_name"]
        table = _SALUTATION_NAMED if name else _SALUTATION_GENERIC
        text = _furniture(table, language, tone)
        return text.format(name=name) if name else text
```

**c.** `build()` (lines 118–170) — resolve the form, pass it and the real posting text, put the
register in the result:

```python
        address_form = resolve_address_form(tone, self._posting_text(), language)

        body = CoverLetterWriter(
            executor=self.executor,
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            tone=tone,
            focus=focus,
            address_form=address_form,
            cv_facts=cv_facts,
            personality_dossier=personality,
            style_dossier=style,
            company_dossier=research["dossier"],
            mode=self.mode,
            posting_text=self._posting_text(),  # was self.job_posting — the MODEL object
        ).write()
```

and in the result dict:

```python
            "salutation": self._salutation(language, tone),
            …
            "closing": _furniture(_CLOSING, language, tone),
            "register": {
                "form": address_form,
                "leaks": register_leaks(body, language, address_form),
            },
```

The same two arguments (`address_form=`, `posting_text=self._posting_text()`) go into the
`_repair()` rewrite call (line 331) — it builds a second `CoverLetterWriter` and currently repeats
both bugs.

**d.** `render_markdown` (lines 299–303) — the closing was printed twice:

```python
        out.append(r["body"])
        out.append("")
        out.append(r["closing"])
        out.append("")
        out.append(snd["name"])
```

### 4. surfacing it

`register` rides along in the generation result payload (same path as `grounding` — follow that
field through `serializers.py` and `lib/queries/generations.ts`), and `result-view.tsx` renders one
badge next to the existing ai_share / grounding badges:

```tsx
        {letter.register?.leaks?.length ? (
          <Badge variant="outline" className="text-amber-600">
            register: {letter.register.leaks.join(", ")} — wrong form of "you"
          </Badge>
        ) : null}
```

## Tests

**Step 0 — unskip.** Delete the `@skip` decorators in `backend/jac/tests/test_pipeline.py`.

`backend/jac/tests/test_pipeline.py` — deterministic, no network. Covers:

- `detect_address_form`: a `du`-posting, an `ihr`-posting, a `Sie`-posting, an English posting
  (`""`), an empty string (`""`), and the ambiguity guard — lowercase `sie` (she/they) and bare
  `ihr` (her/their) must **not** register as address forms.
- `resolve_address_form`: `personal` → `ihr` whatever the posting says (including a `du` posting);
  `formal` → `sie` whatever the posting says; `neutral` mirrors the posting and falls back to `sie`;
  English → `""` at every tone.
- `register_leaks`: `Sie`/`Ihnen` in an `ihr` letter is flagged; `euch` in a `Sie` letter is
  flagged; `du` is flagged in both; a clean letter returns `[]`; English returns `[]`; results are
  deduped.
- furniture: all three German tones produce a different salutation *and* closing; no German closing
  ends in a comma; an unknown tone falls back to neutral; an unknown language falls back to English.
- `CoverLetterWriter`: the `ihr` clause appears in the prompt and the `Sie` clause does not (and
  vice versa); no address clause at all for English; the refusal guard turns
  `"I'm sorry, I can't assist with that."` and a 20-word reply into `""` while a 200-word letter
  passes through.
- `render_markdown` contains its closing exactly once.

```bash
cd backend && python manage.py test jac.tests.test_pipeline
```

## Verification

1. Suite red → green; `python manage.py check`.
2. Generate against a **German** posting with the personality tone set to **personal**. The letter
   must open "Hallo zusammen,", use *ihr/euch*, and close "Viele Grüße" with no comma. No "Sie",
   no "Du".
3. Same posting, tone **formal**: "Sehr geehrte Damen und Herren," + *Sie* + "Mit freundlichen
   Grüßen".
4. Tone **neutral** against a posting written in the `du` form, then against one written in `Sie`:
   the neutral letter should mirror — `du`-posting → *du*, `Sie`-posting → *Sie*.
5. An English posting at every tone: no German clause anywhere, no register badge.
6. Force a slip (temporarily hard-code `address_form="ihr"` on a `Sie`-heavy letter) and confirm the
   badge lists the offending pronouns — it must flag, never silently rewrite.
7. `high` mode against a German posting: confirm the prompt now carries the posting **text**. Before
   this guide it carried `JobPosting object (12)` — worth checking the logged prompt once.
8. Export a letter to markdown: the closing appears once, not twice.

## Results

<!-- human: raw test output, observed issues, what works -->
