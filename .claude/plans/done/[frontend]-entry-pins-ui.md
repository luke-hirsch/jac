# [frontend] entry-pins-ui

> **SPA phase, guide 4 — ACTIVATED 2026-07-18** (contracts verified against code; red
> tests on disk, skip-marked). Branch: `frontend/entry-pins-ui`.
>
> **Scope shrank at activation.** The stub specced pin toggles, pinned styling, and a
> merge-on-apply helper — all of that is **already typed and green-tested**
> (`togglePin`, pin icons in `content-card.tsx:325-375`, `mergePinned` +
> `runToApplicationPatch(result, currentCv)` wired in `generate-panel.tsx:129`). The
> real gap, verified 2026-07-18: **the pin set never leaves the browser.**
> `ApplicationRow`/`ApplicationPatch` have no `pinned_entries`, so the server field
> stays `[]` and the next run force-keeps nothing — the whole backend guarantee
> (`CVFilter` force-keep, `pinned`/`warning` result rows) runs on an empty set.

## Verified — already landed (do not re-implement)

| Piece | Where | State |
| --- | --- | --- |
| `JobApplication.pinned_entries` + PATCH validation (shape / ownership / cap 50 / de-dupe) | `jac/models.py:532`, `jac/serializers.py:685` | landed + tested (`PinnedEntriesApiTests`) |
| Every rung force-keeps pins; only `high` warns; stale ids tolerated | `jac/filter.py`, warning text `CVFilter._PIN_WARNING` | landed + tested (`PinnedSelectionTests`) |
| Result rows carry `pinned` + `warning` | `jac/generation_result.py:71-72` | landed |
| Pin toggle + pinned styling per entry row | `content-card.tsx` (Pin/PinOff buttons, sky pin icon) | landed |
| `togglePin` / `mergePinned` (run copy wins for re-selected pins, dropped pins re-append at the section tail, no-pins = passthrough) | `lib/cv-doc.ts:184,204` | landed + tested |
| Apply merges pins | `runToApplicationPatch(result, app.cv_content)` in `generate-panel.tsx:129` | landed + tested |
| `CvEntry.warning?: string` | `lib/queries/generations.ts` — arrives with `llm-config-rework` step 3 | prerequisite, not this guide |

Note on "edits survive": `cv_content` entries are `{id, label, score}` refs — bullets
live in the career DB rows and survive by construction. The merge only has to keep ids,
positions, and flags.

## The delta

### Step 1 — sync the pin set to the server (`lib/cv-doc.ts`, `lib/queries/applications.ts`, `content-card.tsx`)

New pure helper:

```ts
/** The server-side pin set implied by a cv_content: flat "type:pk" ids, section
 *  order, de-duped. Deselected-but-pinned still counts — deselect hides from the
 *  render; the pin promises survival across runs. */
export function pinnedIds(content: CvContent): string[] {
  const out: string[] = [];
  for (const list of Object.values(content)) {
    for (const e of list) {
      if (e.pinned && !out.includes(e.id)) out.push(e.id);
    }
  }
  return out;
}
```

`applications.ts`: `ApplicationRow` gains `pinned_entries: string[]`; `ApplicationPatch`
gains `pinned_entries?: string[]`. Two write paths carry it:

- **Save** (`content-card.tsx onSave`): body gains
  `pinned_entries: pinnedIds(cvDraft)` — the draft's pin flags are already part of the
  dirty compare (they live inside `cv_content`), so no extra dirty logic.
- **Apply** (`runToApplicationPatch`): after the merge, derive from the merged doc:

```ts
export function runToApplicationPatch(
  result: TailoredResult,
  currentCv?: CvContent,
): ApplicationPatch {
  const cv = currentCv ? mergePinned(currentCv, result.cv) : result.cv;
  return {
    cv_content: cv,
    pinned_entries: pinnedIds(cv),
    cover_letter: editableBody(result.cover_letter),
    letter_meta: letterMetaFromResult(result.cover_letter),
  };
}
```

