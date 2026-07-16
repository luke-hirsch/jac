# [fullstack] application-pinned-entries

> **⚠️ BACKEND HALF SUPERSEDED (2026-07-16).** `[backend]-entry-pins` now owns the model
> field (`JobApplication.pinned_entries`), the API validation, and the CVFilter keep
> guarantees + high-mode warning. What remains here is the SPA pin UI (pin buttons in the
> editor/result view, warning badges) — rewrite as a `[frontend]` guide in the SPA phase.

> Spun out of the *LLM-mode redesign* follow-up (Lukas, 2026-07-15). Rides after **guide 3
> (staggered-instruct-pipeline)** — it reuses the shortlist's `force_include` hook. Independent of
> guides 4–6 otherwise; can land any time after 3.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

**Pins are per application; favourites are global.** Favourite = "this entry matters for my
career" (existing flag: small capped ranking nudge + shortlist force-include everywhere). Pin =
"this entry stays in *this* application". Both exist side by side.

The journey that motivates pins: create an application → the tower model generates a first cut →
the user likes some of it, adds a couple of entries by hand → hits **Generate again** (maybe on a
bigger model) → **the hand work must not vanish**. Today, applying a new run's result to the
application replaces `cv_content` wholesale — regeneration silently eats manual curation.

A pin has **two effects**:

1. **Survives apply.** Applying a run's result *merges* instead of replacing: pinned entries (with
   their edits and positions) are kept; unpinned content is replaced by the new selection; an entry
   that is both pinned and re-selected appears once (the pinned copy wins, the new
   `relevance_score` is adopted so ranking stays coherent).
2. **Feeds generation.** The task passes the application's pinned entry ids into the filter as
   `force_include`, so the rerank *ranks around them* (same mechanism as favourites in guide 3)
   instead of colliding with the merge afterwards. The model sees what the user already committed
   to; its remaining picks complement rather than duplicate.

## Affected files

| Path | Change |
| --- | --- |
| `frontend/src/components/applications/content-card.tsx` (cv editor) | Pin toggle per entry row (next to deselect/delete); pinned styling; **a hand-added entry is pinned automatically** (that's the "my manual work" half of the promise — the user can unpin). |
| `frontend/src/lib/cv-doc.ts` | `pinned?: boolean` on the entry shape (JSON field — no migration); the **merge-on-apply** pure helper: `mergeRunIntoCv(current, incoming)` implementing the rules above. |
| `frontend/src/components/applications/result-view.tsx` | The apply action calls the merge helper instead of copying `result.cv` wholesale. Apply-button copy says what happens ("replaces unpinned content"). |
| `backend/jac/tasks.py` | Collect pinned ids from `application.cv_content` before filtering; pass to `filter_cv(force_include=...)`. |
| `backend/jac/filter.py` | `force_include: set[str]` param (entry ids, `type:pk`): union of favourites + pins rides the shortlist (guide 3 built the hook) and is exempt from the drop stage, mirroring the favourite guardrail. |

## Approach / key decisions

- **Pin ≠ favourite, and neither implies the other.** Favourites act on every application
  (ranking nudge + force-include); pins act only on theirs (keep + force-include). The UI keeps
  the two icons distinct — pin on the application page, favourite in the career DB.
- **Merge rules, precisely** (dedupe key = entry id `type:pk`):
  - pinned entries keep their current section, position, and edits;
  - unpinned current entries are dropped; incoming selection fills in by rank around the pins;
  - pinned + re-selected → one entry, pinned flag kept, incoming score adopted;
  - no pins → merge degenerates to today's wholesale copy;
  - **idempotent**: applying the same run twice yields the same document.
- **The letter is out of scope.** The letter body is already the user's editable text and apply
  doesn't clobber edited applications wholesale today (auto-fill only while empty); pins are a
  CV-content concept.
- **Server stays source-of-truth-agnostic.** Apply/merge happens client-side (the SPA writes
  `cv_content`, as today); the backend's only new knowledge of pins is reading ids out of the JSON
  for `force_include`. No schema migration, no new endpoint.

## Tests (written at activation)

- `frontend/tests/lib/cv-doc.test.ts` — merge helper: pinned survives with edits/position,
  unpinned replaced, dedupe (pinned + re-selected = one entry, new score), no-pins = wholesale,
  idempotence.
- `frontend/tests/lib/...` — hand-added entry arrives pinned (pure state logic).
- `backend/jac/tests/test_generation_task.py` — pinned ids from `cv_content` reach
  `filter_cv(force_include=...)`.
- `backend/jac/tests/test_cv_selection.py` — a force-included id below the recall cut is in the
  rung's shortlist and survives selection (extends guide 3's favourite test to pins).

## Verification

Generate on the tower → pin two entries, hand-add one, edit a bullet → Generate again
(`conversational`, paid alias) → apply → the three pinned entries are still there with the edit
intact, everything else reflects the new run; apply the same run again → nothing changes.

## Results

<!-- Human fills this in. -->
