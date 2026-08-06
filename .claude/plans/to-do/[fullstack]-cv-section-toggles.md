# [fullstack] CV section toggles

> Roadmap: **CV-filter phase, item 2** — "could we add a bool to the cv form in the frontend for the
> sections. this way entire sections can be removed with one click. removing section should result
> in more possible entries in the other sections."
> Branch: `fullstack/cv-section-toggles`

## Context / goal

Some postings don't want a Certifications block, or Languages, or Projects. Today the only way to
suppress a section is to deselect every entry in it one by one — and even then the section header
logic in the layout still reserves it.

This guide adds a per-application, per-section on/off switch, and makes the switch *pay*: turning a
section off releases its share of the layout's entry budget to the sections that remain.

Two design points worth stating before the code:

**Where the flag lives.** `cv_content` is `{section: CvEntry[]}` — there is no room for a
section-level flag without inventing a magic key inside the entry map. So it becomes its own field
on `JobApplication`, next to `pinned_entries`: `sections_off`, a list of section keys.

**How the budget moves.** Not one-entry-for-one-entry — a job entry is ~5 lines and a skill is a
fraction of one, so trading 4 certification slots for 4 job slots would overflow the page instantly.
Instead the caps are *scaled*: each section carries a rough line weight, switching sections off
frees weight, and the remaining sections' caps grow by the freed proportion (clamped at 2×). The
actual page fit still has the final word — that's guide `[frontend]-fit-preflight`, which spends the
freed space; this guide only stops the caps from being the binding constraint.

**Honesty note.** A section the user switched off is *not* `cut_for_space` — it was cut on purpose.
It must therefore disappear from the invisible-ink layer too, not get filed under the space-cut
list. Filtering at `activeContent` (before `full` is computed in `export-card.tsx:111`) gets that
for free.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/models.py` | `JobApplication.sections_off`. |
| `backend/jac/migrations/00XX_jobapplication_sections_off.py` | **new**. |
| `backend/jac/serializers.py` | field + `validate_sections_off`. |
| `frontend/src/lib/cv-doc.ts` | `activeContent` takes the off-list; `toggleSection`. |
| `frontend/src/lib/render/fit.ts` | `SECTION_WEIGHT` + `effectiveCaps`. |
| `frontend/src/lib/queries/applications.ts` | `ApplicationRow.sections_off`. |
| `frontend/src/components/applications/content-card.tsx` | the switch, the draft state, the save. |
| `frontend/src/components/applications/export-card.tsx` | filter + effective caps on export. |

## The code

### 1. `backend/jac/models.py` — `JobApplication` (after line 490)

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

### 3. `frontend/src/lib/cv-doc.ts`

`activeContent` grows a second argument. Every caller passes it — the default keeps the pure
"strip deselected" behaviour for tests and any call that genuinely has no application context:

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

Add next to `capContent`:

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
  const growth =
    kept > 0 ? Math.min(1 + freed / kept, MAX_CAP_GROWTH) : 1;
  const out: Record<string, number> = {};
  for (const [section, cap] of Object.entries(maxEntries)) {
    if (off.has(section)) continue;
    out[section] = Math.max(1, Math.round(cap * growth));
  }
  return out;
}
```

### 5. `frontend/src/lib/queries/applications.ts`

`ApplicationRow` gains `sections_off: string[];` (next to `pinned_entries`, line 64).

### 6. `frontend/src/components/applications/content-card.tsx`

**a.** draft state, next to the other drafts (near line 78):

```tsx
  const [sectionsOff, setSectionsOff] = useState<string[]>(app.sections_off ?? []);
```

**b.** the dirty check (line 134) gains
`|| JSON.stringify(sectionsOff) !== JSON.stringify(app.sections_off ?? [])`.

**c.** the save body (line 156) gains `sections_off: sectionsOff,`.

**d.** effective caps instead of raw ones (line 187):

```tsx
  // Switching a section off loosens the others — show the caps the export will use, not
  // the template's untouched numbers.
  const maxEntries = effectiveCaps(spec.data?.cv.max_entries ?? {}, sectionsOff);
  const overCap = overCapIds(activeContent(cvDraft, sectionsOff), maxEntries);
```

