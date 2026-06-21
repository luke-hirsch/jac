# Snippet language flag + AI-share metric for cover letters

## Context

German postings are the higher-yield target, but the cover-letter pipeline has one
language gap. The *furniture* is already bilingual — `_SALUTATION_*`, `_SUBJECT`,
`_CLOSING` in `cover_letter.py` key on `de`/`en`, and `CoverLetterWriter` already injects
`"Write in {language}."`. The gap is the **body source**: `ResumeSnippet` has a single
`content` field and **no language**. So a German posting today hands the writer English
snippets and says "write in de" — i.e. it silently AI-translates your authored voice. That
is precisely the slop the snippet mechanism exists to avoid, and it's why a German letter
wouldn't read as *you*.

**Decision (from the user):** don't force per-language authoring and don't filter/skip.
Add a `language` flag to the snippet; keep selecting by relevance across languages; when the
picked snippet isn't in the posting's language, let the writer translate it (or full-AI
write) as an acceptable fallback. Instead of hiding that cost, **measure it**: the backend
computes an `ai_share` fraction (e.g. 0.37 → "37% written by AI") and returns it for the
frontend to display. This keeps authoring flexible while staying honest about how much of a
given letter is machine-made.

## Scope

Backend only (pipeline + model). Frontend display of `ai_share` and the snippet-editor
language field are the user's domain (noted under Follow-ups).

---

## Changes

### 1. Model — `backend/jac/models.py` (`ResumeSnippet`)

Add a language flag mirroring `JobPosting.language` (ISO-639-1, default `"en"`):

```python
# ISO-639-1 code the snippet is authored in (e.g. "en", "de"). The cover-letter writer
# weaves a snippet untranslated when it matches the posting language; otherwise it
# translates (counted toward ai_share). Defaults to English.
language = models.CharField(max_length=8, default="en")
```

Migration: `python manage.py makemigrations jac` → `0004_resumesnippet_language.py`
(simple `AddField`, default `"en"` backfills existing rows — no data migration needed).

### 2. Serializer — `backend/jac/serializers.py` (`ResumeSnippetSerializer`)

Add `"language"` to `Meta.fields`. No other change (plain `CharField`, no scoping).

### 3. Admin — `backend/jac/admin.py` (`SnippetAdmin`)

Add `"language"` to `list_display` and `list_filter` so you can see/filter the flag while
authoring.

### 4. `SnippetSelector` — `backend/jac/cover_letter.py`

- Accept the posting language so a **native-language tie-break** can prefer an already-in-
  language snippet *only among otherwise-equally-relevant ones* (keeps relevance dominant,
  nudges toward less translation → lower `ai_share`).
- Constructor gains `posting_language: str = "en"`; store as `self.lang`.
- **Keep the native preference out of `_score`.** The body keep-gate is `_score(s) > 0`; a
  numeric bonus folded into `_score` would push a *zero-relevance* native snippet over that
  gate and resurrect it (the same trap the favourite-bonus design sidesteps). Instead leave
  `_score` as pure relevance and add native as a **sort key only**:

```python
def _native(self, s) -> bool:
    return getattr(s, "language", "en") == self.lang

# in select() — relevance first, posting-language breaks ties; the > 0 gate stays on relevance:
intro   = max(intros,   key=lambda s: (self._score(s), self._native(s)), default=None)
closing = max(closings, key=lambda s: (self._score(s), self._native(s)), default=None)
scored  = sorted(bodies, key=lambda s: (self._score(s), self._native(s)), reverse=True)
body    = [s for s in scored if self._score(s) > 0][: self.max_body]
```

No language filtering, nothing dropped for being in the "wrong" language — the tie-break can
only reorder, never resurrect a zero-relevance snippet.

### 5. `CoverLetter.build()` — `backend/jac/cover_letter.py`

- Pass `posting_language=language` into `SnippetSelector(...)`.
- After assembling the body, compute provenance + `ai_share` from the ordered snippets and
  the grade, and add to the result dict:

```python
result = {
    ...                      # existing keys
    "ai_share": self._ai_share(sel["ordered"], language, body_is_ai_fallback),
    "snippet_provenance": {
        "native":     [f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language == language],
        "translated": [f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language != language],
    },
}
```

