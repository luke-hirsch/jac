# [backend] mode-enum-and-plumbing

> **Guide 1** in the *LLM-mode redesign* (see the roadmap note "LLM modes — offline
> tolerance"). Staged sequence:
> 1. **mode-enum-and-plumbing** ← *this guide* (vocabulary + DB, minimal behavior change)
> 2. selection-ladder-remap (pipeline speaks modes natively; deletes the compat shim **and jac's
>    `Grade` vocabulary**)
> 3. staggered-instruct-pipeline (shared embed prefilter → LLM rerank)
> 4. llm-reachability-and-executors-endpoint
> 5. model-first-generate-panel (SPA flips to the model-first panel, auto-runs on create; removes
>    the compat `grade` bridge)
> 6. manual-no-run-mode
> 7. chat-assistant-rework (spun out — mostly independent; owns the final autodetect deletion)
>
> Rides along after 3: `[fullstack]-application-pinned-entries` (per-application pins that survive
> re-generation). After 4: `[fullstack]-llm-model-catalog-and-knobs` (run-time model pick +
> effort/temperature knobs — the llm_connector rework). Companion (unnumbered, ops not code):
> `[infra]-tower-inference-server`.

## Context / goal

We are replacing the model-*strength* axis (`light`/`standard`/`strong`, guessed by
`_autodetect_strength`) with an explicit **mode** the user picks per run. **Three modes, not four**
(Lukas, 2026-07-15): the mode names the *selection strategy*; the **alias names the executor**;
"automatic" is a *trigger* property (the SPA auto-runs on application create when the tower
answers — guide 5), not a mode of its own.

| Mode | Meaning | Internal pipeline (today, until guide 2/3) |
| --- | --- | --- |
| `manual` | No AI — no generation run at all; the user hand-curates entries + snippets | *n/a* — **rejected at `/generations/`** (a manual run is a contradiction); the task fail-fasts any stray row |
| `instruct` | Single instruct pass — the tower/ollama default, also pickable on any alias to experiment | `standard` — guide 3 fronts it with the embed prefilter |
| `conversational` | Holistic conversational selection — the natural pairing for the big commercial models | `strong` |

The reason for the reframe: the tower that hosts ollama will be offline from time to time, and the
app must still work. `manual` is the guaranteed-offline path; a mode is chosen by availability +
spend, not by guessing a model's tier. What an earlier draft called `auto` (the self-hosted
embed→instruct ladder) **is** `instruct` on the free default alias — one strategy, one name.

**This guide is plumbing with one deliberate behavior change.** It introduces `Mode` as the
user-facing + DB vocabulary and *translates to the existing internal `grade`* at the task boundary.
The selection/writer code (`filter.py`, `cover_letter.py`, `llm_prompts.py`, `cv.py`) is **not
touched** — it keeps speaking `grade`; guide 2 migrates it and deletes the `mode_to_grade` shim.
`Grade`, `normalize_grade`, and `get_alias_strength` all **stay** for now — but each has a named
deletion point (see the compat ledger below), none of it outlives the redesign.

### Compat ledger — every bridge artifact's scheduled death

The redesign is staged, so guide 1 necessarily plants translation helpers. This table is the
contract that none of them is permanent — each has exactly one owner, no "cleanup later":

| Artifact | Born | Dies | Killed by |
| --- | --- | --- | --- |
| `MODE_TO_GRADE` + `mode_to_grade()` | guide 1 | **guide 2** | pipeline (`filter.py`/`cv.py`/`cover_letter.py`/`llm_prompts.py`/`tasks.py`) goes mode-native |
| jac's `Grade` + `normalize_grade` | pre-existing | **guide 2** | same branch rekeys their last consumers (`filter.py`, `cv.py`, the eval commands) — verified by grep 2026-07-16: nothing else uses the class |
| serializer-local mode→grade literal map (compat `grade` read after the shim dies) | guide 2 | **guide 5** | SPA reads `mode` |
| `GRADE_TO_MODE`, `KNOWN_MODE_INPUTS`, the legacy-grade branch in `normalize_mode` | guide 1 | **guide 5** | SPA sends `mode`; lenient `grade` write dies |
| compat `grade` fields on both read serializers | guide 1 | **guide 5** | same |
| connector strength machinery (`get_alias_strength`, `_autodetect_strength`, `_STRENGTHS`, `strength` config key, `LLM_STRENGTH`) | pre-existing | **guide 5** (per-alias `strength` report) + **guide 7** (everything else, incl. `llm_check`) | chat gate is the last caller |

