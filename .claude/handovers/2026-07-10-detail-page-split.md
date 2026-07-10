# 2026-07-10 — application detail page split (+ render/export phase closed out)

## Goal

`routes/_authenticated/applications/$applicationId.tsx` had grown to 1332 lines (five cards +
letter editor + run-lifecycle wiring in one file). Split it into readable units without changing
behaviour. Volatile phase — Lukas approved the AI doing the mechanical move directly.

## Where it stands

**Done (unverified — see next action):** the route file is now ~70 lines of orchestration; the
cards live in `frontend/src/components/applications/`:

- `posting-card.tsx` — `PostingCard`
- `generate-panel.tsx` — `GeneratePanel`
- `result-view.tsx` — `ResultView` + private `CvSection`, `CoverLetterCard`, `toneClass`
- `content-card.tsx` — `ApplicationContentCard` + private `CvEditorSection`
- `letter-editor.tsx` — `LetterEditor` + `MetaField` + recipient/sender field constants
- `export-card.tsx` — `ExportCard` + `BuiltPdf`
- `use-run-lifecycle.ts` — hook boxing the reducer + REST-snapshot seeding + WS effect +
  1s clock + abort (returns `{ state, socket, now, runCreatedAt, abort, aborting }`)

The move is verbatim: component bodies, comments, and prop signatures unchanged; only imports
were redistributed (two duplicate imports in the old file disappeared). Nothing else imported
from the route file (checked — only route-path `Link`s elsewhere), `routeTree.gen.ts` keys on
the unchanged file path.

**Also in this commit window:** the `[frontend]-render-export.md` guide move to `plans/done/`
was sitting uncommitted in the tree (belongs to the earlier "pdf export" commit, 30a2861) and
got committed with the wrap-up. `plans/to-do/` is now empty.

## Decisions + why

- **`src/components/applications/`, not TanStack `-`-prefixed colocation** — the repo already
  uses `components/<feature>/` (cv/, security/); consistency wins.
- **One card = one file; private subcomponents stay unexported in their card's file.**
- **`useRunLifecycle` lives beside the components, not in `lib/queries/generations.ts`** — the
  abort handler owns an error toast, and `lib/queries/` is deliberately toast-free everywhere
  else; it's page orchestration, not a reusable query.
- No new tests: pure logic already lives in tested `lib/` modules; components/hooks are deferred
  per [[frontend-test-layout]] (no jsdom/testing-library yet). Acceptance = `tsc -b` + existing
  vitest suite green + identical behaviour.

## Open threads / risks

- **Verification of the split is entirely pending** — no build, no tests, no click-through ran
  this session (per the working-style split, that's Lukas's).
- **`render-export` guide reached `done/` with no `## Results` chapter** — the export feature has
  no logged verification run either. If PDF export misbehaves later there's no baseline log.
- Nit spotted, left as-is: `ExportCard` stores `preview.info` (fit result) but the preview dialog
  never displays it — either surface the fit info in the dialog or drop the field.
- Roadmap follow-up made explicit in CLAUDE.md: `CoverLetterWriter` refusal guard (accepts any
  non-empty response, so a small-model refusal can become the letter body).

## Next action

Run the verification checklist for the split: `cd frontend && npm run build && npm test`, then
click through one application detail page (generate + abort, run picker, apply, CV reorder /
deselect, letter rewrite + stub replace, PDF preview/export). Log anything broken; then roadmap
#1 is the portfolio generator (needs a fresh `/setup-guide`).
