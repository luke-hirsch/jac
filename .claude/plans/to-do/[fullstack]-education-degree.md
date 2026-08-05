# [fullstack] Education degree level

> Roadmap: **CV-filter phase, item 1** — "i have dropped out of university twice. but i have a
> bachelor of science degree … my highest degree should always be part of the cv. for public
> service jobs this is important money wise in germany."
> Branch: `fullstack/education-degree`

## Context / goal

Three problems, one root cause: `Education` has no idea what a *degree* is. `degree` is a free-text
CharField (`jac/models.py:234`), so "Drop Out Education Physics / Mathematics" is a degree as far as
the code is concerned.

1. **The highest degree can be dropped.** Selection ranks education by relevance to the posting like
   everything else. A BSc that reads as off-topic loses to a drop-out that happens to mention a
   matching keyword. For German public service that is not a cosmetic loss — the *Entgeltgruppe*
   is graded on the highest formal qualification.
2. **The CV says "Drop Out".** Because that's what's in the free-text field. An unfinished study
   period is not a disqualification, but it should read as one line of coursework, not as a headline.
3. **"Highest" is not computable.** A boolean can't rank a BSc against an MSc, which is why this
   guide adds an ordered level rather than an `is_degree` flag.

The enforcement is **not** an LLM instruction. A 1B model will ignore "prefer completed degrees"
roughly half the time. It's the **pin** mechanism, which every rung already force-keeps
(`filter.py:185, 248, 303`) and which `dropOrder` already sorts last (`fit.ts:51`): the pipeline
pins the highest completed degree. Visible in the editor as a pin, removable by the user, no new
concept. The prompt clause goes in too, as a nice-to-have on top.

⚠️ **A prerequisite repair.** `tasks.py:162` calls
`cv.apply_selection(cv.filter_cv(jp.posting_text, mode, executor))` — with no `pinned=` argument.
`JobApplication.pinned_entries` is stored (`models.py:490`), validated (`serializers.py:662`), and
written by the editor (`content-card.tsx:156`) — and then **never read by the pipeline**. Entry pins
have no effect on a re-run today. This guide rides on that mechanism, so step 1 fixes it.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/models.py` | `Education.degree_level` (ordered) + `completed`. |
| `backend/jac/migrations/00XX_education_degree_level.py` | **new** — the migration. |
| `backend/jac/serializers.py` | expose both fields. |
| `backend/jac/cv.py` | `highest_degree_id()`, union it into the pins, degree status in the entry text. |
| `backend/jac/tasks.py` | **repair** — pass the application's `pinned_entries` into `filter_cv`. |
| `backend/jac/llm_prompts.py` | one clause each in `Conversational` and `Instruct`. |
| `frontend/src/lib/queries/jac.ts` | `EducationRow` gains both fields. |
| `frontend/src/routes/_authenticated/cv/education.tsx` | form fields + column. |
| `frontend/src/lib/render/parts.ts` | heading composition: "(no degree)" instead of "Drop Out". |
| `frontend/src/lib/cv-doc.ts` | same for `labelFor` (the editor's entry label). |

## The code

### 1. `backend/jac/models.py` — `Education` (line 222)

```python
class Education(CvEntry):
    """Degree or formal study period."""

    FAVOURITE_LIMIT = 2

    class DegreeLevel(models.IntegerChoices):
        """Ordered so "highest" is a max(), not a lookup table. The ordering is what makes
        the force-keep computable — a boolean could not rank a BSc against an MSc."""

        none = 0, _("No degree")
        vocational = 1, _("Vocational / Ausbildung")
        bachelor = 2, _("Bachelor")
        master = 3, _("Master / Diplom / Magister / Staatsexamen")
        doctorate = 4, _("Doctorate")

    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True
    )
    institution = models.CharField(max_length=200)
    field_of_study = models.CharField(max_length=200, blank=True)
    started = models.DateField()
    ended = models.DateField(null=True, blank=True)
    degree = models.CharField(max_length=100, null=True, blank=True)
    degree_level = models.PositiveSmallIntegerField(
        choices=DegreeLevel, default=DegreeLevel.none
    )
    # Studied but did not finish. Not a black mark — it is real experience — but it must
    # not be *ranked* as a qualification, and the CV must not call it a degree.
    completed = models.BooleanField(default=True)
    grade = models.CharField(max_length=50, null=True, blank=True)
    skills = models.ManyToManyField("Skill", blank=True)
    domains = models.ManyToManyField(Domain, blank=True)

    @property
    def is_degree(self) -> bool:
        return self.completed and self.degree_level > self.DegreeLevel.none