Permanent survivors: `Mode` itself, and `normalize_mode`'s blank/unknown→`instruct` coercion
(that's the API's input tolerance, not a bridge). After guide 5 the only grade vocabulary left in
the codebase is the connector's strength machinery, and guide 7 ends that — the closing check is
guide 7's `grep -rn "strength\|Grade" backend/` hitting only migrations and comments.

The deliberate change: **the embed-only tier stops being user-facing.** Legacy `light` maps to
`instruct` (interim internal grade `standard`), so a legacy-SPA "light" create now attempts the
instruct rung — on the current 1B local model that's slower and may degrade to embed via the
existing ladder, which is acceptable until the tower lands. Embed-only survives as `instruct`'s
prefilter + degrade stage (guide 3), not as a tier a user picks. The cost guard moves with it:
`free_only` now derives from **the alias** (`is_free_alias`), not from the grade — a free-alias run
must never route support rungs onto paid pins, whatever its mode.

Because the SPA still sends/reads `grade` (values `light`/`standard`/`strong`) until guide 5, this
guide ships a **compatibility bridge** so it can merge on its own without breaking the frontend:
- **lenient write** — the create serializer accepts a legacy `grade` key and maps it to a mode;
- **additive read** — the run serializers expose the new `mode` *and* keep a compat `grade`
  (derived `mode → grade`).

Guide 5 removes the bridge once the SPA speaks modes.

**`manual` is enforced server-side, not just in the SPA.** "No AI" is a promise, and guide 6's
no-run flow lives entirely in the frontend; per the internet-facing posture a client-side guard
alone is a hole — a direct `POST /api/jac/generations/ {"mode": "manual"}` would otherwise enqueue
a real LLM run under the "No AI" label. Two layers, both landed here:

- the **create serializer 400s** `mode="manual"` with a machine-readable field error — a manual
  application is built by hand (guide 6), never via `/generations/`;
- **`generate_run` fail-fasts** a manual row that exists anyway (admin/ORM create): claim it, mark
  it `failed` with a clear error, make **zero** LLM calls. `MODE_TO_GRADE[manual] = light` stays
  purely as the defensive floor for generic mode→grade mapping code — it must never be the reason a
  manual run silently executes.

## Affected files

| Path | Change |
| --- | --- |
| `backend/jac/models.py` | Add `Mode` TextChoices (3 values), `normalize_mode`, `MODE_TO_GRADE` + `mode_to_grade`; rename `GenerationRun.grade` → `mode` (default `Mode.instruct`, `max_length=20`). `Grade`/`normalize_grade` stay. |
| `backend/jac/migrations/` | **Regenerated.** Migration history was reset (2026-07-16, dev DB dropped): after the model change, `makemigrations` births a fresh `0001_initial` with the field as `mode` — no rename, no data remap. |
| `backend/jac/serializers.py` | Create serializer: `grade`→`mode` field + `validate()` bridge (accepts legacy `grade`, **rejects `manual`**). Read serializers: add `mode`, keep compat `grade`. |
| `backend/jac/tasks.py` | Manual fail-fast guard; resolve `grade = mode_to_grade(run.mode)`; `free_only=is_free_alias(alias, user=user)`; add `mode` to `result["meta"]`. Drop the `get_alias_strength` import here. |
| `backend/jac/tests/test_models.py` | *(AI-written)* Update the field-choice cohesion test to `mode`; add `ModeVocabularyTests`. |
| `backend/jac/tests/test_generation_api.py` | *(AI-written)* Mode-create tests incl. manual rejection; read-bridge test. |
| `backend/jac/tests/test_generation_task.py` | *(AI-written)* Drop the dead `get_alias_strength` patches; mode→grade mapping, manual fail-fast, alias-keyed cost guard. |