**400 surfacing** (`content-card.tsx:159`, the `onSave` mutation's `onError`): today
it toasts "Could not save the application" for everything. `validate_pinned_entries`
(`serializers.py:685`) raises **field-keyed** errors, so on an `ApiError` with
`status === 400` and a `data.pinned_entries` key, surface the server text instead — the
cap/ownership/shape messages ("At most 50 pins…", "Not found or not yours: …") are
written to be user-facing. Reuse the existing `drfFieldError(err, "pinned_entries")`
from `lib/field-save.ts` (it already does the `{field: ["msg"]}` → string decode); keep
the generic string as the fallback for every non-pin failure:

```ts
onError: (e) => {
  const pinMsg =
    e instanceof ApiError &&
    e.status === 400 &&
    (e.data as Record<string, unknown>)?.pinned_entries;
  toast.error(
    pinMsg ? drfFieldError(e, "pinned_entries") : "Could not save the application",
  );
},
```

### Step 2 — hand-added entries pin automatically (`lib/cv-doc.ts addEntry`)

The "my manual work survives" half of the promise: an entry you added by hand must not
vanish on the next apply. `addEntry` appends with `pinned: true` (the user can unpin):

```ts
{ id, label: labelFor(section, row), relevance_score: null, pinned: true }
```

### Step 3 — merge hygiene: warnings belong to a run (`lib/cv-doc.ts mergePinned`)

A `warning` is one run's opinion. Re-selected pins already adopt the run's fresh copy
(warning included). Pins re-appended from the *current* doc must drop any stale warning
they carry from an older run:

```ts
for (const pin of pinned) {
  const i = target.findIndex((e) => e.id === pin.id);
  if (i >= 0) target[i] = { ...target[i], pinned: true };
  else {
    const { warning: _stale, ...keep } = pin;
    target.push({ ...keep });
  }
}
```

### Step 4 — render the warning (`content-card.tsx`)

Entry rows: when `e.warning` is set, an amber `TriangleAlert` (the over-cap icon
pattern at `content-card.tsx:319` is the template) with `title={e.warning}` — next to
the pin icon, visually distinct from the over-cap amber (that one has its own title).
The result surface IS the content card (auto-fill/apply model; there is no separate
result view), so this is the only render site.

### Step 5 — copy

Apply button block (`generate-panel.tsx`): the muted helper text becomes *"Apply
replaces the unpinned content below — pinned entries survive."* Two icons, two
meanings stays: pin = per-application guarantee, favourite (career DB) = global nudge.

## Tests (on disk, skip-marked — unskipping is step 0)

- `frontend/tests/lib/cv-doc.test.ts` —
  `describe.skip("entry-pins sync ([frontend]-entry-pins-ui)")`: `pinnedIds` matrix
  (flags collected across sections, de-duped, deselected-but-pinned included, empty
  doc → `[]`); `addEntry` pins by default; `mergePinned` strips a stale warning on
  tail-appended pins but keeps the run's fresh warning on re-selected ones.
  **Unskip note:** two existing `addEntry` expectations (the exact `toEqual` objects in
  the "addEntry" describe) gain `pinned: true` — update them in the same commit.
- `frontend/tests/lib/applications.test.ts` —
  `describe.skip("pin sync on apply ([frontend]-entry-pins-ui)")`:
  `runToApplicationPatch` carries `pinned_entries` derived from the merged doc.
- Backend: nothing new — the API and selection guarantees are already pinned by
  `PinnedEntriesApiTests` + `PinnedSelectionTests`.

## Verification

Auto-run fills the application → pin two entries, hand-add one (arrives pinned), Save →
the PATCH carries all three ids (network tab) and they land in `pinned_entries` on the
row. Re-run `high` on a commercial executor → the run's result rows show the pins;
apply → the three survive, everything else reflects the new run; a deliberately
irrelevant pin shows the amber warning with the server's text. Apply the same run again
→ document unchanged (idempotent). Patch 51 pins → the cap message appears verbatim in
the toast.

## Results

<!-- Human fills this in. -->
