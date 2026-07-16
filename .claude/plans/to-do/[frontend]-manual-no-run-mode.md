# [frontend] manual-no-run-mode

> **Guide 6** — *LLM-mode redesign*. Depends on **guide 1** (`Mode.manual`); pairs with guide 5
> (the panel routes `manual` here). This is the **guaranteed-offline path**: no LLM, no generation
> run — the user hand-curates the application from their career DB.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

`manual` ("No AI") must work with zero models present. Instead of enqueuing a `GenerationRun`, the
user goes straight to the editor with a curatable canvas: the CV editor already supports
reorder / deselect / delete / add-from-career-DB per section, and snippets have CRUD. What's missing
is the **entry point** — starting an application in `manual` mode without a run — and **seeding** the
editor so there's something to prune rather than a blank page.

## Affected files

| Path | Change |
| --- | --- |
| `frontend/src/components/applications/generate-panel.tsx` | The "No AI — curate by hand" affordance (guide 5's model-first panel — manual is a parallel action, not an entry in the model picker): "Start hand-curated" seeds `cv_content` from the full career DB (or an empty canvas, per the seed decision) and opens the editor; no POST to `/generations/`. |
| `frontend/src/lib/cv-doc.ts` (+ a seed helper) | Build an initial `cv_content` from the user's career-DB entries (all entries, unranked, `relevance_score` absent/0) so `fitCv` and the editor have real rows. Reuse the existing career-DB queries; no new shape. |
| `frontend/src/lib/queries/applications.ts` | A mutation to set `cv_content`/`letter_meta` on the application directly (already exists for edits — confirm it covers the seed write). |
| **Backend** (small) | Confirm an application can be fully built + exported with **no** `GenerationRun` attached (result-view / export-card must not assume a run). If the letter editor needs `letter_meta` furniture, seed a minimal default (language from the posting, empty snippets) without any LLM call. |

## Approach / key decisions

- **No run, no task, no LLM.** The whole point is offline tolerance — the manual flow must not touch
  `llm_connector` at all. Guard: seeding + editing must work with the worker down and ollama down.
- **The backend guarantee lives in guide 1, lean on it.** `POST /generations/` 400s
  `mode="manual"` and the task fail-fasts stray manual rows — so this no-run flow is the *only*
  manual path, and a buggy or hostile client can't turn "No AI" into an LLM run. This guide's
  backend work is therefore only the no-run rendering/export confirmation below.
- **Seed only into an empty application.** Mirror the run auto-fill invariant (`tasks.py` fills
  only while `cv_content`/`cover_letter` are empty): "Start hand-curated" seeds only when
  `cv_content` is empty; when it isn't, ask ("Replace the current CV content?") before overwriting.
  A double-click, a re-render, or a later revisit must never silently clobber curation.
- **A seeded application blocks later auto-fill — deliberately.** Seeding makes the application
  non-empty, so a later AI run will *not* auto-fill it; the user applies that run's result
  explicitly (existing rule). That's the right behavior — going manual first means AI output never
  overwrites hand work unasked — but say it in the apply-button copy rather than letting it read as
  a bug ("this replaces your current content").
- **Seed = full career DB, unranked.** Give the user everything to prune (matches how the editor
  already treats deselection), rather than a blank page. The `manual` CV has no `relevance_score`;
  `fitCv` orders favourites-last as it does for a degraded run.
- **Interaction with the auto-run (guide 5):** when the tower was up at create, the application
  arrives already filled by the free instruct run — picking `manual` then means pruning *that*
  content (a perfectly good canvas), or replacing it with the full-career-DB seed behind the
  existing confirm. The empty-canvas seed path below is the tower-down case, which is exactly when
  `manual` is the offered default.
- **Letter is optional in manual.** No writer model → no auto letter. Offer the snippet-append /
  manual compose path the letter editor already has; the `PERSONAL_STUB`/export-blocker rules are
  untouched (a manual letter has no stub to gate).
- **Cross-check the async-loop assumptions.** The result-view + `use-run-lifecycle` hook currently
  assume a run exists; a `manual` application has none. Ensure the detail page renders from
  `application.cv_content`/`cover_letter` directly when there are no runs.

## Tests (written at activation)

- `frontend/tests/lib/cv-doc.test.ts` — the manual seed builds a full-career-DB `cv_content` with
  the expected sections and no scores; the seed helper flags a non-empty `cv_content` (pure
  function returns a needs-confirmation signal instead of the overwrite).
- `frontend/tests/lib/...` — the detail page's data selection prefers `application.*` and does not
  require a run (pure selector logic).
- Backend — an application with zero `GenerationRun`s serializes + exports without error.

## Verification

With **ollama stopped and the celery worker stopped**: create an application, pick "No AI", curate
the CV, compose a letter from snippets, and export a PDF — end to end, no errors, no network calls to
any model. This is the acceptance for "the app works when the tower is offline".

## Results

<!-- Human fills this in. -->
