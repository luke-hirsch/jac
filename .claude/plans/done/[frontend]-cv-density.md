# [frontend] CV density — Compact 9pt + header bio/contact swap

> **Mode note:** volatile phase — Claude implements (Lukas's delegation, 2026-07-11);
> tests land first; Lukas owns the visual verification (this one is _mostly_ visual).

## why

Lukas on the rendered CV: right direction, but "smaller font, tighter within a section,
keep the whitespace between sections; skills/languages and skills-within-a-job even more
compact". Of the three density options offered (9.5 / 9 / 8.5), he picked **Compact — 9pt**.
Also: swap bio before contact in the header.

## decisions

- **Base 10 → 9pt**; small text (entry meta incl. skills-in-a-job, sidebar joined lines)
  → **0.833 × base ≈ 7.5pt**; entry gap 6 → **3pt** (`base/3`); body-under-heading gap
  0.2 → 0.15 × base.
- **Inter-section whitespace preserved**: `sectionTitle.marginTop` 1.0 → **1.4 × base**
  (12.6pt) so marginTop + the shrunken entry gap ≈ the old 16pt visual gap.
- Sidebar compact lines get their **own style** (they used full-size `entry` before).
- **Header order: name → bio → contact** (was name → contact → bio).
- **Budgets**: `default_layout.json` base_pt 9, skills 10→14, languages 4→6;
  `two_page_layout.json` base_pt 11→10, skills 18→20; `FALLBACK_SPEC` mirrors the default.
  Main-section budgets stay — `fitCv` measures real pages anyway.

## design

- `lib/render/templates.tsx`: `cvStyles` gets the numbers above + a `compact` style;
  **exported** so the density decision is pinned by tests, not folklore. `CvSectionView`
  compact branch renders `styles.compact`; `CvPages` swaps summary before contact.
- `lib/render/spec.ts`: FALLBACK_SPEC updated (base_pt 9, skills 14, languages 6).
- `backend/jac/resources/{default,two_page}_layout.json`: as above. Seeded layout media
  files refresh on the next `seed_default_domains` run (the command overwrites stale
  templates — covered by an existing test).
- `frontend/tests/lib/_pdf-text.ts`: the PDF text-run extractor moves out of
  `render-hidden-pdf.test.ts` into a shared test helper (the header-order test needs it).

## test map (land first, red)

| file                                       | covers                                                                                                                                                                                                    |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/lib/render-templates.test.ts` (new) | density numbers at base 9 (entry gap 3, meta/compact ≈ 7.5, sectionTitle marginTop 12.6) and that they scale with base_pt; header text order bio-before-contact on a real render (shared pdf-text helper) |
| `tests/lib/render-spec.test.ts`            | existing assertions are value-agnostic — no change needed; FALLBACK_SPEC equality keeps guarding the parse fallback                                                                                       |

Run: `npx vitest run tests/lib/render-templates.test.ts tests/lib/render-hidden-pdf.test.ts`

## Verification (Lukas)

1. Suites green, `npx tsc -b` clean.
2. `python manage.py seed_default_domains` (refreshes the seeded layout templates).
3. Export a real one-page CV: section gaps look unchanged, entries clearly tighter,
   skills/languages lines noticeably smaller but printable, header shows bio above the
   contact line. Two-page layout still fits sensibly.
4. Judgement call to log in Results: is 9pt/7.5pt the right stopping point, or try the
   Dense (8.5/7) variant next?

## Results

_(filled by Lukas after testing — raw test output, observed issues, what works)_

- just a tad too mujch whitespace around the bio. the header feels a little disconnected. otherwise i like the layout
- one little bug in the one page layout. it appends one empty page.

### Follow-up fixes (Claude, 2026-07-12)

- **Header whitespace**: the culprit was `name.marginBottom` — a full base line (9pt)
  between the 18pt name and the bio. Now `0.4 × base` (3.6pt); pinned by a new test in
  `render-templates.test.ts`. Bio→contact (3.6pt) and contact→content (9pt) unchanged.
- **Empty trailing page**: root cause found by boundary probing — a *non-fixed,
  bottom-anchored* absolute element (the invisible-ink layer from the hidden-layer
  guide) joins react-pdf's pagination and gets a blank page of its own once the flow
  content ends near the page bottom. `fitCv` measures a document *without* ink and the
  export renders *with* it, so exactly at the fill boundary the export grew a page the
  fit loop never saw. Fix: `HiddenInk` is now a single **fixed** Text (fixed elements
  opt out of pagination — verified page-neutral at the boundary) whose render prop
  emits the payload only on `pageNumber === totalPages`, so it stays a single copy at
  the bottom of the last page. Regression test: boundary sweep (n=16–24 jobs) in
  `render-hidden-pdf.test.ts`; extraction + jumbo invariance tests still green.
- Re-verify: export the same one-page CV — no trailing empty page, header reads as one
  block, and `pdftotext` still finds the hidden payload once.