## The code

### 1. `backend/jac/models.py`

Keep the existing `Grade` block exactly as it is. **Directly below** `normalize_grade` (after
line 61), add the new mode vocabulary:

```python
class Mode(models.TextChoices):
    """How a generation selects — the user-facing axis that replaced model *strength*.

    Three modes: the mode names the selection STRATEGY, the run's alias names the executor,
    and "automatic" is a trigger property (the SPA auto-runs `instruct` on the free default
    alias when the tower answers), not a mode. `manual` never enters the pipeline (the user
    hand-curates); the other two map to an internal `Grade` at the task boundary until the
    pipeline speaks modes natively (see the selection-ladder-remap guide). THIS is the
    canonical list — nothing else hardcodes it.
    """

    manual = "manual", _("No AI")
    instruct = "instruct", _("Instruct")
    conversational = "conversational", _("Conversational")


# Legacy grade -> mode. Accepts a legacy `grade` from the SPA until it speaks modes
# (model-first-generate-panel guide). `light` and `standard` both collapse into
# `instruct` — the embed-only tier is no longer user-facing (it becomes instruct's prefilter).
GRADE_TO_MODE = {
    Grade.light: Mode.instruct,
    Grade.standard: Mode.instruct,
    Grade.strong: Mode.conversational,
}

# Mode -> internal pipeline grade. Temporary translation: the selection/writer code still branches
# on Grade; the selection-ladder-remap guide replaces these call sites with mode-native logic and
# deletes this map. `manual` maps to the safe floor (`light`) ONLY for generic mapping code — the
# serializer rejects manual creates and the task fail-fasts manual rows, so it never executes.
MODE_TO_GRADE = {
    Mode.manual: Grade.light,
    Mode.instruct: Grade.standard,
    Mode.conversational: Grade.strong,
}

# Everything the create serializer will accept as an incoming mode: real modes + legacy grades.
KNOWN_MODE_INPUTS = frozenset(Mode.values) | frozenset(GRADE_TO_MODE)


def normalize_mode(value: str | None) -> str:
    """Coerce any input to a valid `Mode` value. Accepts a mode, or a legacy grade
    (`light`/`standard` → `instruct`, `strong` → `conversational`). Anything else — blank,
    None, a typo — defaults to `instruct` (the AI default; the SPA offers `manual` itself
    when nothing is reachable)."""
    if value in Mode.values:
        return str(value)
    if value in GRADE_TO_MODE:
        return str(GRADE_TO_MODE[value])
    return Mode.instruct


def mode_to_grade(mode: str | None) -> str:
    """Translate a `Mode` to the internal `Grade` the pipeline still branches on. Deleted by the
    selection-ladder-remap guide once the pipeline speaks modes."""
    return MODE_TO_GRADE.get(mode, Grade.standard)
```

Then change the `GenerationRun.grade` field (currently line 648) to:

```python
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.instruct)
```

> `max_length` goes from 10 → 20 because `"conversational"` is 14 chars. Default is
> `Mode.instruct` (the field default only bites direct ORM creates; the serializer/UI drives real
> creates).

### 2. Migrations — regenerate, don't hand-write

The migration history was reset on 2026-07-16 (dev DB dropped, every `00xx_*.py` deleted across
`jac` / `llm_connector` / `spa`), so the hand-written rename/remap migration this guide originally
carried is obsolete — there is no old column to rename and no rows to remap. After typing the
model change:

```bash
cd backend && python manage.py makemigrations && python manage.py migrate
```

This births fresh `0001_initial` files with the field as `mode` from day one. No data migration,
no widen-before-write ordering, no reverse map.

> Caveat: regenerated initials only apply to DBs that never ran the old chain. Any environment
> that did (stage/prod, another dev checkout) must have its DB reset too — or be
> `migrate --fake-initial`-ed after manually aligning the schema.

### 3. `backend/jac/serializers.py`

Update the import near the top (line ~24) from `normalize_grade` to add the new helpers:

```python
from jac.models import (
    ...,
    KNOWN_MODE_INPUTS,
    Mode,
    mode_to_grade,
    normalize_mode,
    ...,
)
```

