# [backend] Make grade / strength one cohesive concept

> **Branch:** `backend/bugfixes` (shared). Tests land red first.
> **No runtime behaviour change** — this is a de-duplication/cohesion refactor. Existing tests that
> encode the *current* defaults must stay green (see the "Decision point" note before touching them).

## Context / goal

"How strong is this run" (`light | standard | strong`) is defined and re-validated in **five+
places** with **three different fallback behaviours**, plus a dead enum whose value disagrees with
its name. This is the clunkiness to fix.

| where | today | fallback |
| --- | --- | --- |
| `llm_connector/conf.py::_STRENGTHS` + `get_alias_strength` | generic "strength" vocabulary | `strong` |
| `jac/models.py::GenerationRun.GradeChoice` | `high = "strong"` — **name/value mismatch, and unused** (field has no `choices=`) | — |
| `jac/models.py::GenerationRun.grade` | `CharField(default="light")`, no choices | — |
| `jac/cv.py::CV.FILTER_GRADE` + `CV.__init__` | literal `["strong","standard","light"]`, validates | `light` |
| `jac/cv.py::CV.filter_cv` | **re-validates** the same grade inline | `light` |
| `jac/serializers.py::GenerationRunCreateSerializer.validate_grade` | literal tuple, coerce+warn | `light` |

Goal: **one canonical `Grade` in `jac`**, imported by the model, serializer, `CV`, and `CVFilter`;
delete the mismatched `GradeChoice`; collapse the triple validation into a single `normalize_grade()`
helper. The connector's `_STRENGTHS`/`get_alias_strength` stays as-is — it's the generic layer and
`llm_connector` must not import `jac` (correct layering; the two vocabularies are intentionally the
same three strings). The single resolution seam stays `run.grade or get_alias_strength(...)` in
`tasks.py`.

### Decision point (do NOT bake into this guide's tests)

