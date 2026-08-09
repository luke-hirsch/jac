# [fullstack] Education degree level

> Roadmap: **polish-the-PDF phase, item 2** — "i have dropped out of university twice. but i have a
> bachelor of science degree … my highest degree should always be part of the cv. for public
> service jobs this is important money wise in germany."
> Branch: `fullstack/education-degree`
> **Activated 2026-08-07**, then **rescoped the same day to one field** (`degree_level`, no
> `completed` flag) — see "Why one field" below. Contracts verified against the code on `main`
> (post `page-fit`); tests on disk: **20 backend (all red)**, **6 frontend (3 red, 3 green
> regression guards)**.

## Implementation log (2026-08-08)

Lukas typed the **first** shape of this guide (`degree_level` + `completed`) end to end before the
rescope — migration `0007_education_completed_education_degree_level.py`. He then asked the AI to
finish the rescope, so **the code below was written by the AI on this branch**, not typed by hand
(explicit per-task override of the usual working style). Testing and merging stay with the human.

What the rescope deleted: `completed` from `models.py`, `serializers.py`, `cv.py`,
`render.py`, `generation_result.py`, `spa/portfolio.py`, `cv_export.py`, `cv_import.py`,
`queries/jac.ts`, `cv-doc.ts`, `parts.ts` and four places in `education.tsx`; every marker now asks
`is_degree` / `isDegree`. Added `secondary = 1` (Abitur / High School) and renumbered
vocational–doctorate to 2–5 — **no data migration needed**, all five education rows were still at
the `0007` default. New migration: `0008_drop_education_completed.py` (RemoveField + AlterField).

Suites after the rescope — **green**: backend `test_models` / `test_pipeline` / `test_api` /
`spa.test_portfolio` = 176 tests OK (38 skipped), plus `test_attachments` / `spa.test_auth` /
`spa.test_settings_hardening` = 59 OK; frontend full run 427 passed, 40 skipped. `tsc -b` clean.
`test_prompts` (live tower) not run.

One test file needed a fix of its own: the shared `education` fixture in `cv-doc.test.ts` predates
the field, so `isDegree` read `undefined > 0` and the *pre-existing* `labels an education with
degree + field head` test started failing. It now carries `degree_level: 3`. That failure was the
one-field shape earning its keep — under `completed` the same fixture gap was invisible in tests
and would only have shown up on a page.

**Still the human's:** `python manage.py migrate`, then verification steps 3–9 below (classify the
five rows, generate, export, pin check).

## Context / goal

Three problems, one root cause: `Education` has no idea what a _degree_ is. `degree` is a free-text
CharField (`backend/jac/models.py:234`), so "Drop Out Education Physics / Mathematics" is a degree as
far as the code is concerned.

1. **The highest degree can be dropped.** Selection ranks education by relevance to the posting like
   everything else. A BSc that reads as off-topic loses to a drop-out that happens to mention a
   matching keyword. For German public service that is not a cosmetic loss — the _Entgeltgruppe_
   is graded on the highest formal qualification.
2. **The CV says "Drop Out".** Because that's what's in the free-text field. An unfinished study
   period is not a disqualification, but it should read as one line of coursework, not as a headline.
3. **"Highest" is not computable.** Nothing in the row ranks a BSc against an MSc, which is why this
   guide adds an ordered level.

The enforcement is **not** an LLM instruction. A 1B model will ignore "prefer completed degrees"
roughly half the time. It's the **pin** mechanism, which every rung already force-keeps
(`backend/jac/filter.py:185` embed floor, `:248` instruct verdicts, `:303` holistic) and which
`dropOrder` already sorts last (`frontend/src/lib/render/fit.ts:53`): the pipeline pins the highest
degree. Visible in the editor as a pin, removable by the user, no new concept. The prompt clause goes
in too, as a nice-to-have on top.

## Why one field, and why not `grade`

The real rows (`sqlite3 backend/db.sqlite3 "select … from jac_education"`, 2026-08-07):

