# [frontend] Fit preflight — drop *and* grow, in the editor

> Roadmap: **CV-filter phase, item 3** — "a pre render run to determine if everything fits and
> adjust accordingly would be good", and the second half of item 2 in the UI section — "if we gain
> some space because the skill cloud is removed … i would love to use the space".
> Branch: `frontend/fit-preflight`
> Depends on: `[frontend]-cv-typography` (it changes what fits) and
> `[fullstack]-cv-section-toggles` (`effectiveCaps` is the input here).

## Context / goal

The fit has two holes.

**It only ever drops.** `fitCv` (`fit.ts:131`) binary-searches the smallest number of entries to
*remove* so the CV fits its page budget. Nothing ever puts an entry back. So the caps are a hard
ceiling: after guide 2 takes the skill clouds off the page, and after guide 5 lets you switch a
section off, the freed space just… stays blank. The page budget is a budget — an under-full page is
as much a miss as an overflowing one.

**It only has two gears.** An entry is either on the CV in full or gone. Guide
`[frontend]-cv-typography` adds a third state — `compact`, the dated row without the description —
and demoting a deep job to it is almost always better than dropping it: a CV that lists every
position but describes only the relevant ones is a normal good CV, while a CV missing positions has
gaps a recruiter will ask about. So the reduction ladder becomes **demote, then drop**.

**It runs too late.** `fitCv` is called from `buildPdf()` — export and preview only. Until you hit
one of those buttons, the editor shows no sign that three entries won't make it. `overCapIds`
warns about the *template cap*, which is a different and much cruder thing than the actual page fit.

This guide adds the grow pass and moves the whole measurement into the editor as a debounced
background render, with the result cached so the export doesn't redo it.

**Why binary search both ways.** Page count is monotone in the entry count — non-increasing as you
drop, non-decreasing as you add — so both directions are ~log₂(n) renders (4–6), not n. A render is
~50–200 ms in the browser, so a full preflight is well under a second. Demotions preserve that
monotonicity (removing a description never adds lines), so the reduction ladder stays a single
ordered list with one binary search over it, exactly like today's drop count.

## Affected files

| path | why |
| --- | --- |
| `frontend/src/lib/render/fit.ts` | `reductionOrder`, `applySteps`, `reduceCv` (replaces `fitCv`), `beyondCap`, `addOrder`, `growCv`, `fitContent`, `preflightKey`. |
| `frontend/src/lib/cv-doc.ts` | `setDetail` — the per-entry detail override. |
| `frontend/src/components/applications/use-preflight.ts` | **new** — the debounced background measurement hook. |
| `frontend/src/components/applications/content-card.tsx` | will-be-cut / will-be-added badges from the preflight. |
| `frontend/src/components/applications/letter-editor.tsx` | live letter page count. |
| `frontend/src/components/applications/export-card.tsx` | build on the cached preflight instead of re-fitting. |

## The code

### 1. `frontend/src/lib/render/fit.ts`

**a.** the pool — what the cap cut off, still in rank order:

```ts
/**
 * The entries each section has *beyond* its cap, in rank order — the grow pass's candidate
 * pool. `headroom` bounds how far past the editorial cap the fit may go: page space is not
 * the only constraint, nobody wants 40 skills just because they fit.
 */
export const GROW_HEADROOM = 1.5;

export function beyondCap(
  content: CvContent,
  maxEntries: Record<string, number>,
  headroom = GROW_HEADROOM,
): CvContent {
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    const cap = maxEntries[section];
    if (cap == null) continue; // uncapped sections are already all in
    out[section] = list.slice(cap, Math.ceil(cap * headroom));
  }
  return out;
}
```

**b.** the mirror of `dropOrder` — best candidates first:

