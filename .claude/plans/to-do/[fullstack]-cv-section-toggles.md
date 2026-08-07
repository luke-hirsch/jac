# [fullstack] CV section toggles

> Roadmap: **CV-filter phase, item 2** — "could we add a bool to the cv form in the frontend for the
> sections. this way entire sections can be removed with one click. removing section should result
> in more possible entries in the other sections."
> Branch: `fullstack/cv-section-toggles`
> **Activated 2026-08-07** — contracts verified against the code, tests on disk and red
> (5 backend, 8 frontend).
> Order: implement **after** `[frontend]-fit-preflight`'s round-1 follow-up is green. This guide
> edits the same three frontend files and assumes the preflight is the thing that measures the page.

## Context / goal

Some postings don't want a Certifications block, or Languages, or Projects. Today the only way to
suppress a section is to deselect every entry in it one by one — and even then the layout still
reserves the section header.

This guide adds a per-application, per-section on/off switch, and makes the switch *pay*: turning a
section off releases its share of the layout's entry budget to the sections that remain.

Three design points before the code:

**Where the flag lives.** `cv_content` is `{section: CvEntry[]}` — there is no room for a
section-level flag without inventing a magic key inside the entry map. So it becomes its own field
on `JobApplication`, next to `pinned_entries`: `sections_off`, a list of section keys.

**How the budget moves.** Not one-entry-for-one-entry — a job entry is ~5 lines and a skill is a
fraction of one, so trading 4 certification slots for 4 job slots would overflow the page instantly.
Instead the caps are *scaled*: each section carries a rough line weight, switching sections off
frees weight, and the remaining sections' caps grow by the freed proportion (clamped at 2×). The
actual page fit still has the final word — `fitContent` spends the freed space and reports what it
could not use; this guide only stops the caps from being the binding constraint.

**A switched-off section is not `cut_for_space`.** It was cut on purpose. So it must disappear from
the invisible-ink layer too, rather than being filed under the space-cut list — and out of the JSON
export, which is a dump of *this CV*, not of the career DB. Filtering at `activeContent`, before
`full` is computed, gets both for free.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/models.py` | `JobApplication.sections_off` (after line 490). |
| `backend/jac/migrations/0006_jobapplication_sections_off.py` | **new**. |
| `backend/jac/serializers.py` | field (after line 605) + `validate_sections_off` (next to line 662). |
| `frontend/src/lib/cv-doc.ts` | `activeContent` takes the off-list; `toggleSection`. |
| `frontend/src/lib/render/fit.ts` | `SECTION_WEIGHT`, `MAX_CAP_GROWTH`, `effectiveCaps`. |
| `frontend/src/lib/queries/applications.ts` | `ApplicationRow.sections_off` (line 64). |
| `frontend/src/components/applications/content-card.tsx` | the switch, the draft state + re-seed, the save, effective caps. |
| `frontend/src/components/applications/use-preflight.ts` | the off-list reaches the measurement. |
| `frontend/src/components/applications/export-card.tsx` | filter + effective caps on export, md and json. |

**Blast radius.** `activeContent` grows a defaulted second parameter — every existing call keeps
working. `effectiveCaps` is new. The only behavioural change to an untouched path is the JSON export
(it now respects the off-list, deliberately). No migration risk: a JSONField with `default=list` on
an existing table.

## The code

### 1. `backend/jac/models.py` — `JobApplication` (after `pinned_entries`, line 490)

```python
    # Sections the user switched off for THIS application (["certifications", …]).
    # Not "everything deselected": a section that is off releases its slice of the
    # layout's entry budget to the sections that stay, and never reaches the export —
    # including the machine-readable layer, because it was cut on purpose, not for space.
    sections_off = models.JSONField(default=list, blank=True)