| institution              | degree              | grade   | ended | → level             |
| ------------------------ | ------------------- | ------- | ----- | ------------------- |
| Lessing-Gymnasium        | Abitur              | 1.4     | 2008  | `secondary`         |
| Swartz Creek High School | Honorary HS Diploma | GPA 3.6 | 2006  | `secondary`         |
| MLU Halle-Wittenberg     | Bachelor of Science | 2.6     | 2012  | `bachelor` ← pinned |
| TU Berlin                | Drop Out            | —       | 2016  | `none`              |
| FU Berlin                | Drop Out            | —       | 2020  | `none`              |

**`grade` was the obvious shortcut and is rejected on purpose.** Graded/ungraded does split this
table correctly today, with zero schema change — but it fails on two things that matter here:

- **It can't rank.** Three rows are graded, so "highest" would fall back to _latest ended_, which
  picks the BSc only by accident of chronology; any graded education row added later steals the pin.
  Grade values can't break the tie either — German grades run 1–5 with 1 best, so the 1.4 Abitur
  would "beat" the 2.6 BSc.
- **It couples the pin to a display decision.** That BSc grade is 2.6. The day it gets taken off the
  CV, the degree stops being a degree _and_ prints "(no degree)" next to it — a false statement on
  the document, caused by a formatting choice. `grade` is display data; it stays display data.
  (`test_api.py::test_a_grade_alone_does_not_make_a_degree` pins this so it can't creep back.)

**And why no `completed` flag** (the shape this guide had at first activation): make the level mean
what the entry **earned**, not what it aimed at, and the flag becomes redundant — a drop-out earned
nothing, so it is `none`. The level it was aiming at is already prose in the row: TU Berlin's
`field_of_study` literally reads `"Physics (Master)"`, and that text is what the LLM rungs read
anyway. One field, one meaning, one Select in the form.

⚠️ **A prerequisite repair.** `backend/jac/tasks.py:162` calls
`cv.apply_selection(cv.filter_cv(jp.posting_text, mode, executor))` — with no `pinned=` argument.
`filter_cv` _accepts_ `pinned` (`cv.py:275`) and `CVFilter` honours it, and
`JobApplication.pinned_entries` is stored (`models.py:490`), validated (`serializers.py:678`), and
written by the editor (`content-card.tsx:174`) — but the one caller that matters never passes it.
Entry pins have no effect on a re-run today. This guide rides on that mechanism, so step 4 fixes it.

## Affected files

| path                                                    | why                                                                                                             |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `backend/jac/models.py`                                 | `Education.DegreeLevel` + `degree_level` + `is_degree` (line 222).                                              |
| `backend/jac/migrations/0007_education_degree_level.py` | **new** — the migration (0006 is `sections_off`).                                                               |
| `backend/jac/serializers.py`                            | expose the field (`EducationSerializer.Meta.fields`, line 153).                                                 |
| `backend/jac/cv.py`                                     | degree status in the flattened entry text (line 193), `highest_degree_id()`, union it into the pins (line 275). |
| `backend/jac/tasks.py`                                  | **repair** — pass the application's `pinned_entries` into `filter_cv` (line 162).                               |
| `backend/jac/llm_prompts.py`                            | one clause each in `Conversational._INSTRUCTION` (line 140) and `Instruct._INSTRUCTION` (line 233).             |
| `backend/jac/generation_result.py`                      | `_education_label` (line 26) — the run-snapshot label.                                                          |
| `backend/jac/render.py`                                 | `_format_entry` educations branch (line 85) — the `cv_test` markdown artifact.                                  |
| `backend/jac/management/commands/cv_export.py`          | `_educations` (line 179) — or the level vanishes on export.                                                     |
| `backend/jac/management/commands/cv_import.py`          | `_import_educations` (line 343) — the other half of the round trip.                                             |
| `backend/spa/portfolio.py`                              | `_career_item` education branch (line 276) — the public card.                                                   |
| `frontend/src/lib/queries/jac.ts`                       | `EducationRow.degree_level` (line 30).                                                                          |
| `frontend/src/routes/_authenticated/cv/education.tsx`   | schema (line 51), column (line 262), both `initial` branches (line 307), one form field (after line 417).       |
| `frontend/src/lib/cv-doc.ts`                            | `isDegree` helper + `labelFor` (line 119) — the editor's entry label.                                           |
| `frontend/src/lib/render/parts.ts`                      | heading composition (line 130): `(no degree)` instead of "Drop Out".                                            |

**Blast radius (grepped, `degree` across `backend/` + `frontend/src`).** Five label sites compose an
education heading out of the free-text `degree` field, not two: the PDF heading (`parts.ts`), the
editor list (`cv-doc.ts`), the run snapshot (`generation_result.py`), the markdown smoke artifact
(`render.py`), and the **public portfolio card** (`spa/portfolio.py`). Fixing only the first two
leaves "Drop Out Education Physics" on the portfolio and in every `cv_test` artifact — and once you
clean the free-text field by hand (verification step 4), the portfolio would title an unfinished
study period exactly like a degree. All five are in scope. `backend/jac/admin.py:31` also lists
`degree` in `list_display`; adding `degree_level` there is a one-word nicety, not required.

Nothing else reads `Education.degree`. `views.py:186` searches it (unchanged), `cv_export`/
`cv_import` copy it (both extended below so a round trip stops silently resetting every level to 0).

**Two wordings, on purpose.** The heading form is `Physics @ FU Berlin (no degree)` where the string
ends there (`parts.ts`, `render.py`, and the portfolio's short title). The list form is
`… (Oct 2016 – Sep 2020) — no degree` where a date range already closes the string (`cv-doc.ts`,
`generation_result.py`) — a second bracket right after the first reads as a typo. The tests pin both.

## The code

### 1. `backend/jac/models.py` — `Education` (line 222)

```python
class Education(CvEntry):
    """Degree or formal study period."""

    FAVOURITE_LIMIT = 2

    class DegreeLevel(models.IntegerChoices):
        """What this entry EARNED — not what it aimed at. Ordered, so "highest" is a
        max() and not a lookup table; that ordering is what makes the force-keep
        computable. A study period that ended without a qualification is `none`, and the
        level it was aiming at stays in the free-text `degree` / `field_of_study`."""

        none = 0, _("No degree")
        secondary = 1, _("Abitur / High School")
        vocational = 2, _("Vocational / Ausbildung")
        bachelor = 3, _("Bachelor")
        master = 4, _("Master / Diplom / Magister / Staatsexamen")
        doctorate = 5, _("Doctorate")

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
    # `grade` is display data and stays that way: leaving a mediocre grade off the CV
    # must not change what the entry IS.
    grade = models.CharField(max_length=50, null=True, blank=True)
    skills = models.ManyToManyField("Skill", blank=True)
    domains = models.ManyToManyField(Domain, blank=True)

    @property
    def is_degree(self) -> bool:
        return self.degree_level > self.DegreeLevel.none
```

`choices=DegreeLevel` (the class, not `.choices`) matches the house style — see `Skill.proficiency`
at line 275 and `Language.fluency` at line 394. Django 6 accepts it.

Migration:

```bash
cd backend && python manage.py makemigrations jac -n education_degree_level
```

`default=none` is the conservative default: after migrating, **every existing education row claims
nothing** — including the Abitur and the BSc — so every one of them renders "(no degree)" until you
classify them by hand (verification step 4). That is expected, not a bug. The alternative (guessing a
level from the free text) is exactly the ambiguity this guide removes.

### 2. `backend/jac/serializers.py` — `EducationSerializer.Meta.fields` (line 153)

Add `"degree_level"` after `"degree"` (line 159). Nothing else: DRF derives the 0–5 range from the
model choices, which is what the API test's rejection case leans on.

### 3. `backend/jac/cv.py`

**a.** the entry text the selectors score (line 193) — say out loud whether it's a degree, so the
LLM rungs have the fact available at all. Goes **between** the `@ institution (window)` composition
and the existing `if e.description:` line:

```python
        for e in self.entries["educations"]:
            window = f"{e.started or '?'}–{e.ended or 'present'}"
            text = f"{e.degree or ''} {e.field_of_study or ''}".strip()
            text = (
                f"{text} @ {e.institution} ({window})"
                if text
                else f"{e.institution} ({window})"
            )
            # Explicit, because "Drop Out Education Physics" in a free-text degree field is
            # exactly the ambiguity this guide removes.
            text += (
                f" [degree: {e.get_degree_level_display()}]"
                if e.is_degree
                else " [no degree]"
            )
            if e.description:
                text += f" — {e.description[:200]}"
```

**b.** the force-keep, as a method on `CV` directly above `filter_cv` (line 275):

```python
    def highest_degree_id(self) -> str | None:
        """Flat id of the highest degree in this CV's education set, or None.

        German public service grades pay on the highest formal qualification, so it belongs
        on every CV regardless of how it scores against the posting. Highest is max(level),
        NOT the most recent entry and NOT the best grade — ties (two Masters) go to the one
        finished later. A study period that earned nothing is ordinary content the selection
        may rank and drop like anything else.
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

The signature is unchanged — `pinned=None` is already there, it was just passed straight through.
`date` is imported at `cv.py:10`. Note `self.entries` is the **pre-selection** dict (loaded in
`__init__`), so this must be called before `apply_selection` prunes it — which is what step 4 does.

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

`application` is already in scope (line 145).

### 5. `backend/jac/llm_prompts.py` — one clause each

`Conversational._INSTRUCTION` (line 140), inside the bullet list, after the "keep a skill if…" line:

```python
        "  - an entry marked [degree: ...] is a formal qualification and outranks an "
        "unfinished study period at the same institution or in the same field;\n"
```

`Instruct._INSTRUCTION` (line 233), after the `0 = not relevant` line and before the "Output ONE
line per entry" line:

```python
        "An entry marked [degree: ...] is a formal qualification — rate it at least 2 "
        "unless it is entirely unrelated to the posting.\n"
```

Both are advisory. The pin is what actually guarantees the outcome — do not weaken the pin because
the prompt "should" handle it.

### 6. `backend/jac/generation_result.py` — `_education_label` (line 26)

The label the SPA stores with a run. `content-card.tsx:451` prefers the live career row and falls
back to this string, so it has to agree with `labelFor` word for word:

```python
def _education_label(e) -> str:
    window = f"{e.started or '?'}–{e.ended or 'present'}"
    head = f"{e.degree or ''} {e.field_of_study or ''}".strip()
    label = (
        f"{head} @ {e.institution} ({window})"
        if head
        else f"{e.institution} ({window})"
    )
    # The list form: the string already ends in a bracketed range.
    return label if e.is_degree else f"{label} — no degree"
```

### 7. `backend/jac/render.py` — `_format_entry`, educations branch (line 85)

The markdown `cv_test` writes next to `findings.md`:

```python
        if kind == "educations":
            label = " ".join(p for p in (e.degree, e.field_of_study) if p).strip()
            heading = f"{label} @ {e.institution}" if label else e.institution
            if not e.is_degree:
                heading = f"{heading} (no degree)"
```

(the rest of the branch is unchanged).

### 8. the JSON round trip

`backend/jac/management/commands/cv_export.py`, `_educations` (line 179) — after `"degree"`:

```python
                "degree_level": e.degree_level,
```

`backend/jac/management/commands/cv_import.py`, `_import_educations` (line 343) — after `degree=`:

```python
                degree_level=item.get("degree_level", 0),
```

Without both halves an export→import cycle silently resets every level to 0 — i.e. it undoes exactly
the hand-classification of verification step 4. The importer's docstring field list (line 23) gets
the name too.

### 9. `backend/spa/portfolio.py` — `_career_item`, education branch (line 276)

```python
    elif type_name == "education":
        title = obj.degree or obj.field_of_study
        item.update(
            # The public card is read by strangers with no context: once "Drop Out" is out
            # of the free-text field, an unfinished period would look like a degree.
            title=title if obj.is_degree else f"{title} (no degree)",
            subtitle=obj.institution,
            started=obj.started,
            ended=obj.ended,
        )
```

### 10. `frontend/src/lib/queries/jac.ts` — `EducationRow` (line 30)

```ts
degree: string | null;
/** 0–5, see Education.DegreeLevel. Plain `number`, NOT a literal union — see below. */
degree_level: number;
```

⚠️ **Not `0 | 1 | 2 | 3 | 4 | 5`.** The row type and the route's zod schema have to _infer the same
type_, because `useLineSave` → `useUpdate<EducationRow>` takes `Partial<EducationRow>` as its body
(`jac.ts:234`). That's why the string enums pair up the way they do — `LanguageRow.fluency` is a
union _and_ its schema is `z.enum([...])`, which infers exactly that union. A literal union here
paired with `z.number()` does not:

```
Type 'number' is not assignable to type '0 | 1 | 2 | 3 | 4 | 5 | undefined'.
```

Plain `number` is also the house shape for every other numeric field (`location`, `skills`), and
the range is enforced twice anyway — `z.number().int().min(0).max(5)` on the form, DRF's choices on
the server. Nothing in the SPA switches exhaustively on the level, so the union buys no safety and
costs a cast at every form boundary.

_(If you'd rather keep the union: zod 4 has `z.literal([0, 1, 2, 3, 4, 5])`, which infers it — but
then `f.handleChange(Number(v))` in the Select needs `as EducationRow["degree_level"]`.)_

### 11. `frontend/src/routes/_authenticated/cv/education.tsx`

**a.** the schema (line 51, after `degree`): `degree_level: z.number().int().min(0).max(5),`.

**b.** near the schema, the labels — one source for the Select and the table column:

```tsx
const DEGREE_LEVELS: [number, string][] = [
  [0, "No degree"],
  [1, "Abitur / High School"],
  [2, "Vocational / Ausbildung"],
  [3, "Bachelor"],
  [4, "Master / Diplom"],
  [5, "Doctorate"],
];
const DEGREE_LABELS = Object.fromEntries(DEGREE_LEVELS) as Record<
  number,
  string
>;
```

**c.** a column next to `degree` (line 262):

```tsx
    col.accessor("degree_level", {
      header: "Level",
      cell: (c) => DEGREE_LABELS[c.getValue()] ?? "—",
    }),
```

**d.** **both** branches of the `initial` ternary (line 307 for the edit case, line 321 for the
create case) — `degree_level: row.degree_level ?? 0,` and `degree_level: 0,`. Miss the second one
and TanStack Form types the field as `never`.

**e.** the field, right after the `degree` field block (ends line 417):

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
        What you earned, not what you studied toward. "No degree" renders as
        "(no degree)"; your highest is pinned onto every generated CV.
      </p>
      <LineSaveHint s={line.fields[f.name]} />
    </div>
  )}
</form.Field>
```

`Select`/`SelectContent`/`SelectItem`/`SelectTrigger`/`SelectValue` come from
`@/components/ui/select` — copy the import block from `cv/languages.tsx:35`, whose `fluency` field
(line 385) is the same shape. `Label` is already imported here.

### 12. `frontend/src/lib/cv-doc.ts` — the shared predicate + `labelFor` (line 119)

`parts.ts` already imports from this module (`parts.ts:6`), so the predicate lives here once:

```ts
/** One ordered field, one meaning: anything above `none` is a formal qualification. */
export const isDegree = (e: EducationRow) => e.degree_level > 0;
```

and the educations branch of `labelFor` takes the em-dash form (the label already ends in a date
range):

```ts
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const w = dateRange(e.started, e.ended);
      const base = head
        ? `${head} @ ${e.institution} (${w})`
        : `${e.institution} (${w})`;
      return isDegree(e) ? base : `${base} — no degree`;
    }
```

### 13. `frontend/src/lib/render/parts.ts` — the printed heading (line 130)

```ts
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const base = head ? `${head} @ ${e.institution}` : e.institution;
      const d = dateParts(e.started, e.ended);
      return {
        heading: isDegree(e) ? base : `${base} ${NO_DEGREE}`,
        …
```

with `isDegree` added to the existing `@/lib/cv-doc` import block (line 6) and, at module scope:

```ts
/** Unfinished study periods say so plainly. It is honest, it is normal on a German CV,
 *  and it beats whatever the user typed into the free-text degree field. */
export const NO_DEGREE = "(no degree)";
```

## Tests

On disk and red — the `@skip` / `.skip` markers were dropped at activation, so
`python manage.py test jac spa` fails until the code lands. That is the intended state.

| file                                           | class / block                                         | covers                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/jac/tests/test_models.py`             | `EducationDegreeTests` (4)                            | the six levels are ordered; the default claims nothing; every level above `none` is a degree (Abitur included); free text never overrides the level.                                                                                                                                                        |
| `backend/jac/tests/test_pipeline.py`           | `HighestDegreeTests` (7)                              | `highest_degree_id` picks the max level, is not stolen by a _later_ drop-out, falls to the Abitur when that's all there is, breaks a tie on the later end date, returns None with no degree at all; `filter_cv` unions it with the caller's pins; the flattened text carries `[degree: …]` / `[no degree]`. |
| `backend/jac/tests/test_pipeline.py`           | `DegreeLabelTests` (3)                                | the two server label surfaces: `serialize_cv_selection` and `CvRender.export_md`.                                                                                                                                                                                                                           |
| `backend/jac/tests/test_api.py`                | `EducationDegreeApiTests` (4)                         | default on create, the level round-trips through PATCH, out-of-range is a 400, **a grade alone does not make a degree**.                                                                                                                                                                                    |
| `backend/spa/tests/test_portfolio.py`          | `CareerItemDegreeTests` (2)                           | the public card marks a `none` entry and leaves a degree alone.                                                                                                                                                                                                                                             |
| `frontend/tests/lib/cv-doc.test.ts`            | `labelFor — unfinished education` (3)                 | the editor list form (`— no degree`), incl. the grade-is-not-a-signal guard.                                                                                                                                                                                                                                |
| `frontend/tests/lib/render-typography.test.ts` | `entryParts — an unfinished study period says so` (3) | the printed heading form (`(no degree)`) + the date column is untouched.                                                                                                                                                                                                                                    |

```bash
cd backend && python manage.py test jac.tests.test_models jac.tests.test_pipeline jac.tests.test_api spa.tests.test_portfolio
cd frontend && npx vitest run tests/lib/cv-doc.test.ts tests/lib/render-typography.test.ts
```

**Red state at activation** (2026-08-07): all 20 backend tests error on `Education.DegreeLevel` not
existing — the honest shape of "the field isn't there yet". Frontend: 3 of the 6 fail on the missing
marker; the other 3 pass already and are there as regression guards (a degree must _not_ pick up a
suffix, and the date column must stay out of it).

## Verification

1. `python manage.py makemigrations jac -n education_degree_level && python manage.py migrate`.
2. Backend + frontend suites above: red → green. Then the full suites (`python manage.py test`,
   `npx vitest run`) — nothing hardcoded the old education label at activation time, so a red one
   elsewhere is a real regression.
3. **Expect every education entry to read "(no degree)" right after the migration.** Everything
   defaults to `none`; step 4 is what fixes it. Don't debug this.
4. **Classify your own data** (the half no code can do): CV → Education, set **Degree level** on all
   five rows — Abitur → _Abitur / High School_, Honorary HS Diploma → _Abitur / High School_,
   Bachelor of Science → _Bachelor_, both TU/FU rows → _No degree_. While you're there, take the
   words "Drop Out" out of the free-text Degree field on the last two (the heading composes the
   marker now) and leave the aimed-at level in `field_of_study` where it already is
   ("Physics (Master)").
5. Generate a CV against a posting that has nothing to do with physics. The BSc must appear, with a
   pin icon in the editor. The drop-outs may or may not — that's the point.
6. Unpin the BSc in the editor, save, re-generate: it may now be dropped. The pin is an override,
   not a lock.
7. Export: the education entries read `Bachelor of Science Physics @ MLU Halle-Wittenberg` and
   `Physics (Master) @ TU Berlin (no degree)` — no "Drop Out" anywhere.
8. **Pin repair check** (independent of degrees): pin any job entry, save, hit Generate again — it
   survives the new run. Before this guide it did not.
9. **The other four surfaces**, quickly: the run snapshot label in the editor list (a generated run,
   before applying), `python manage.py cv_test …`'s markdown artifact, `cv_export` → `cv_import`
   round trip on your own user (levels survive), and the public portfolio card for an education
   entry.

## Results

<!-- human: raw test output, observed issues, what works -->

the useForm function in @frontend/src/routes/\_authenticated/cv/education.tsx has a type error now

````Type '{ ended: string | null; institution: string; field_of_study: string; started: string; degree: string; grade: string; degree_level: number; completed: boolean; description: string; location: number | null; skills: number[]; domains: number[]; favourite: boolean; }' is not assignable to type 'Partial<EducationRow>'.
  Types of property 'degree_level' are incompatible.
    Type 'number' is not assignable to type '0 | 1 | 2 | 3 | 4 | undefined'.
jac.ts(234, 46): The expected type comes from property 'body' which is declared here on type '{ id: number; body: Partial<EducationRow>; }'```
````

-->solved

tests:

```⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/3]⎯

 FAIL  tests/lib/cv-doc.test.ts > labelFor — unfinished education > leaves a degree untouched
AssertionError: expected 'BSc CS @ TU (Sep 2015 – Aug 2018) — n…' to be 'BSc CS @ TU (Sep 2015 – Aug 2018)' // Object.is equality

Expected: "BSc CS @ TU (Sep 2015 – Aug 2018)"
Received: "BSc CS @ TU (Sep 2015 – Aug 2018) — no degree"

 ❯ tests/lib/cv-doc.test.ts:438:71
    436|
    437|   it("leaves a degree untouched", () => {
    438|     expect(labelFor("educations", { ...education, degree_level: 3 })).toBe(
       |                                                                       ^
    439|       "BSc CS @ TU (Sep 2015 – Aug 2018)",
    440|     );

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/3]⎯

 FAIL  tests/lib/render-typography.test.ts > entryParts — an unfinished study period says so > leaves a degree's heading alone
AssertionError: expected 'B.Sc. CS @ TU (no degree)' to be 'B.Sc. CS @ TU' // Object.is equality

Expected: "B.Sc. CS @ TU"
Received: "B.Sc. CS @ TU (no degree)"

 ❯ tests/lib/render-typography.test.ts:250:58
    248|
    249|   it("leaves a degree's heading alone", () => {
    250|     expect(entryParts(eduDb, "educations", bsc).heading).toBe("B.Sc. CS @ TU");
       |                                                          ^
    251|   });
    252|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[3/3]⎯


 Test Files  2 failed | 32 passed | 3 skipped (37)
      Tests  3 failed | 424 passed | 40 skipped (467)
   Start at  16:34:27
   Duration  4.24s (transform 4.31s, setup 0ms, import 11.13s, tests 5.89s, environment 30ms)

lukas@localhost frontend %
```