```ts
/**
 * Ids in add-first order: the exact mirror of `dropOrder`. Shallowest position fraction
 * first (the best of what the cap cut), favourites and pins ahead of the rest. Being a
 * mirror matters — an entry the drop order would shed last is the one the grow order takes
 * back first, so the two passes can never fight over the same entry.
 */
export function addOrder(
  pool: CvContent,
  isFavourite: (id: string) => boolean = () => false,
): string[] {
  const cands: {
    id: string;
    frac: number;
    size: number;
    section: string;
    fav: boolean;
    pin: boolean;
  }[] = [];
  for (const [section, list] of Object.entries(pool)) {
    list.forEach((e, i) => {
      cands.push({
        id: e.id,
        frac: (i + 1) / list.length,
        size: list.length,
        section,
        fav: isFavourite(e.id),
        pin: !!e.pinned,
      });
    });
  }
  cands.sort(
    (a, b) =>
      Number(b.pin) - Number(a.pin) ||
      Number(b.fav) - Number(a.fav) ||
      a.frac - b.frac ||
      b.size - a.size ||
      a.section.localeCompare(b.section),
  );
  return cands.map((c) => c.id);
}

/** Put `ids` back into `content`, each into its own section, keeping the rank order the
 *  full (pre-cap) content defines. */
export function applyAdd(
  content: CvContent,
  full: CvContent,
  ids: string[],
): CvContent {
  const wanted = new Set(ids);
  const out: CvContent = { ...content };
  for (const [section, list] of Object.entries(full)) {
    const present = new Set((content[section] ?? []).map((e) => e.id));
    const merged = list.filter((e) => present.has(e.id) || wanted.has(e.id));
    if (merged.length > 0 || content[section]) out[section] = merged;
  }
  return out;
}
```

**c.** the grow search:

```ts
export type GrowResult = { content: CvContent; addedIds: string[]; pages: number };

/**
 * Largest number of pool entries that still fits `maxPages`, by binary search — page count
 * is monotonically non-decreasing in the add count. Mirrors `fitCv`; `pagesFor` is injected
 * the same way.
 */
export async function growCv(
  content: CvContent,
  full: CvContent,
  pool: CvContent,
  maxPages: number,
  pagesFor: (c: CvContent) => Promise<number>,
  isFavourite?: (id: string) => boolean,
): Promise<GrowResult> {
  const order = addOrder(pool, isFavourite);
  if (order.length === 0)
    return { content, addedIds: [], pages: await pagesFor(content) };

  const pagesAt = (k: number) =>
    pagesFor(applyAdd(content, full, order.slice(0, k)));

  const all = await pagesAt(order.length);
  if (all <= maxPages) {
    return {
      content: applyAdd(content, full, order),
      addedIds: order,
      pages: all,
    };
  }
  let lo = 0; // known: fits (it's the fitted content)
  let hi = order.length; // known: doesn't fit
  let loPages = await pagesAt(0);
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    const p = await pagesAt(mid);
    if (p <= maxPages) {
      lo = mid;
      loPages = p;
    } else {
      hi = mid;
    }
  }
  return {
    content: applyAdd(content, full, order.slice(0, lo)),
    addedIds: order.slice(0, lo),
    pages: loPages,
  };
}
```

**c2.** the reduction ladder — demote before drop:

```ts
export type Step = { kind: "demote" | "drop"; id: string };

/**
 * Every reduction available, cheapest first. Two rungs, in order:
 *
 *   1. **demote** a full entry to compact (drop its description), deepest rank fraction
 *      first, never below `MIN_DETAILED` per section;
 *   2. **drop** it entirely, in the existing `dropOrder`.
 *
 * All demotions come before any drop. That is an editorial decision, not an accident:
 * losing a description costs a few lines of colour, losing an entry costs a visible gap in
 * the timeline. Revisit it in Results if a real CV disagrees.
 */
export function reductionOrder(
  content: CvContent,
  detailed: Record<string, number>,
  isFavourite: (id: string) => boolean = () => false,
): Step[] {
  const demotes: { id: string; frac: number; section: string }[] = [];
  for (const [section, list] of Object.entries(content)) {
    const budget = detailed[section] ?? 0;
    // Only entries that are currently full can be demoted, and the top MIN_DETAILED of
    // each section stay full whatever happens.
    list.slice(MIN_DETAILED, budget).forEach((e, i) => {
      if (e.detail === "compact") return; // the user already made it a one-liner
      demotes.push({
        id: e.id,
        frac: (MIN_DETAILED + i + 1) / list.length,
        section,
      });
    });
  }
  demotes.sort(
    (a, b) => b.frac - a.frac || a.section.localeCompare(b.section),
  );
  return [
    ...demotes.map((d) => ({ kind: "demote" as const, id: d.id })),
    ...dropOrder(content, isFavourite).map((id) => ({
      kind: "drop" as const,
      id,
    })),
  ];
}

/** Apply the first k steps: demoted entries carry `detail: "compact"`, dropped ones are
 *  gone. Immutable — the editor's draft must not be rewritten by a measurement. */
export function applySteps(content: CvContent, steps: Step[]): CvContent {
  const demoted = new Set(
    steps.filter((s) => s.kind === "demote").map((s) => s.id),
  );
  const dropped = new Set(steps.filter((s) => s.kind === "drop").map((s) => s.id));
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    out[section] = list
      .filter((e) => !dropped.has(e.id))
      .map((e) =>
        demoted.has(e.id) ? { ...e, detail: "compact" as const } : e,
      );
  }
  return out;
}
```

`reduceCv` is then today's `fitCv` with `reductionOrder`/`applySteps` in place of
`dropOrder`/`applyDrop`, and a result that separates the two kinds:

```ts
export type ReduceResult = {
  content: CvContent;
  demotedIds: string[];
  droppedIds: string[];
  pages: number;
  fits: boolean;
};
```

The binary search itself is unchanged — copy it from `fitCv` (lines 137–171) and split the chosen
prefix by `kind` at the end. **`fitCv` goes away**; `reduceCv` replaces it outright (no compat
bridge — dev-phase rule).

**d.** the orchestrator — one call the editor and the export both use:

```ts
export type PreflightResult = ReduceResult & { addedIds: string[] };

/**
 * cap → reduce down → grow up. Exactly one of the last two ever does anything: if the
 * capped content overflows we demote and then drop, and if it leaves room we add. `full`
 * is the active content BEFORE the cap, so the grow pass has somewhere to grow from.
 *
 * The grow pass deliberately does NOT promote compact entries back to full: the layout's
 * `detailed` budget is an editorial intent ("two jobs described, the rest listed"), not a
 * floor to be spent up. Leftover space buys more entries, not more prose.
 */
export async function fitContent(
  full: CvContent,
  maxEntries: Record<string, number>,
  detailed: Record<string, number>,
  maxPages: number,
  pagesFor: (c: CvContent) => Promise<number>,
  isFavourite?: (id: string) => boolean,
): Promise<PreflightResult> {
  const capped = capContent(full, maxEntries);
  const fit = await reduceCv(capped, detailed, maxPages, pagesFor, isFavourite);
  if (fit.droppedIds.length > 0 || fit.demotedIds.length > 0 || !fit.fits)
    return { ...fit, addedIds: [] };

  const pool = beyondCap(full, maxEntries);
  const grown = await growCv(
    fit.content,
    full,
    pool,
    maxPages,
    pagesFor,
    isFavourite,
  );
  return {
    content: grown.content,
    demotedIds: [],
    droppedIds: [],
    addedIds: grown.addedIds,
    pages: grown.pages,
    fits: true,
  };
}

/**
 * Cache key for a preflight: everything a render depends on. Cheap to compute and stable
 * — the editor recomputes it on every keystroke, so it must not do work.
 */
export function preflightKey(args: {
  specVersion: string;
  content: CvContent;
  sectionsOff: string[];
  letterBody: string;
  letterMeta: unknown;
}): string {
  return JSON.stringify([
    args.specVersion,
    args.content,
    args.sectionsOff,
    args.letterBody,
    args.letterMeta,
  ]);
}
```