```

```bash
cd backend && python manage.py makemigrations jac -n jobapplication_sections_off
```

Note what this deliberately does **not** touch: the pipeline still selects entries for a
switched-off section, and a fresh run still fills it. The flag is a *rendering* decision on one
application, not a filter on the career DB — flip it back on and the content is still there.

### 2. `backend/jac/serializers.py` — `JobApplicationSerializer`

Add `"sections_off"` to `fields` (after `"pinned_entries"`, line 605) and the validator next to
`validate_pinned_entries` (line 662):

```python
    _SECTIONS = frozenset(
        ("jobs", "educations", "projects", "skills", "certifications", "languages")
    )

    def validate_sections_off(self, value):
        """A list of known section keys, deduped. Unknown keys are rejected rather than
        ignored: a typo that silently does nothing is worse than a 400."""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError("Expected a list of section names.")
        unknown = sorted(set(value) - self._SECTIONS)
        if unknown:
            raise serializers.ValidationError(f"Unknown sections: {unknown}")
        return list(dict.fromkeys(value))
```

The keys are the **plural** `cv_content` section names — `"certification"` is the plausible typo and
the tests pin that it 400s.

### 3. `frontend/src/lib/cv-doc.ts`

`activeContent` (line 232) grows a second argument. The default keeps the pure "strip deselected"
behaviour for the calls that genuinely have no application context:

```ts
/** Deselected entries stripped, and switched-off sections dropped whole. Both are
 *  "not on the CV", but for different reasons — see JobApplication.sections_off. */
export function activeContent(
  content: CvContent,
  sectionsOff: string[] = [],
): CvContent {
  const off = new Set(sectionsOff);
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    if (off.has(section)) continue;
    out[section] = list.filter((e) => !e.deselected);
  }
  return out;
}

/** Immutable toggle for the editor's section switch. */
export function toggleSection(sectionsOff: string[], section: string): string[] {
  return sectionsOff.includes(section)
    ? sectionsOff.filter((s) => s !== section)
    : [...sectionsOff, section];
}
```

### 4. `frontend/src/lib/render/fit.ts` — the budget redistribution

Next to `capContent`:

```ts
/**
 * Rough vertical cost of one entry in each section, in "lines". Only the ratios matter —
 * they convert an entry budget into a page-space budget, which is the thing a switched-off
 * section actually frees. A job is a heading + meta + a few description lines; a skill is a
 * fraction of one joined sidebar line.
 */
export const SECTION_WEIGHT: Record<string, number> = {
  jobs: 5,
  educations: 4,
  projects: 4,
  certifications: 2,
  skills: 1,
  languages: 1,
};
const weight = (section: string) => SECTION_WEIGHT[section] ?? 2;

/** Growth is clamped: one toggle should loosen the layout, not abolish it. */
export const MAX_CAP_GROWTH = 2;

/**
 * The template's per-section caps, with the weight freed by switched-off sections spread
 * over the sections that remain, proportionally to what they already are. Switched-off
 * sections drop out of the result entirely.
 *
 * Deliberately NOT one-slot-for-one-slot: 4 certification slots are worth ~8 lines, which
 * is one and a half jobs, not four.
 */
export function effectiveCaps(
  maxEntries: Record<string, number>,
  sectionsOff: string[] = [],
): Record<string, number> {
  const off = new Set(sectionsOff);
  let freed = 0;
  let kept = 0;
  for (const [section, cap] of Object.entries(maxEntries)) {
    const w = cap * weight(section);
    if (off.has(section)) freed += w;
    else kept += w;
  }
  const growth = kept > 0 ? Math.min(1 + freed / kept, MAX_CAP_GROWTH) : 1;
  const out: Record<string, number> = {};
  for (const [section, cap] of Object.entries(maxEntries)) {
    if (off.has(section)) continue;
    out[section] = Math.max(1, Math.round(cap * growth));
  }
  return out;
}
```

Integer caps round, so a small release can be invisible on a small section (turning skills off and
turning certifications off both take `jobs` from 5 to 6). That is honest behaviour, not a bug — the
page fit is what spends the difference. The test reads the claim on `projects`, where it shows.

### 5. `frontend/src/lib/queries/applications.ts`

`ApplicationRow` gains `sections_off: string[];` next to `pinned_entries` (line 64), and the
patch/update body type gains `sections_off?: string[];` next to its `pinned_entries?` (line 75).

### 6. `frontend/src/components/applications/content-card.tsx`

**a.** draft state, next to `cvDraft` (line 88):

```tsx
  const [sectionsOff, setSectionsOff] = useState<string[]>(app.sections_off ?? []);
