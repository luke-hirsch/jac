# [frontend] entry-pins-ui

> **SPA phase, guide 4.** Replaces `[fullstack]-application-pinned-entries` (2026-07-17): its
> backend half landed as `[backend]-entry-pins` (in `done/`) — `JobApplication.pinned_entries`
> (flat `type:pk` ids; PATCH-validated: shape, ownership, cap 50, de-dupe), every selection
> rung force-keeps pins, and result rows carry `pinned` + `warning` (only the `high` rung
> warns: *"pinned by you — the high-mode selection would have dropped this entry"*). This
> guide is the remaining SPA half.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

**Pins are per-application guarantees** ("this entry stays in *this* application");
favourites are global career-DB nudges. Neither implies the other. The journey: the auto-run
drafts → the user pins keepers, hand-adds an entry, edits a bullet → re-runs (maybe `high` on
a commercial executor) → **the hand work must not vanish**. The generation side already
honours pins (they ride the selection, force-included); missing is the UI to set them and an
apply that doesn't clobber pinned edits — today apply copies `result.cv` wholesale.

## Affected files

| Path | Change |
| --- | --- |
| `lib/queries/applications.ts` | `pinned_entries` on the application type + a PATCH mutation (surface the 400s: cap, malformed/foreign ids). |
| `components/applications/content-card.tsx` | Pin toggle per entry row (next to deselect/delete); pinned styling; **a hand-added entry pins automatically** (the "my manual work" half of the promise — the user can unpin). |
| `components/applications/result-view.tsx` | Render the `pinned` badge + the `warning` string on entry rows; apply calls the merge helper instead of copying wholesale; apply-button copy says "replaces unpinned content". |
| `lib/cv-doc.ts` | `pinned?: boolean` / `warning?: string` on the entry shape; the **merge-on-apply** pure helper `mergeRunIntoCv(current, incoming)`. |

## Approach / key decisions

- **Merge rules, precisely** (dedupe key = entry id `type:pk`):
  - pinned entries keep their current section, position, and edits;
  - pinned + re-selected → one entry, the pinned copy wins, the incoming score is adopted
    (ranking stays coherent);
  - unpinned current entries are dropped; the incoming selection fills in by rank around the
    pins;
  - no pins → the merge degenerates to today's wholesale copy;
  - **idempotent**: applying the same run twice yields the same document.
- **The backend needs nothing new.** The run's result already contains every pin
  (force-included by CVFilter), so the merge mostly resolves duplicates in the user's favour;
  the PATCH keeps the server's pin set in sync so the *next* run force-includes them.
- **Two icons, two meanings**: pin on the application page, favourite in the career DB.
- **The letter stays out of scope** — its edit survival is the apply-only-explicitly rule,
  already in force.

## Tests (at activation)

- `frontend/tests/lib/cv-doc.test.ts` — merge matrix: pinned survives with edits/position,
  dedupe + score adoption, unpinned replaced, no-pins = wholesale, idempotence; hand-added
  entry arrives pinned (pure state logic).
- Badge selector logic: a row with `warning` renders it; `standard`-run rows never carry one.

## Verification

Auto-run → pin two entries, hand-add one, edit a bullet → re-run `high` on a commercial
executor → apply → the three survive with the edit intact, everything else reflects the new
run; the deliberately-irrelevant pin shows the warning badge; apply the same run again →
nothing changes.

## Results

<!-- Human fills this in. -->
