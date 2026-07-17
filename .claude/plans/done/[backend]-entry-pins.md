# [backend] entry-pins

> **Rework guide 3 of 3** — *single-executor redesign (2026-07-16)*. Depends on guides 1+2
> (`executor-connector`, `pipeline-single-executor`); same branch (`backend/executor-rework`).
> Small guide: the pinning hooks were already **typed** with CVFilter in guide 2 §2 — this guide
> adds the model field + API surface, wires the task, and owns the tests for the whole behavior.

## Context / goal

Lukas's rule: **manual edits and entry pins survive every kind of AI generation run.** A pinned
career-DB entry is always in the tailored CV, whatever the mode or executor; if the `high`
(holistic) selection would have dropped it, the run attaches an expressive warning but keeps it.

Scope split, deliberately:

- **Entry pins are backend** (this guide): a per-application set of entry ids that every
  selection rung force-includes.
- **Letter-text edit survival is a frontend concern**: auto-fill already only touches an
  *empty* application (task-side, unchanged), and re-run results land in the application only
  when the user explicitly applies them from the SPA — the paragraph-level merge ("replace AI
  paragraphs, keep mine") belongs to the SPA's apply flow and is deferred to the frontend phase.
  Nothing in this guide touches `cover_letter`/`letter_meta` semantics.

Pins are **not** favourites: a favourite is a career-DB-wide ranking nudge (small, capped,
per-type limits); a pin is a per-application *guarantee*. They compose — a favourite can also be
pinned.

## Affected files

| Path | Change |
| --- | --- |
| `jac/models.py` | `JobApplication.pinned_entries` JSONField. |
| `jac/serializers.py` | `pinned_entries` writable on the application + shape/ownership validation. |
| `jac/filter.py` | Already typed in guide 2 (the `pinned` param, keep-guarantees, `_PIN_WARNING`); no new edits. |
| `jac/tasks.py` | Already typed in guide 2 (`pinned=application.pinned_entries`); no new edits. |
| `jac/generation_result.py` | Already typed in guide 2 (`pinned`/`warning` row keys); no new edits. |

## Approach / key decisions

- **Pins live on the application, as flat ids** (`["job:3", "skill:12", …]` — the pipeline's
  native `type:pk` vocabulary), not as M2M rows. They scope one application, die with it, and
  the pipeline consumes exactly this shape; a join table would buy nothing but migrations.
- **Validation is shape + ownership, tolerance for staleness.** A PATCH validates the list
  shape, the id pattern, and that every referenced entry exists and belongs to the user (400
  otherwise — a typo'd pin that silently never matches would be a support nightmare). An entry
  deleted *after* pinning simply stops matching at merge time — the filter ignores unknown ids;
  no cleanup cascade needed.
- **The guarantee is selection-layer, not post-hoc.** Pinned ids ride into `CVFilter` and are
  kept by each rung's own keep-logic (guide 2): label rung keeps them regardless of verdict,
  embed rung exempts them from the floor, holistic rung forces them back **with the warning**.
  Doing it inside the filter keeps ranking coherent (a forced entry still sorts by its real
  score) instead of stapling entries onto a finished selection.
- **The warning is data, not a log line.** `_PIN_WARNING` travels on the entry row through
  `apply_selection` → `serialize_cv_selection` → `run.result.cv` — the SPA renders it on the
  entry card. Only the `high` rung emits it (only that rung forms an opinion about the whole
  set).
- **Cap: 50 pins.** An "everything pinned" application is the manual flow wearing a costume;
  the cap keeps the guarantee meaningful and the validation cheap.

## The code

### 1. `jac/models.py` — `JobApplication`

After `letter_meta`:

```python
    # Per-application entry pins: flat pipeline ids ("job:3") the user has locked
    # into the tailored CV. Every generation run force-keeps them (CVFilter's
    # keep-guarantees); the high mode may attach a warning to one it would have
    # dropped, but never drops it. Not favourites — a favourite nudges ranking
    # career-DB-wide, a pin GUARANTEES presence in THIS application.
    pinned_entries = models.JSONField(default=list, blank=True)
```

### 2. `jac/serializers.py` — `JobApplicationSerializer`

Add `"pinned_entries"` to `fields` (it stays writable — not in `read_only_fields`), plus:

```python
    _PIN_RE = re.compile(r"^(skill|job|project|education|certification|language):\d+$")
    _PIN_MODELS = {
        "skill": Skill, "job": Job, "project": Project, "education": Education,
        "certification": Certification, "language": Language,
    }
    MAX_PINS = 50

    def validate_pinned_entries(self, value):
        """Shape + ownership. Stale pins (entry deleted later) are tolerated at
        merge time; garbage is not tolerated at write time."""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError("Expected a list of entry ids.")
        if len(value) > self.MAX_PINS:
            raise serializers.ValidationError(
                f"At most {self.MAX_PINS} pins — pin less, or build the CV manually."
            )
        bad = [v for v in value if not self._PIN_RE.match(v)]
        if bad:
            raise serializers.ValidationError(f"Malformed entry ids: {bad}")
        user = self.context["request"].user
        for etype, model in self._PIN_MODELS.items():
            pks = [int(v.split(":")[1]) for v in value if v.startswith(f"{etype}:")]
            if not pks:
                continue
            owned = set(
                model.objects.filter(user=user, pk__in=pks).values_list("pk", flat=True)
            )
            missing = [pk for pk in pks if pk not in owned]
            if missing:
                raise serializers.ValidationError(
                    f"Not found or not yours: {etype}:{missing}"
                )
        return list(dict.fromkeys(value))  # de-dupe, order-preserving
```

(`import re` at the top; the entry models are already imported.)

### 3. Already in place from guide 2 — verify while typing

- `CVFilter.__init__(…, pinned=None)` stores `self.pinned = frozenset(pinned or ())`.
- `_select_ranked`: keep on `… or e["id"] in self.pinned`.
- `_select`: pinned entries survive the floor cut.
- `_select_holistic`: pinned forced back; non-chosen pinned rows carry
  `"warning": self._PIN_WARNING`; all rows carry `"pinned": bool`.
- `tasks.generate_run` passes `pinned=application.pinned_entries`.
- `apply_selection` stamps `obj.pinned` / `obj.selection_warning`;
  `serialize_cv_selection` emits `pinned` + `warning` per row.

## Tests — on disk, red now

- `jac/tests/test_api.py::PinnedEntriesApiTests` — PATCH round-trip, shape 400s, foreign-entry
  400, de-dupe, cap.
- `jac/tests/test_pipeline.py::PinnedSelectionTests` — label rung keeps a 0-rated pinned entry;
  embed rung keeps a below-floor pinned entry; holistic rung forces a dropped pin back WITH the
  warning while a chosen pin carries none; stale pin ids are ignored without error; pinned rows
  are flagged in `serialize_cv_selection`.

## Verification

1. `python manage.py test jac` green (with guides 1+2 in place).
2. Live: pin an obviously irrelevant entry on an application, re-run `standard` and `high` via
   the API — the entry is present in both results; the `high` result carries the warning string
   on exactly that entry.

## Results

<!-- Human fills this in: raw test output, observed issues, what works. -->