Where `body_is_ai_fallback` is `True` when there were no snippets (the body, if any, is
fully machine-written) — track it from the existing "no snippets → raw fallback" branch.

### 6. AI-share heuristic — new `CoverLetter._ai_share(...)`

A transparent, length-weighted provenance metric with a grade-based rewrite tax. Constants
at the top of the method so it's tunable; words (not chars) for cross-language stability.

```python
# How much the writer reshapes even same-language prose, by grade. Native words are
# multiplied by this and counted as "AI"; translated words always count fully.
_REWRITE_TAX = {"light": 0.05, "standard": 0.20, "strong": 0.45}

def _ai_share(self, snippets, language, ai_fallback) -> float:
    """Fraction of the body attributable to the machine, 0.0–1.0.

    0.0  = every snippet authored in the posting language, lightly stitched.
    1.0  = no snippets (body fully AI-written) — or all snippets translated at strong grade.
    Heuristic, not exact: the writer melts snippets into prose, so we attribute by source
    provenance + a per-grade rewrite tax rather than diffing output text.
    """
    if ai_fallback or not snippets:
        return 1.0
    tax = self._REWRITE_TAX.get(self.grade, self._REWRITE_TAX["standard"])
    native_w = sum(len(s.content.split()) for s in snippets if s.language == language)
    trans_w  = sum(len(s.content.split()) for s in snippets if s.language != language)
    total = native_w + trans_w
    if not total:
        return 1.0
    ai_w = trans_w + tax * native_w
    return round(ai_w / total, 2)
```

Notes:
- All-native + `light` → `0.05`; all-native + `strong` → `0.45`; all-translated → `1.0`;
  no snippets → `1.0`. Matches the "37%-ish" feel for a mixed letter.
- The raw-fallback path (LLM call failed) leaves snippets *untranslated* in their original
  language — a pre-existing degradation, out of scope here. `ai_share` reflects the normal
  weave path; if you want, gate the tax to `0` when the LLM failed, but don't special-case
  it further in this guide.

### 7. Management command — `backend/jac/management/commands/cover_letter.py`

In `_one`, print `ai_share` (as a percentage) alongside the existing smoke-test output and
include it in the written markdown artifact header, e.g. `> AI share: 37%`.

---

## Tests (AI writes; human runs) — `backend/jac/tests.py`

- **Model/serializer:** `language` defaults to `"en"`; round-trips through
  `ResumeSnippetSerializer`.
- **Native tie-break:** two equally-relevant snippets (same job/skills), one `de` one `en`;
  with `posting_language="de"` the `de` one orders first; with `"en"` the `en` one does.
  Relevance still wins when scores differ (a more-relevant `en` snippet beats a less-relevant
  `de` one).
- **`_ai_share` table:** all-native+light ≈ `0.05`; all-native+strong ≈ `0.45`;
  all-translated → `1.0`; no snippets → `1.0`; a mixed case lands strictly between.
- **`build()` result shape:** `ai_share` is a float in `[0,1]` and `snippet_provenance`
  partitions `snippets_used` into native/translated by the posting language.

Use existing fixtures/factories in `tests.py`; no LLM call needed — test `_ai_share` and the
selector directly (stub or skip `CoverLetterWriter.write`).

---

## Verification

1. `python manage.py makemigrations jac && python manage.py migrate`.
2. In the admin, set a couple of seeded snippets to `language="de"`, leave others `en`.
3. `python manage.py cover_letter` against an EN and a DE posting; confirm:
   - DE posting + EN-only snippets → high `ai_share`, all under `translated`.
   - DE posting with some DE snippets → lower `ai_share`, those listed under `native` and
     ordered ahead of equally-relevant EN ones.
   - The artifact header shows the `AI share: NN%` line.
4. `python manage.py test jac` green.

## Follow-ups (frontend / user's domain)

- Snippet editor: add a `language` dropdown (`en`/`de`) to the CRUD form + a column/filter.
- Cover-letter view: render `ai_share` (e.g. a "37% written by AI" badge) from the API.
- Optional later: gate `_REWRITE_TAX` to `0` on confirmed raw-fallback, and surface
  per-snippet provenance in the UI.