### 2. `frontend/src/components/applications/use-preflight.ts` (new)

```ts
/**
 * Background page-fit measurement for the editor. Renders the real documents off-screen
 * (react-pdf into a Blob, never mounted) and reports what the export WILL do — which
 * entries get cut, which get pulled back in, how many pages, whether the letter spills.
 *
 * Three things keep it cheap:
 *  - a debounce, so typing doesn't queue renders;
 *  - a run token, so a superseded measurement's result is discarded rather than raced in;
 *  - a module-level cache keyed by `preflightKey`, shared with the export card — pressing
 *    Download right after the editor settled reuses the measurement instead of redoing it.
 */
import { useEffect, useRef, useState } from "react";
import type { CvContent } from "@/lib/cv-doc";
import type { LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { effectiveCaps, fitContent, preflightKey, type PreflightResult } from "@/lib/render/fit";
import { isFavouriteLookup } from "@/lib/render/parts";
import type { LayoutSpec } from "@/lib/render/spec";
import { CvDocument, LetterDocument, pdfPages } from "@/lib/render/templates";

export type Preflight = {
  result: PreflightResult | null;
  letterPages: number | null;
  measuring: boolean;
};

const CACHE = new Map<string, Preflight>();
const CACHE_MAX = 12; // a handful of editor states; this is a measurement, not a store.

export function readPreflightCache(key: string): Preflight | undefined {
  return CACHE.get(key);
}

const DEBOUNCE_MS = 800;

export function usePreflight(args: {
  spec: LayoutSpec | undefined;
  db: CvEntriesResponse | undefined;
  content: CvContent; // active content, pre-cap (sections already filtered)
  sectionsOff: string[];
  name: string;
  meta: LetterMeta;
  body: string;
}): Preflight {
  const { spec, db, content, sectionsOff, name, meta, body } = args;
  const [state, setState] = useState<Preflight>({
    result: null,
    letterPages: null,
    measuring: false,
  });
  const token = useRef(0);

  const key = spec
    ? preflightKey({
        specVersion: `${spec.version}:${spec.cv.pages}:${spec.font.base_pt}:${spec.colors.accent}`,
        content,
        sectionsOff,
        letterBody: body,
        letterMeta: meta,
      })
    : "";

  useEffect(() => {
    if (!spec || !key) return;
    const cached = CACHE.get(key);
    if (cached) {
      setState(cached);
      return;
    }
    const mine = ++token.current;
    setState((s) => ({ ...s, measuring: true }));
    const timer = setTimeout(async () => {
      try {
        const caps = effectiveCaps(spec.cv.max_entries, sectionsOff);
        const result = await fitContent(
          content,
          caps,
          spec.cv.detailed,
          spec.cv.pages,
          // The candidate content already carries `detail: "compact"` on demoted
          // entries, so the measuring render sees exactly what the export will.
          (c) => pdfPages(CvDocument({ spec, name, content: c, db })),
          isFavouriteLookup(db),
        );
        const letterPages = body.trim()
          ? await pdfPages(LetterDocument({ spec, meta, body }))
          : null;
        if (token.current !== mine) return; // superseded
        const next = { result, letterPages, measuring: false };
        if (CACHE.size >= CACHE_MAX) CACHE.delete(CACHE.keys().next().value!);
        CACHE.set(key, next);
        setState(next);
      } catch {
        if (token.current === mine)
          setState({ result: null, letterPages: null, measuring: false });
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [key, spec, db, name]);

  return state;
}
```

Note the deliberate `key`-only dependency: `content`/`meta`/`body` are already folded into it, and
listing the objects themselves would re-fire on every identity change.

### 3. `frontend/src/components/applications/content-card.tsx`

Call it next to the other state, and derive the two badge sets:

```tsx
  const preflight = usePreflight({
    spec: spec.data,
    db: careerDb.data,
    content: activeContent(cvDraft, sectionsOff),
    sectionsOff,
    name: meta.sender.name || "CV",
    meta: letterMeta,
    body: coverLetter,
  });
  // The real page fit, not the crude template cap: these are the entries the export will
  // actually shorten, cut, and pull back in to fill the page.
  const willCut = new Set(preflight.result?.droppedIds ?? []);
  const willShorten = new Set(preflight.result?.demotedIds ?? []);
  const willAdd = new Set(preflight.result?.addedIds ?? []);
```

Pass `willCut` / `willShorten` / `willAdd` down to `CvEditorSection` alongside `overIds`, and render
on each `<li>`:

```tsx
                {willCut.has(e.id) && (
                  <Badge variant="outline" className="text-amber-600">
                    won't fit
                  </Badge>
                )}
                {willShorten.has(e.id) && (
                  <Badge variant="outline" className="text-muted-foreground">
                    title only
                  </Badge>
                )}
                {willAdd.has(e.id) && (
                  <Badge variant="outline" className="text-emerald-600">
                    fills the page
                  </Badge>
                )}
```

and the per-entry override next to the existing eye / pin / trash actions — the fit's choice is a
default, not a verdict:

```tsx
                <Button
                  variant="ghost"
                  size="icon-sm"
                  title={
                    entryDetail(e, i, section, detailed, willShorten) === "full"
                      ? "Show the title only"
                      : "Show the full description"
                  }
                  onClick={() =>
                    onEdit((c) =>
                      setDetail(
                        c,
                        section,
                        i,
                        entryDetail(e, i, section, detailed, willShorten) === "full"
                          ? "compact"
                          : "full",
                      ),
                    )
                  }
                >
                  {entryDetail(e, i, section, detailed, willShorten) === "full" ? (
                    <AlignLeft />
                  ) : (
                    <Minus />
                  )}
                </Button>
```

with a `setDetail(content, section, index, detail)` immutable helper in `lib/cv-doc.ts`, written to
match the existing `toggleDeselect` / `togglePin` (same signature shape, same immutability).

and a one-line status under the section list:

```tsx
        <p className="text-xs text-muted-foreground">
          {preflight.measuring
            ? "measuring the layout…"
            : preflight.result
              ? `${preflight.result.pages} of ${spec.data?.cv.pages} page(s) used` +
                (preflight.result.addedIds.length
                  ? ` — ${preflight.result.addedIds.length} extra entr${
                      preflight.result.addedIds.length === 1 ? "y" : "ies"
                    } added to fill it`
                  : "")
              : ""}
        </p>
```

### 4. `frontend/src/components/applications/letter-editor.tsx`