```

Migration:

```bash
cd backend && python manage.py makemigrations jac -n education_degree_level
```

`default=True` on `completed` is the right default for existing rows — most education entries *are*
finished — and the two drop-outs get corrected by hand (verification step 6).

### 2. `backend/jac/serializers.py` — `EducationSerializer.Meta.fields` (line 153)

Add `"degree_level"` and `"completed"` after `"degree"`.

### 3. `backend/jac/cv.py`

**a.** the entry text the selectors score (line ~195) — say out loud whether it's a degree, so the
LLM rungs have the fact available at all:

```python
            text = f"{e.degree or ''} {e.field_of_study or ''}".strip()
            text = (
                f"{text} @ {e.institution} ({window})"
                if text
                else f"{e.institution} ({window})"
            )
            # Explicit, because "Drop Out Education Physics" in a free-text degree field is
            # exactly the ambiguity this guide removes.
            text += (
                f" [completed: {e.get_degree_level_display()}]"
                if e.is_degree
                else " [studied, no degree]"
            )
```

**b.** the force-keep, as a method on `CV` (next to `filter_cv`, line 275):

```python
    def highest_degree_id(self) -> str | None:
        """Flat id of the highest COMPLETED degree in this CV's education set, or None.

        German public service grades pay on the highest formal qualification, so it belongs
        on every CV regardless of how it scores against the posting. Ties (two Masters) go to
        the most recently finished one; an unfinished study period is ordinary content the
        selection may rank and drop like anything else.
        """
        best = None
        for e in self.entries.get("educations", []):
            if not e.is_degree:
                continue
            key = (e.degree_level, e.ended or date.min)
            if best is None or key > (best.degree_level, best.ended or date.min):
                best = e
        return f"education:{best.pk}" if best else None

    def filter_cv(self, job_post_text: str, mode: str | None, executor, pinned=None):
        # The highest degree joins the user's own pins: same mechanism, so every rung
        # force-keeps it and the editor shows it as a pin the user can remove.
        pins = set(pinned or ())
        top = self.highest_degree_id()
        if top:
            pins.add(top)
        return CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            mode=normalize_mode(mode),
            executor=executor,
            pinned=pins,
        ).output()
```

(`date` is already imported in `cv.py`; confirm before typing.)

### 4. `backend/jac/tasks.py` (line 162) — the prerequisite repair

```python
            cv.apply_selection(
                cv.filter_cv(
                    jp.posting_text,
                    mode,
                    executor,
                    # Was missing entirely: the editor writes pinned_entries and the
                    # pipeline never read them, so pins did nothing on a re-run.
                    pinned=set(application.pinned_entries or []),
                )
            )
```

### 5. `backend/jac/llm_prompts.py` — one clause each

`Conversational._INSTRUCTION` (line 140), inside the bullet list after the "keep a skill if…" line:

```python
        "  - a COMPLETED degree outranks an unfinished study period at the same "
        "institution or in the same field;\n"
```

`Instruct._INSTRUCTION` (line 233), after the `0 = not relevant` line:

```python
        "An entry marked [completed: ...] is a formal qualification — rate it at least 2 "
        "unless it is entirely unrelated to the posting.\n"
```

Both are advisory. The pin is what actually guarantees the outcome — do not weaken the pin because
the prompt "should" handle it.

### 6. `frontend/src/lib/queries/jac.ts` — `EducationRow` (line 30)

```ts
  degree: string | null;
  degree_level: 0 | 1 | 2 | 3 | 4;
  completed: boolean;
```

### 7. `frontend/src/routes/_authenticated/cv/education.tsx`

**a.** the schema (line 60): `degree_level: z.number().int().min(0).max(4),` and
`completed: z.boolean(),`.

**b.** the two `defaultValues` blocks (lines ~311 and ~324): `degree_level: row.degree_level ?? 0,
completed: row.completed ?? true,` and `degree_level: 0, completed: true,`.

**c.** a column next to `degree` (line 262):

```tsx
    col.accessor("degree_level", {
      header: "Level",
      cell: (c) => (c.row.original.completed ? DEGREE_LABELS[c.getValue()] : "—"),
    }),