> Keep `normalize_grade` in the import only if something else in the file still uses it; the
> generation serializers below no longer do. (`grep normalize_grade jac/serializers.py`.)

Replace `GenerationRunCreateSerializer` (lines 483–516) with:

```python
class GenerationRunCreateSerializer(
    ScopeRelatedToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("job_application",)
    # Canonical key is `mode`. A legacy `grade` (light/standard/strong) is still accepted and
    # mapped (the SPA flips to `mode` in the model-first-generate-panel guide). Blank/unknown → `instruct`.
    mode = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = GenerationRun
        fields = [
            "job_application",
            "mode",
            "alias",
            "verify_grounding",
            "verifier_alias",
            "personal_paragraph",
            "research_alias",
            "max_body_snippets",
            "domains",
            "started",
            "ended",
            "min_skill_proficiency",
        ]

    def validate(self, attrs):
        # Bridge: prefer an explicit `mode`, else a legacy `grade` key the old SPA still sends.
        # A blank or unrecognised value coerces to `instruct` (never a 400 — a typo shouldn't
        # fail the request), warning only when the value was non-blank and genuinely unknown.
        raw = (attrs.get("mode") or self.initial_data.get("grade") or "").strip()
        if raw and raw not in KNOWN_MODE_INPUTS:
            logger.warning("Unrecognised mode %r coerced to %r", raw, Mode.instruct)
        mode = normalize_mode(raw)
        if mode == Mode.manual:
            # "No AI" never enqueues a run — the SPA builds manual applications directly
            # (manual-no-run-mode guide). This 400 is the server-side guarantee; the UI
            # guard alone would be bypassable.
            raise serializers.ValidationError(
                {"mode": ["manual never runs a generation — curate the application directly."]}
            )
        attrs["mode"] = mode
        return attrs
```

In `GenerationRunSerializer` (read, lines 519–543) add the compat `grade` and the new `mode`:

```python
class GenerationRunSerializer(serializers.ModelSerializer):
    posting_title = serializers.CharField(
        source="job_application.posting.title", read_only=True, default=""
    )
    # Compat: the SPA still reads `grade` until the model-first-generate-panel guide. Derive it from `mode`.
    grade = serializers.SerializerMethodField()

    def get_grade(self, obj) -> str:
        return mode_to_grade(obj.mode)

    class Meta:
        model = GenerationRun
        fields = [
            "id",
            "job_application",
            "status",
            "stage",
            "error",
            "result",
            "mode",
            "grade",
            "alias",
            "personal_paragraph",
            "verify_grounding",
            "evaluation",
            "score",
            "posting_title",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
```

And `GenerationRunSummarySerializer` (lines 546–553) the same way:

```python
class GenerationRunSummarySerializer(serializers.ModelSerializer):
    """Compact nested shape for `JobApplicationSerializer.runs` — no `result` payload,
    the SPA fetches a run's detail (or subscribes to its socket) separately."""

    grade = serializers.SerializerMethodField()

    def get_grade(self, obj) -> str:
        return mode_to_grade(obj.mode)

    class Meta:
        model = GenerationRun
        fields = ["id", "status", "stage", "mode", "grade", "alias", "created_at"]
        read_only_fields = fields
```

### 4. `backend/jac/tasks.py`

Change the import (line 27) — drop `get_alias_strength` (nothing else in the file uses it),
keep `pick_alias`, add `is_free_alias`:

```python
from llm_connector.conf import is_free_alias, pick_alias
```

Add the model imports (line 33):

```python
from jac.models import GenerationRun, JobPostAddress, Mode, mode_to_grade
```

Directly after the claim block (the `publish_event(run.pk, {"event": "progress", ..., "stage": ""})`
line, ~line 128), add the manual guard. The API refuses manual creates, so this only catches rows
created around the API (admin, shell, a future bug) — fail loudly instead of silently burning LLM
time under a "No AI" label:

```python
    if run.mode == Mode.manual:
        _fail(run, "manual mode never generates — build the application content by hand")
        return
```