`get_alias_strength` / `_autodetect_strength` default to **`strong`** for anything unrecognised,
while `jac` defaults to **`light`**. That's the one genuine inconsistency, and it's *deliberately*
tested (`test_get_alias_strength_defaults_to_strong_when_unset`, `..._missing_alias_is_strong`,
`llm_check` `strength=strong`). Flipping it to `light` (which matches the project's "light is the
showcase default" thesis) is a **behaviour change** the human should sign off on. This guide leaves
it alone; a short "if you want to flip it" appendix lists the exact tests to update. Keep the two
defaults' asymmetry visible in a comment rather than silently changing it.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/models.py` | add `Grade(TextChoices)` + `normalize_grade()`; make `GenerationRun.grade` use `choices=Grade.choices, default=Grade.light`; delete `GradeChoice` |
| `backend/jac/cv.py` | `CV.FILTER_GRADE` derives from `Grade`; `__init__`/`filter_cv` use `normalize_grade` (validate once) |
| `backend/jac/filter.py` | import `Grade`; replace the inline `("standard","strong")` / `["light","standard","strong"]` literals |
| `backend/jac/serializers.py` | `validate_grade` uses `normalize_grade` instead of the literal tuple |
| `backend/jac/migrations/000X_*.py` | **generated** — `makemigrations jac` for the `grade` field's new `choices`/`default` (state-only, no schema change) |
| `backend/jac/tests/test_models.py` | **(test)** — the enum, the field's choices, `normalize_grade` |
| `backend/jac/tests/test_cv_selection.py` | **(test)** — `CV.FILTER_GRADE` derives from `Grade`; unknown grade normalises to light |

## The code

### 1. `backend/jac/models.py`

Add near the top (after the imports, before the managers) the canonical vocabulary + helper:

```python
class Grade(models.TextChoices):
    """The CV-tailoring / cover-letter quality rung. 1:1 with an LLM alias's 'strength'
    (llm_connector.conf.get_alias_strength) — same three strings, but this is jac's own copy so
    the connector stays app-agnostic. This is THE definition; nothing else should hardcode the list.
    """

    light = "light", _("Light")
    standard = "standard", _("Standard")
    strong = "strong", _("Strong")


def normalize_grade(value: str | None) -> str:
    """Coerce any input to a valid Grade value, defaulting to `light`.

    The single validation point for grade across the pipeline (serializer, CV, CVFilter). A blank
    or unknown grade becomes `light` — the safe/cheap rung — never an error (a typo shouldn't fail a
    run). NOTE: this differs from llm_connector.get_alias_strength, which defaults unknowns to
    `strong`; see the grade-cohesion guide's 'Decision point'.
    """
    return value if value in Grade.values else Grade.light
```

Then in `GenerationRun`:

- **delete** the `GradeChoice` inner class (lines ~548–551).
- change the field:

```python
    grade = models.CharField(
        max_length=10, choices=Grade.choices, default=Grade.light
    )
```

### 2. `backend/jac/cv.py`

```python
from jac.models import Certification, Education, Grade, Job, Language, Project, Skill, normalize_grade
```

Replace the class attribute:

```python
    FILTER_GRADE = list(Grade.values)  # ["light", "standard", "strong"]
```

In `__init__`, replace the `if filter_grade in self.FILTER_GRADE … else "light"` block with:

```python
        self.filter_grade = normalize_grade(filter_grade)
```

In `filter_cv`, drop the inline re-validation — trust the normalized grade:

```python
    def filter_cv(self, job_post_text: str, grade: str | None, alias: str = "default"):
        cv_filter = CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            grade=normalize_grade(grade),
            user=self.user,
            alias=alias,
        )
        return cv_filter.output()
```

### 3. `backend/jac/filter.py`

`CVFilter.output()` branches on the grade strings. Import `Grade` and use it so the values aren't
re-spelled:

```python
from jac.models import Grade
```

```python
        if self.grade == Grade.strong:
            selected = self._strong_selection()
            if selected:
                return self._select_holistic(selected)
        if self.grade in (Grade.standard, Grade.strong):
            labels = self._standard_scores()
            if labels:
                return self._select_ranked(labels)
        return self._select(self._light_scores())
```

> `CVFilter.__init__` no longer needs its own validation — callers pass a normalized grade. Leave the
> `grade: str = "light"` default as the safe fallback for direct instantiation.

### 4. `backend/jac/serializers.py`

Replace `GenerationRunCreateSerializer.validate_grade`:

```python
from jac.models import (
    ...,
    normalize_grade,
)
```

```python
    def validate_grade(self, value):
        # Blank stays blank (the task auto-detects from alias strength). A non-blank but unknown
        # grade is coerced to the safe rung rather than 400'd — a typo shouldn't fail the request.
        if not value:
            return value
        normalized = normalize_grade(value)
        if normalized != value:
            logger.warning("Invalid grade %r coerced to %r", value, normalized)
        return normalized
```

### 5. Migration

```bash
cd backend && python manage.py makemigrations jac
```

Adding `choices=`/`default=Grade.light` is a state-only change (the stored value `"light"` is
unchanged), so the migration alters no columns — but Django still records it. Commit it.

## Tests

- `backend/jac/tests/test_models.py` (**append** `GradeCohesionTests`): `Grade.values ==
  ["light","standard","strong"]` and each member's value equals its name (guards the old
  `high="strong"` mismatch); `GenerationRun._meta.get_field("grade").choices` is set and
  `"strong"` is among its values; `normalize_grade` maps `""`, `None`, `"bogus"` → `"light"` and
  passes `"strong"`/`"standard"` through; `GenerationRun` no longer defines `GradeChoice`.
- `backend/jac/tests/test_cv_selection.py` (**append**): `set(CV.FILTER_GRADE) == set(Grade.values)`;
  `CV(...).filter_grade` is `"light"` when constructed with `filter_grade="nonsense"`.

Run:

```bash
cd backend && python manage.py test jac.tests.test_models jac.tests.test_cv_selection
```

## Verification

```bash
cd backend
python manage.py makemigrations jac        # produces one state-only migration
python manage.py test jac                  # full jac suite green (behaviour unchanged)
python manage.py test                      # whole suite green — incl. the connector's
                                           # strength tests, which this guide does NOT touch
```

- Create a run with `grade="STRONG!"` via the API → persisted as `light`, a warning logged, run
  still succeeds (unchanged behaviour, now from a single code path).
- `grep -rn '"light", "standard", "strong"\|\[.strong., .standard., .light.\]' backend/jac` returns
  nothing outside `Grade` — the list is spelled once.

**Done looks like:** one `Grade` definition drives the model field, the serializer, `CV`, and
`CVFilter`; the dead/mismatched `GradeChoice` is gone; grade is normalised in exactly one function;
and the whole suite is green because nothing about runtime behaviour changed.
