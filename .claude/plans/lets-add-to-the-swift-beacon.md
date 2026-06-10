# Plan: `extract_keywords` — model-free keyword extraction from job text

## Context

The `CV` class has a `deterministic_filter(keywords)` method that filters CV entries by keyword list. The missing piece is a way to *generate* that keyword list from a raw job posting. The user wants this to be deterministic (no LLM call).

**Key insight**: the CV already holds a rich vocabulary of every skill, domain, certification, job title, project name, and language in the database. Instead of general-purpose keyword extraction (which needs either a trained model or a statistical corpus), we can do *vocabulary matching*: scan the job text for occurrences of terms the CV already knows. This produces exactly the keywords `deterministic_filter` can act on — zero new dependencies, zero ML.

---

## Approach: vocabulary matching

Build a term set from `self.entries`, then match each term against the input text using case-insensitive, word-boundary-aware regex.

**Why this is better than generic extraction here:**
- Generic extractors (RAKE, YAKE, TF-IDF) return arbitrary frequent words — "responsible", "team", "role" — that can't match any CV entry.
- Vocabulary matching returns *only* terms the CV can act on, so recall is perfect for the filter's purpose.
- Zero new `requirements.txt` lines.

**Sorting longer terms first** avoids partial-match shadowing: "Machine Learning" is checked before "Machine", so both can be returned correctly.

**Word-boundary matching** uses `re.search` with `(?<!\w)...<term>...(?!\w)` (safer than `\b` for terms ending in non-word chars like `C++` or `Node.js`).

---

## Implementation

**File to edit**: `backend/jac/cv.py`

Add one import at the top: `import re`

Add the following method to the `CV` class (between `deterministic_filter` and `ai_filter_entries`):

```python
def extract_keywords(self, text: str) -> list[str]:
    """Return CV vocabulary terms found in text — for use as deterministic_filter input."""
    if not text:
        return []

    # Collect all known terms from loaded entries
    terms: set[str] = set()
    for s in self.entries["skills"]:
        terms.add(s.name)
        terms.update(d.name for d in s.domains.all())
    for j in self.entries["jobs"]:
        terms.add(j.title)
        terms.update(s.name for s in j.skills.all())
        terms.update(d.name for d in j.domains.all())
    for e in self.entries["educations"]:
        if e.field_of_study:
            terms.add(e.field_of_study)
    for c in self.entries["certifications"]:
        terms.add(c.name)
    for p in self.entries["projects"]:
        terms.add(p.name)
        terms.update(s.name for s in p.skills.all())
        terms.update(d.name for d in p.domains.all())
    for la in self.entries["languages"]:
        terms.add(la.name)

    text_lower = text.lower()
    matched: list[str] = []
    # longest first: "Machine Learning" before "Machine"
    for term in sorted(terms, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)"
        if re.search(pattern, text_lower):
            matched.append(term)

    return matched
```

### Typical call sequence

```python
cv = CV(user_pk=1)
keywords = cv.extract_keywords(job_posting_text)
cv.deterministic_filter(keywords)
# cv.entries now contains only relevant sections
```

---

## What this does NOT do

- It won't surface "Python" if the user has no Skill named "Python" in the DB — the vocabulary is bounded by existing data.
- It doesn't infer synonyms ("JS" → "JavaScript"). That's a good reason to eventually add the LLM path (`ai_filter_entries`), but is out of scope here.

---

## Verification

1. Run `python manage.py shell`
2. Create a `CV` instance for a user that has skills/domains populated
3. Call `cv.extract_keywords("We are looking for a Python developer with experience in Django and REST APIs.")`
4. Verify returned list contains the skill/domain names that exist in that user's CV
5. Call `cv.deterministic_filter(keywords)` and confirm `cv.entries["skills"]` is correctly narrowed