```

**b.** the server re-seed (lines 98–124) must carry it too, or applying a run silently reverts the
switch in the UI while the server still has it. Add `const serverOff = JSON.stringify(app.sections_off ?? []);`
next to `serverCv`, `off: serverOff` to the `prevServer` state and to the `setPrevServer` call,
`prevServer.off !== serverOff` to the `if`, and inside it:

```tsx
    setSectionsOff(app.sections_off ?? []);
```

**c.** the dirty check (line 140) gains

```tsx
    JSON.stringify(sectionsOff) !== serverOff ||
```

**d.** the save body (line 162) gains `sections_off: sectionsOff,`.

**e.** effective caps instead of raw ones (line 191) — and the preflight measures the content the
switch leaves behind:

```tsx
  // Switching a section off loosens the others — show the caps the export will use, not
  // the template's untouched numbers.
  const maxEntries = effectiveCaps(spec.data?.cv.max_entries ?? {}, sectionsOff);
```

and in the `usePreflight({…})` call (line 195): `content: activeContent(cvDraft, sectionsOff),`
plus a new `sectionsOff,` argument.

**f.** pass the flag down in the section map (line 258):

```tsx
                off={sectionsOff.includes(section)}
                onToggleSection={() =>
                  setSectionsOff((s) => toggleSection(s, section))
                }
```

**g.** `CvEditorSection` — two new props on top of the ones the preflight added, and the switch in
the header (line 325 onwards):

```tsx
  off: boolean;
  onToggleSection: () => void;
```

```tsx
  const missing = missingEntries(db, section, entries);
  if (entries.length === 0 && missing.length === 0) return null;
  const active = entries.filter((e) => !e.deselected).length;
  const over = !off && cap != null && active > cap;
  return (
    <div className={off ? "opacity-50" : undefined}>
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Checkbox
          checked={!off}
          onCheckedChange={onToggleSection}
          aria-label={`include ${SECTION_TITLES[section]}`}
        />
        {SECTION_TITLES[section]}
        {off ? (
          <span className="text-xs font-normal text-muted-foreground">
            not on this CV — its budget goes to the other sections
          </span>
        ) : (
          cap != null &&
          entries.length > 0 && (
            <span
              className={`text-xs font-normal ${
                over ? "text-amber-600" : "text-muted-foreground"
              }`}
            >
              {active}/{cap} in the layout
            </span>
          )
        )}
      </h3>
      {!off && (
        <ul className="space-y-1">
          {/* …unchanged… */}
        </ul>
      )}
      {/* the add-picker below stays inside the `!off` branch too */}
    </div>
  );
```

The old `<h3 className="text-sm font-semibold">` becomes the flex row above, and the `ml-2` on the
counter span goes (the flex `gap` does it now). Add
`import { Checkbox } from "@/components/ui/checkbox";`.

### 7. `frontend/src/components/applications/use-preflight.ts`

The hook's args gain `sectionsOff: string[];`, and two lines inside change so the measurement
matches the export:

```ts
        spec,
        content,
        sectionsOff,
        cvHeader: { name, contact, summary },