```

**d.** the fields, right after the `degree` field block (line 418):

```tsx
      <form.Field name="degree_level">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Degree level</Label>
            <Select
              value={String(f.state.value)}
              onValueChange={(v) => {
                f.handleChange(Number(v));
                line.save(f.name, Number(v));
              }}
            >
              <SelectTrigger id={f.name}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DEGREE_LEVELS.map(([value, label]) => (
                  <SelectItem key={value} value={String(value)}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Your highest completed degree is pinned onto every generated CV.
            </p>
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="completed">
        {(f) => (
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={f.state.value}
                onCheckedChange={(v) => {
                  f.handleChange(Boolean(v));
                  line.save(f.name, Boolean(v));
                }}
              />
              Completed
            </label>
            <p className="text-xs text-muted-foreground">
              Unchecked renders as "(no degree)" and never counts as your highest
              qualification.
            </p>
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>
```

with, near the schema:

```tsx
const DEGREE_LEVELS: [number, string][] = [
  [0, "No degree"],
  [1, "Vocational / Ausbildung"],
  [2, "Bachelor"],
  [3, "Master / Diplom"],
  [4, "Doctorate"],
];
const DEGREE_LABELS = Object.fromEntries(DEGREE_LEVELS) as Record<number, string>;
```

and `Select`/`SelectContent`/`SelectItem`/`SelectTrigger`/`SelectValue` imported from
`@/components/ui/select`.

### 8. the rendered heading — `frontend/src/lib/render/parts.ts` + `frontend/src/lib/cv-doc.ts`

In `parts.ts`, the `educations` branch of `entryParts`:

```ts
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const base = head ? `${head} @ ${e.institution}` : e.institution;
      return {
        heading: e.completed ? base : `${base} ${NO_DEGREE}`,
        …
```

with, at module scope:

```ts
/** Unfinished study periods say so plainly. It is honest, it is normal on a German CV,
 *  and it beats whatever the user typed into the free-text degree field. */
export const NO_DEGREE = "(no degree)";
```

`cv-doc.ts` `labelFor`'s `educations` branch gets the same suffix, so the editor list and the page
agree:

```ts
      const base = head
        ? `${head} @ ${e.institution} (${w})`
        : `${e.institution} (${w})`;
      return e.completed ? base : `${base} — no degree`;
```

## Tests

**Step 0 — unskip.** Not the active guide: delete the `@skip` decorators in the two backend test
files and every `.skip` in the frontend file.

| file | covers |
| --- | --- |
| `backend/jac/tests/test_models.py` | `Education.is_degree` (completed + level > none; a completed "none" is not a degree; an uncompleted Master is not either). |
| `backend/jac/tests/test_pipeline.py` | `CV.highest_degree_id` picks the Master over the Bachelor, ignores drop-outs, breaks a tie on the later end date, returns None with no degrees at all; `filter_cv` unions it with the caller's pins; the flattened entry text carries the `[completed: …]` / `[studied, no degree]` marker. |
| `backend/jac/tests/test_api.py` | `degree_level` / `completed` round-trip through the education endpoint. |
| `frontend/tests/lib/cv-doc.test.ts` | `labelFor` marks an uncompleted education. |
| `frontend/tests/lib/render-typography.test.ts` | *(guide 2's file)* — add the `entryParts` heading case there once both guides have landed; noted here so it isn't forgotten. |

```bash
cd backend && python manage.py test jac.tests.test_models jac.tests.test_pipeline jac.tests.test_api
cd frontend && npx vitest run tests/lib/cv-doc.test.ts
```

## Verification

1. `python manage.py makemigrations jac -n education_degree_level && python manage.py migrate`.
2. Backend + frontend suites above: red → green.
3. **Fix your own data** (this is the half no code can do): CV → Education. For the two study
   periods, untick **Completed**, set **Degree level** to what you actually studied toward, and take
   the words "Drop Out" out of the free-text Degree field. Add the BSc row if it isn't in the DB —
   check first, because the screenshot shows only the two drop-outs, which may mean the degree was
   never entered rather than that it was dropped.
4. Generate a CV against a posting that has nothing to do with your degree. The BSc must appear,
   with a pin icon in the editor. The drop-outs may or may not — that's the point.
5. Unpin the BSc in the editor, save, re-generate: it may now be dropped. The pin is an override,
   not a lock.
6. Export: the education entries read `B.Sc. Physics @ FU Berlin` and
   `Physics / Mathematics @ FU Berlin (no degree)` — no "Drop Out" anywhere.
7. **Pin repair check** (independent of degrees): pin any job entry, save, hit Generate again — it
   survives the new run. Before this guide it did not.

## Results

<!-- human: raw test output, observed issues, what works -->
