# [frontend] manual-no-run-mode

> **SPA phase, guide 3.** Rewritten 2026-07-17 for the single-executor backend: the alias
> vocabulary is gone, the auto-run lives backend-side, and `POST /api/jac/generations/` 400s
> `mode="manual"` (landed — this no-run flow is the *only* manual path; a buggy or hostile
> client can't turn "No AI" into an LLM run). Pairs with
> `[frontend]-model-first-generate-panel` (the panel routes "No AI" here).
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

`manual` ("No AI") must work with **zero executors**: HirschAI offline + no commercial row
means `default_executor()` is None, the backend spawns no auto-run, and the application
arrives **empty** — exactly the state this guide fills. The CV editor already curates
(reorder / deselect / delete / add-from-career-DB per section) and snippets have CRUD;
missing is the **entry point** (start hand-curating without a run) and **seeding** (something
to prune rather than a blank page).

## Affected files

| Path | Change |
| --- | --- |
| `components/applications/generate-panel.tsx` | The "No AI — curate by hand" affordance (guide 1's panel — a parallel action, not a picker entry): "Start hand-curated" seeds `cv_content` from the full career DB and opens the editor; no generations POST. |
| `lib/cv-doc.ts` (+ a seed helper) | Build an initial `cv_content` from the user's career-DB entries (all entries, unranked, no `relevance_score`) so `fitCv` and the editor have real rows. Reuse the existing career-DB queries; no new shape. |
| `lib/queries/applications.ts` | Confirm the existing `cv_content`/`letter_meta` mutation covers the seed write. |
| **Backend** (confirm only) | An application with **zero** runs serializes, renders, and exports without error; if the letter editor needs `letter_meta` furniture, seed a minimal default client-side (language from the posting, empty snippets) — no LLM call anywhere. |

## Approach / key decisions

- **No run, no task, no LLM.** The manual flow must not touch `llm_connector` at all. Guard:
  seeding + editing + export must work with the celery worker down *and* ollama down.
- **The backend guarantee is landed — lean on it.** The serializer rejects `mode="manual"`
  on `/generations/`; nothing to build server-side beyond the no-run rendering confirmation.
- **Seed only into an empty application.** Mirrors the task's auto-fill invariant (fills only
  while empty): "Start hand-curated" seeds only when `cv_content` is empty; otherwise ask
  ("Replace the current CV content?") before overwriting. A double-click or revisit must
  never silently clobber curation.
- **A seeded application blocks later auto-fill — deliberately.** Non-empty means a later AI
  run won't auto-fill; the user applies results explicitly (existing rule). Say it in the
  apply-button copy ("this replaces your current content") so it doesn't read as a bug.
- **Seed = full career DB, unranked.** Everything to prune, not a blank page. No
  `relevance_score`; `fitCv` orders favourites-last as for a degraded run.
- **Interaction with the backend auto-run:** when HirschAI was up at create, the application
  arrives already filled by the auto-run — picking manual then means pruning *that* content
  (a fine canvas), or re-seeding behind the existing confirm. The empty-canvas path is the
  tower-down case — exactly when manual is the offered default.
- **Letter is optional in manual.** No writer model → no auto letter; the snippet-append /
  manual compose path already exists. `PERSONAL_STUB`/export-blocker rules untouched (a
  manual letter has no stub to gate).
- **Cross-check the runless assumptions.** `result-view` + `use-run-lifecycle` currently
  assume a run exists; a manual application has none. The detail page must render from
  `application.cv_content`/`cover_letter` directly when there are no runs.

## Tests (at activation)

- `frontend/tests/lib/cv-doc.test.ts` — the seed builds a full-career-DB `cv_content` with
  expected sections and no scores; the seed helper returns a needs-confirmation signal for a
  non-empty `cv_content` instead of overwriting.
- Pure selector logic: the detail page's data selection prefers `application.*` and requires
  no run.
- Backend: an application with zero `GenerationRun`s serializes + exports without error.

## Verification

With **ollama stopped and the celery worker stopped**: create an application, pick "No AI",
curate the CV, compose a letter from snippets, export a PDF — end to end, no errors, zero
model traffic. This is the acceptance for "the app works when the tower is offline".

## Results

<!-- Human fills this in. -->
