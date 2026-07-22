# [frontend] manual-no-run-mode

> **SPA phase, guide 3 — ACTIVATED 2026-07-18** (contracts verified against code; red
> tests on disk, skip-marked — see "Tests" for the step-0 unskip). Branch:
> `frontend/manual-no-run-mode` off `main` after `llm-config-rework` merges.
>
> **Scope shrank at activation, honestly.** The 2026-07-17 stub specced a seed helper,
> a seed button, pin-safe letter furniture, and a runless-page audit. Grepping the code:
> most of that **already exists** — typed during the cv-editor phase. What's left is two
> small deltas and a verification pass. Don't rebuild what's on disk.

## Context / goal

`manual` ("No AI") must work with **zero executors**: HirschAI offline + no commercial
key means `default_executor()` is None, the backend spawns no auto-run, and the
application arrives **empty** — this guide makes that an intended first-class path, not
a degraded one. Acceptance is the offline click-through at the bottom: create → curate
→ letter → export **with ollama and the celery worker both stopped**.

## Verified — already landed (do not re-implement)

| Piece | Where | State |
| --- | --- | --- |
| Backend rejects `mode: "manual"` on `/generations/` | `jac/serializers.py:509` | landed + tested (`jac/tests/test_api.py::test_manual_mode_is_rejected`) |
| Task defensively fails a manual run | `jac/tasks.py:128` | landed |
| No auto-run without an executor; app arrives empty | `jac/views.py:377`, tested `test_create_without_an_executor_creates_no_run` | landed |
| Zero-run application serializes/renders | `runs: []` nested list; route seeds `runId = selectedRunId ?? app.data?.runs[0]?.id ?? null` → `useRunLifecycle(null)` is inert (snapshot query disabled, socket effect returns early); `GeneratePanel` computes `running=false` when `activeRunId == null`; `ApplicationContentCard` renders from `app.cv_content` directly | verified 2026-07-18, nothing to change |
| Seed helper (full career DB, unranked, no scores) | `lib/cv-doc.ts:128 fromCareerDb` — every `SECTION_ORDER` section, `relevance_score: null` | landed + tested (`tests/lib/cv-doc.test.ts` "fromCareerDb") |
| Seed affordance, no-clobber by construction | `content-card.tsx:232` — "Start from full career DB" renders **only when `!hasCv`**, so a non-empty CV can never be silently overwritten (the stub's confirm dialog is moot; to re-seed you empty the sections first) | landed |
| Letter without a run | `normalizeLetterMeta({})` fills furniture (`lib/letter-doc.ts:30`), profile fills the sender block (`content-card.tsx:117`), snippet-append + hand-composed body exist (`letter-editor.tsx`) | landed |
| Export of a manual application | client-side react-pdf; `exportBlocker` only gates on a `PERSONAL_STUB` in the body — a hand-written letter has none; `fitCv` with all-null scores keeps API order (favourites-last only reorders scored drops) | verified |

## The delta

### Step 1 — letter furniture in the posting's language (`lib/letter-doc.ts`)

A German posting hand-curated today gets `language: "en"` furniture. Thread the posting
language through as the fallback:

```ts
export function emptyLetterMeta(language = "en"): LetterMeta {
  return {
    language,
    subject: "",
    salutation: "",
    date: new Date().toISOString().slice(0, 10),
    closing: "",
    sender: {},
    recipient: {},
  };
}

/** Stored letter_meta may be `{}` (manual mode) or partial — fill gaps; `language`
 *  falls back to the POSTING's language, not blanket "en". */
export function normalizeLetterMeta(raw: unknown, language = "en"): LetterMeta {
  const r = (raw ?? {}) as Partial<LetterMeta>;
  return {
    ...emptyLetterMeta(language),
    ...r,
    sender: { ...(r.sender ?? {}) },
    recipient: { ...(r.recipient ?? {}) },
  };
}
```

`content-card.tsx` passes it at all three call sites (the `serverMeta` compare string,
the `useState` initializer, the server re-seed block):
`normalizeLetterMeta(app.letter_meta, app.posting_detail.language)`. Both sides of the
dirty-compare use the same fallback, so this can't fake a dirty state.

### Step 2 — the generate panel steers to hand-curation

The rebuilt panel (`llm-config-rework`) has the `noExecutors` state rendering prose.
This guide owns the copy and the hand-off: *"No AI is available right now — HirschAI is
offline and no commercial key is configured. Build the application by hand below: the
content card starts you from your full career DB."* Prose + (optionally) a plain anchor
that scrolls to the content card — **not** a second seed button; the seed lives in one
place (`content-card.tsx`), keeping the no-clobber guarantee in one place too.

### Step 3 — verify the AI affordances fail loud, not weird, at zero executors

Letter editor rewrite/chat ride the default executor after `llm-config-rework`; with
nothing available the backend answers 400
`No executor available — HirschAI is offline and no provider is configured.` Confirm the
existing error toasts surface that (no code change expected — this is a click-through
item, log the outcome in Results).

## Tests (on disk, skip-marked — unskipping is step 0)

- `frontend/tests/lib/letter-doc.test.ts` — `describe.skip("manual-mode letter
  furniture …")`: `emptyLetterMeta("de")` / `normalizeLetterMeta({}, "de")` fall back to
  the posting language; an explicit stored `language` still wins. Red once unskipped
  (today the extra argument is ignored and "en" comes back).
- Already-green coverage this guide leans on (don't duplicate): `fromCareerDb` +
  `addEntry`/`moveEntry`/`removeEntry` describes in `cv-doc.test.ts`; backend
  `test_manual_mode_is_rejected` + `test_create_without_an_executor_creates_no_run`.

## Verification

With **ollama stopped and the celery worker stopped**: create an application (stays
empty, no run appears, no stale-queue banner), panel shows the no-AI prose → "Start from
full career DB" → prune/reorder/pin → letter: furniture in the posting's language,
sender from profile, snippets appended, body written by hand → export PDF + md — end to
end, zero model traffic, no errors. Then start ollama: the empty application does NOT
retro-generate; the panel's executor row goes green within 30 s. `tsc -b` + vitest green
(unskipped describe included).

## Results

<!-- Human fills this in. -->