Replace the grade resolution (lines 145–146) inside `generate_run`:

```python
            alias = run.alias or "default"
            mode = run.mode or Mode.instruct
            grade = mode_to_grade(mode)
```

And rekey the cost guard on the **alias**, not the grade — the old `free_only=grade == "light"`
tied "never route onto paid pins" to the embed tier; the real invariant is "a free-alias run stays
free". In the `pick_alias(...)` call (line ~166):

```python
                free_only=is_free_alias(alias, user=user),
```

Everything else downstream (`cv.filter_cv(..., grade=grade)`, `CoverLetter(..., grade=grade)`) is
unchanged — it still speaks `grade`. Finally, surface `mode` in the result meta (line 205) so the
SPA can show it later without a shape break:

```python
        result = {
            "meta": {"mode": mode, "grade": grade, "alias": alias},
            "cv": serialize_cv_selection(cv),
            "cover_letter": letter,
        }
```

## Tests

All three files already exist; the AI edits them in place (they're the guide's acceptance
criteria). Run with:

```bash
cd backend && python manage.py test jac.tests.test_models jac.tests.test_generation_api jac.tests.test_generation_task
```

- **`jac/tests/test_models.py`** — `GradeCohesionTests.test_generation_run_field_uses_choices`
  retargeted to the `mode` field (asserts `conversational` in its choices); new `ModeVocabularyTests`
  covering `Mode.values` (three members), `normalize_mode` (modes pass through; `light`/`standard`
  → `instruct`, `strong` → `conversational`; blank/unknown → `instruct`), `mode_to_grade`, and the
  field default (`instruct`). Red now: `Mode`/`normalize_mode`/`mode_to_grade` don't exist and the
  field is still `grade`.
- **`jac/tests/test_generation_api.py`** — `test_omitted_mode_defaults_to_instruct`;
  `test_unknown_mode_is_coerced_to_instruct`; `test_legacy_grade_key_is_mapped_to_a_mode`
  (`strong`→`conversational` and `light`→`instruct`); `test_read_exposes_mode_and_compat_grade`;
  `test_manual_mode_is_rejected_at_create` (400 with a `mode` field error, **no run row created,
  `apply_async` never called** — the server-side "No AI" guarantee). Red now: no `mode` field.
- **`jac/tests/test_generation_task.py`** — drop the ten dead
  `@patch("jac.tasks.get_alias_strength", ...)` decorators (the task no longer imports it; the
  patch would raise `AttributeError` post-impl); `test_mode_maps_to_internal_grade` asserting the
  two AI modes reach `filter_cv`/`CoverLetter` as the right internal grade;
  `test_manual_run_fails_fast_without_llm_calls` (an ORM-created manual run ends `failed` with a
  clear error and `CV`/`CoverLetter`/`AddressExtract` are never touched);
  `test_cost_guard_follows_the_alias_not_the_mode` (a `conversational` run on the free default
  alias still calls `pick_alias` with `free_only=True` — red now, because today `free_only` derives
  from the grade and `strong` would be `False`). Red now:
  `GenerationRun.objects.create(..., mode=...)` rejects the unknown kwarg.

## Verification

1. `python manage.py makemigrations` emits fresh `0001_initial` files (`jac` / `llm_connector` /
   `spa`) and `python manage.py migrate` applies them cleanly on the empty DB.
2. The three test modules go green.
3. Full backend suite stays green (nothing else regressed by the rename):
   `python manage.py test` — a clean wall of dots.
4. Manual smoke (optional, needs the dev stack): from the SPA, generate with the existing grade
   dropdown — a `strong` pick behaves as before (stored as `mode="conversational"`, mapped back to
   `grade="strong"`); a `light` pick now runs the instruct ladder (deliberate — see Context), which
   on the 1B local model may degrade to embed via the existing chain. Nothing in the SPA should
   error on the added `mode` key.
5. `curl` (or the DRF browsable API) `POST /api/jac/generations/` with `{"mode": "manual"}` →
   400 with the `mode` error; no run appears in the list afterwards.

## Results

<!-- Human fills this after implementing + testing: raw test output, observed issues, what works. -->