**e.** pass the flag down (line 240):

```tsx
                off={sectionsOff.includes(section)}
                onToggleSection={() =>
                  setSectionsOff((s) => toggleSection(s, section))
                }
```

**f.** `CvEditorSection` — two new props and the switch in the header:

```tsx
function CvEditorSection({
  section,
  entries,
  db,
  onEdit,
  cap,
  overIds,
  freshIds,
  off,
  onToggleSection,
}: {
  …
  off: boolean;
  onToggleSection: () => void;
}) {
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
    </div>
  );
}
```

`Checkbox` is already imported in this file's sibling routes — add
`import { Checkbox } from "@/components/ui/checkbox";` here.

### 7. `frontend/src/components/applications/export-card.tsx`

Line 111–112:

```tsx
    const off = app.sections_off ?? [];
    const full = activeContent(app.cv_content ?? {}, off);
    const active = capContent(full, effectiveCaps(s.cv.max_entries, off));
```

and the same two lines in `onDownloadMd` (line 258). `full` is what feeds `hiddenPayload`, so a
switched-off section is now absent from the invisible layer too — which is the point.

### 8. Thread the off-list through the preflight

`[frontend]-fit-preflight` shipped **without** this guide (its dependency was only `effectiveCaps`,
which it works around by using `spec.cv.max_entries` directly). Four small edits close the loop —
without them a switched-off section frees budget on paper but the page fit never spends it, which is
the whole promise of the toggle:

1. `use-preflight.ts` — the hook's args gain `sectionsOff: string[]`; it passes
   `effectiveCaps(spec.cv.max_entries, sectionsOff)` to `fitContent` instead of
   `spec.cv.max_entries`, and `sectionsOff` into `preflightKey` (the key already accepts the field).
2. `content-card.tsx` — pass `sectionsOff` to the hook, and `activeContent(cvDraft, sectionsOff)` as
   its `content`.
3. `export-card.tsx` — the same two swaps inside `buildPdf` (`effectiveCaps(s.cv.max_entries, off)`
   in the `fitContent` call, `sectionsOff: off` in the `preflightKey` call). Both sides must build
   the key identically or the export silently re-measures on every download.
4. Verify: switching a section off makes the editor's status line report *more* entries added.

## Tests

**Step 0 — unskip.** Delete the `@skip` in the backend file and every `.skip` in the frontend ones.

| file | covers |
| --- | --- |
| `backend/jac/tests/test_api.py` | `sections_off` round-trips, dedupes, rejects unknown keys and non-lists. |
| `frontend/tests/lib/cv-doc.test.ts` | `activeContent` drops a whole switched-off section while still stripping deselected entries elsewhere; the default argument preserves the old behaviour; `toggleSection` adds/removes immutably. |
| `frontend/tests/lib/render-fit.test.ts` | `effectiveCaps`: identity with nothing off; the off section disappears from the result; remaining caps grow; growth is weight-based (turning off `skills`, worth 18 lines, moves the caps more than turning off `certifications`, worth 8); clamped at 2×; never returns a cap below 1. |

```bash
cd backend && python manage.py test jac.tests.test_api
cd frontend && npx vitest run tests/lib/cv-doc.test.ts tests/lib/render-fit.test.ts
```

## Verification

1. Migrate, run both suites red → green, `npx tsc -b`.
2. Open an application with certifications in the CV. The Certifications header now has a ticked
   checkbox and reads `3/4 in the layout`.
3. Untick it: the section greys out and collapses, and **the other sections' counters change** —
   e.g. Experience goes from `4/5` to `4/6`. Save.
4. Reload: the section is still off (it round-tripped through `sections_off`).
5. Preview the PDF: no Certifications block. Download the JSON export and search the invisible-ink
   payload — the certifications must **not** appear there either, and must **not** be listed under
   `cut_for_space` (they weren't cut for space).
6. Untick three sections at once and confirm the growth stops at 2× rather than exploding.
7. `PATCH /api/jac/applications/<pk>/ {"sections_off": ["certification"]}` (singular — a plausible
   typo) → 400 `Unknown sections: ['certification']`.

## Results

<!-- human: raw test output, observed issues, what works -->