The editor takes a `letterPages: number | null` prop (passed from the content card's preflight) and
renders, under the body textarea:

```tsx
      {letterPages != null && letterPages > 1 && (
        <p className="text-xs text-destructive">
          This letter runs to {letterPages} pages — it should be one.
        </p>
      )}
```

The *how much* to cut, and the shortening loop, are guide `[fullstack]-letter-fit`. This is just the
signal.

### 5. `frontend/src/components/applications/export-card.tsx`

In `buildPdf()`, replace the `capContent` + `fitCv` pair (lines 111–133) with the shared call, and
check the cache first:

```tsx
    const off = app.sections_off ?? [];
    const full = activeContent(app.cv_content ?? {}, off);
    const caps = effectiveCaps(s.cv.max_entries, off);
    const key = preflightKey({
      specVersion: `${s.version}:${s.cv.pages}:${s.font.base_pt}:${s.colors.accent}`,
      content: full,
      sectionsOff: off,
      letterBody: app.cover_letter,
      letterMeta: meta,
    });
    const cached = readPreflightCache(key);

    const fit =
      scope === "letter"
        ? null
        : (cached?.result ??
          (await fitContent(
            full,
            caps,
            s.cv.detailed,
            s.cv.pages,
            (c) =>
              pdfPages(
                <CvDocument
                  spec={s}
                  name={name}
                  content={c}
                  db={db}
                  contact={contact}
                  summary={summary}
                  portfolio={portfolio}
                />,
              ),
            isFavouriteLookup(db),
          )));
```

⚠️ The cached measurement is rendered **without** contact/summary/QR (the hook keeps its render
minimal). Those are absolutely positioned or single lines; if the Results show a page-count
disagreement between preview and editor, drop the cache reuse for the QR case rather than
"fixing" it by adding the QR to the hook — the fit must stay layout-invariant.

## Tests

**Step 0 — unskip.** Delete every `.skip` in `frontend/tests/lib/render-grow.test.ts`.

`frontend/tests/lib/render-grow.test.ts` — **new**. `pagesFor` is a fake that costs each entry a
fixed number of "lines" and divides by a page height, so the tests are deterministic and fast (no
react-pdf). Covers:

- `reductionOrder`: demotions come before every drop; demotions go deepest-first; the top
  `MIN_DETAILED` of each section are never demoted; entries beyond the `detailed` budget (already
  compact) produce no demote step; an entry the user already set to `compact` produces none either;
  a section with no `detailed` budget contributes only drops.
- `applySteps`: demoted entries gain `detail: "compact"` and keep their position; dropped ones are
  gone; the input is not mutated; a step naming an unknown id is a no-op.
- `reduceCv`: prefers demotion over dropping (a CV one line over budget loses a description, not an
  entry); falls through to dropping once every demotion is spent; `demotedIds`/`droppedIds` split
  the chosen prefix correctly; `fits: false` still propagates.
- `beyondCap`: takes only what's past the cap, respects `GROW_HEADROOM`, skips uncapped sections.
- `addOrder`: exact mirror of `dropOrder` (pins first, then favourites, then shallowest fraction);
  an entry `dropOrder` sheds last is the one `addOrder` takes first.
- `applyAdd`: re-inserts in the full content's rank order, not at the end; leaves other sections
  alone; is immutable.
- `growCv`: adds nothing when the pool is empty; adds everything when everything fits; binary-search
  boundary — adds exactly the entries that fit and not one more; the returned `pages` is the page
  count of the returned content.
- `fitContent`: overflowing input reduces and never grows; roomy input grows and never reduces; a
  perfect fit does neither; `fits: false` propagates; the grow pass **never promotes** a compact
  entry back to full (the `detailed` budget is intent, not a floor to spend up).
- `preflightKey`: stable across identical inputs, different when any input changes (content,
  sections, letter body, spec version).

```bash
cd frontend && npx vitest run tests/lib/render-grow.test.ts tests/lib/render-fit.test.ts
```

The hook is not unit-tested (hooks are still deferred, memory `frontend-test-layout`) — everything
it decides lives in `fit.ts`.

## Verification

1. Suite red → green, `npx tsc -b`.
2. Open an application whose CV is comfortably under one page. Within a second of the editor
   settling, the status line reads `1 of 1 page(s) used — N extra entries added to fill it`, and
   those entries carry a green **fills the page** badge.
3. Export it: the PDF contains exactly the badged entries. Editor and export must agree — if they
   don't, the cache key is missing an input.
4. Now add entries by hand until it overflows *slightly*: the first thing that should happen is a
   deep job picking up a grey **title only** badge — not an amber **won't fit**. Export and check:
   that job's heading and dates are still on the page, its description is gone. Push it further
   over and the amber drops start once the demotions are spent.
4b. Click the detail toggle on the top job to force it compact, and on a deep one to force it full:
   both must survive a save + reload and beat whatever the fit wanted.
5. Type continuously in the letter body for ~10 s: the network/CPU should show *one* measurement
   after you stop, not one per keystroke.
6. Switch a section off (guide 5): the grow pass should immediately pull more entries in — that's
   the whole point of the freed budget.
7. Paste a very long letter: the "runs to N pages" warning appears under the textarea.
8. Switch layouts (1-page ↔ 2-page): the preflight re-measures and the badges move.

## Results

<!-- human: raw test output, observed issues, what works -->