```

```ts
          const result = await fitContent(
            content,
            effectiveCaps(spec.cv.max_entries, sectionsOff),
            spec.cv.detailed,
```

`preflightKey` already accepts `sectionsOff` — it was left there for exactly this. Import
`effectiveCaps` alongside `fitContent`.

### 8. `frontend/src/components/applications/export-card.tsx`

`buildPdf` (line 103): the off-list filters the content and shapes the caps, and it goes into the
key — both sides must build the key identically or every download re-measures:

```tsx
    const off = app.sections_off ?? [];
    const full = activeContent(app.cv_content ?? {}, off);
```

```tsx
      preflightKey({
        spec: s,
        content: full,
        sectionsOff: off,
        cvHeader: { name, contact, summary },
```

and the `fitContent` call's second argument (line 119) becomes
`effectiveCaps(s.cv.max_entries, off)`.

`full` is what feeds `hiddenPayload`, so a switched-off section is now absent from the invisible
layer too — which is the point.

`onDownloadMd` (line 275) gets the same two swaps:

```tsx
      ? capContent(
          activeContent(app.cv_content ?? {}, app.sections_off ?? []),
          effectiveCaps(spec.data.cv.max_entries, app.sections_off ?? []),
        )
      : activeContent(app.cv_content ?? {}, app.sections_off ?? []);
```

and `onDownloadJson` (line 293) passes the off-list too:

```tsx
        content: activeContent(app.cv_content ?? {}, app.sections_off ?? []),
```

JSON stays a *full* dump in the sense that it skips the layout caps — but a switched-off section is
not layout, it is content the user removed from this application. It has no business in any export.

## Tests

**Step 0 is done** — the three skip-marked blocks are unskipped and red.

| file | covers |
| --- | --- |
| `backend/jac/tests/test_api.py` (`SectionsOffApiTests`, 5) | defaults to `[]`, round-trips + dedupes, rejects unknown keys (`"certification"`, `"hobbies"`), rejects a bare string, can be cleared again. |
| `frontend/tests/lib/cv-doc.test.ts` (2 red of 4) | `activeContent` drops a switched-off section whole while still stripping deselected entries elsewhere; the one-argument call keeps its old behaviour; `toggleSection` adds/removes immutably. |
| `frontend/tests/lib/render-fit.test.ts` (6) | `effectiveCaps`: identity with nothing off, the off section gone from the result, remaining caps grow, growth is weight-based (skills' 18 lines release more than certifications' 8), clamped at 2×, never below 1. |

```bash
cd backend && python manage.py test jac.tests.test_api.SectionsOffApiTests
cd frontend && npx vitest run tests/lib/cv-doc.test.ts tests/lib/render-fit.test.ts
```

**Red set at activation.** Backend: `Ran 5 tests … FAILED (failures=2, errors=3)` — all
`SectionsOffApiTests`. Frontend: `20 failed | 399 passed | 42 skipped`, of which **8 are this
guide** (2 cv-doc + 6 effectiveCaps); the other 12 are `[frontend]-fit-preflight`'s round-1
follow-up, which lands first. After that follow-up is green, this guide's red set is exactly 8 + 5.

## Verification

1. Migrate; both suites red → green; `npx tsc -b`.
2. Open an application with certifications in the CV. The Certifications header now carries a ticked
   checkbox and reads `3/4 in the layout`.
3. Untick it: the section greys out and collapses, and **the other sections' counters change** —
   e.g. Experience goes from `4/5` to `4/6`. Save.
4. Reload: the section is still off (it round-tripped through `sections_off`).
5. The editor's status line should report *more* entries added to fill the page than before the
   toggle — that is the freed budget actually being spent, and the whole promise of the feature.
6. Preview the PDF: no Certifications block, and — with the preflight follow-up's sidebar pairing in
   — Languages sits full width rather than beside an empty column.
7. Download the JSON export and search it: the certifications must not appear, and must **not** be
   listed under `cut_for_space` in the invisible-ink payload (they weren't cut for space).
8. Apply a generation run while a section is off: the switch must survive (that is step 6b's
   re-seed), and the section's entries must still be in `cv_content` when you switch it back on.
9. Untick three sections at once and confirm the growth stops at 2× rather than exploding.
10. `PATCH /api/jac/applications/<pk>/ {"sections_off": ["certification"]}` (singular — the
    plausible typo) → 400 `Unknown sections: ['certification']`.

## Results

<!-- human: raw test output, observed issues, what works -->
