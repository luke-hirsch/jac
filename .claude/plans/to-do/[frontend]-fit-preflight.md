# [frontend] Fit preflight — drop _and_ grow, in the editor

> Roadmap: **CV-filter phase, item 3** — "a pre render run to determine if everything fits and
> adjust accordingly would be good", and the second half of the UI section's item 2 — "if we gain
> some space because the skill cloud is removed … i would love to use the space".
> Branch: `frontend/fit-preflight`
> Depends on: `[frontend]-cv-typography` — **landed** (`d66ed35`, `08fd5fa`): `spec.cv.detailed`,
> `CvEntry.detail`, `entryDetail(entry, i, section, detailed, demoted)` and the `demoted` prop on
> `CvPages` are all in the tree. This guide is what fills that prop.
> **Activated 2026-08-05** — contracts verified against the code, tests on disk and red.

## Context / goal

The fit has three holes.

**It only ever drops.** `fitCv` (`fit.ts:131`) binary-searches the smallest number of entries to
_remove_ so the CV fits its page budget. Nothing ever puts an entry back, so the template caps are a
hard ceiling: now that guide 2 took the skill clouds off the page, the freed space just… stays
blank. A page budget is a budget — an under-full page is as much a miss as an overflowing one.

**It only has two gears.** An entry is either on the CV in full or gone. Guide 2 added the third
state — `compact`, the dated row without the description — and demoting a deep job to it is almost
always better than dropping it: a CV that lists every position but describes only the relevant ones
is a normal good CV, while a CV missing positions has gaps a recruiter will ask about. So the
reduction ladder becomes **demote, then drop**.

**It runs too late.** `fitCv` is called from `buildPdf()` — export and preview only. Until you press
one of those buttons the editor shows no sign that three entries won't make it. `overCapIds` warns
about the _template cap_, which is a different and much cruder thing than the actual page fit — and
with a grow pass it is now actively wrong, because an entry past the cap may well come back.

This guide adds the grow pass, adds the demote rung, and moves the whole measurement into the editor
as a debounced background render, cached so the export doesn't redo it.

**Why binary search both ways.** Page count is monotone in the entry count — non-increasing as you
drop, non-decreasing as you add — so both directions are ~log₂(n) renders (4–6), not n. A render is
~50–200 ms in the browser, so a full preflight is well under a second. Demotions preserve that
monotonicity (removing a description never adds lines), so the reduction ladder stays a single
ordered list with one binary search over it, exactly like today's drop count.

**Two decisions worth stating before the code.**

1. **Demotions travel _beside_ the content, never inside it.** `applySteps` does not write
   `detail: "compact"` into the entries. `entry.detail` is the _user's_ field — `entryDetail` reads
   it before anything else, and the markdown exporter (`export.ts:36`) honours it and nothing else.
   A machine demotion baked into that field would be indistinguishable from editorial intent and
   would silently strip descriptions from the markdown export. So the fit returns a `demoted` id set
   and hands it to `CvPages` through the prop guide 2 left for exactly this. The measuring render
   and the export render therefore take the _same two inputs_ — parity by construction, which is the
   entire point of a preflight. This is why `pagesFor` gains a second parameter.
2. **The user always wins.** `reductionOrder` skips any entry that carries an explicit
   `entry.detail`, in either direction: `compact` has nothing left to give, and `full` is the user
   saying "describe this one". Without that skip the fit would demote an entry the render then
   refuses to shorten, and the measurement would disagree with the page.

## Scope note — `sections_off` is _not_ in this guide

The original draft depended on `[fullstack]-cv-section-toggles` for `effectiveCaps` and the two-arg
`activeContent`. Neither exists yet, and neither is needed to ship the preflight: this guide uses
`spec.cv.max_entries` and today's one-arg `activeContent`. `preflightKey` already accepts an
optional `sectionsOff` so the key survives the change. The section-toggles guide carries the
four-line follow-up (its §8) that threads the off-list through the hook and the export card.

## Affected files

| path                                                     | why                                                                                                                                                                                         |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/lib/render/fit.ts`                         | `reductionOrder`, `applySteps`, `PagesFor`, `reduceCv`, `beyondCap`, `addOrder`, `applyAdd`, `growCv`, `fitContent`, `preflightKey`. **`fitCv`, `FitResult` and `overCapIds` are deleted.** |
| `frontend/src/lib/cv-doc.ts`                             | `setDetail` — the per-entry detail override.                                                                                                                                                |
| `frontend/src/components/applications/use-preflight.ts`  | **new** — the debounced background measurement hook + the shared cache.                                                                                                                     |
| `frontend/src/components/applications/content-card.tsx`  | the hook call, the three badge sets, the detail toggle, the status line; `overCapIds` usage goes.                                                                                           |
| `frontend/src/components/applications/letter-editor.tsx` | `letterPages` prop + the "runs to N pages" line.                                                                                                                                            |
| `frontend/src/components/applications/export-card.tsx`   | build on the cached preflight instead of re-fitting; pass `demoted` to both CV render sites.                                                                                                |
| `frontend/src/lib/render/preview.ts`                     | `BuiltPdf.fit` is a `PreflightResult`; `fitNotices` reports shortened and added entries.                                                                                                    |

**Blast radius (honest).** Deleting `fitCv` breaks `export-card.tsx` and `preview.ts` — both are
repaired here, in this branch, no bridge. Deleting `overCapIds` removes the amber template-cap
warning from the editor; the per-section `4/5 in the layout` counter stays, and the per-entry truth
becomes the preflight's. Nothing else in the tree imports either symbol (checked).

## The code

Everything below was typed into a throwaway worktree off `08fd5fa` and verified: `npx tsc -b` clean,
`npx eslint` clean, full suite green (396 passed / 52 skipped).

### 1. `frontend/src/lib/render/fit.ts`

**a.** two new imports at the top, next to the existing `CvContent` type import:

```ts
import { MIN_DETAILED } from "./parts";
import type { LayoutSpec } from "./spec";
```

(No cycle: `parts.ts` and `spec.ts` don't import `fit.ts`.)

**b.** delete `overCapIds` (lines 86–107), `FitResult` and `fitCv` (lines 119–172). `dropOrder`,
`applyDrop`, `capContent`, `countPdfPages` and `MIN_KEEP` all stay exactly as they are.

**c.** the reduction ladder — demote before drop:

```ts
/* ---------- the reduction ladder: demote, then drop ---------- */

export type Step = { kind: "demote" | "drop"; id: string };

/**
 * Every reduction available, cheapest first. Two rungs, in order:
 *
 *   1. **demote** a full entry to compact (drop its description), deepest rank fraction
 *      first, never below `MIN_DETAILED` per section;
 *   2. **drop** it entirely, in the existing `dropOrder`.
 *
 * All demotions come before any drop. That is an editorial decision, not an accident:
 * losing a description costs a few lines of colour, losing an entry costs a visible gap
 * in the timeline. Revisit it in Results if a real CV disagrees.
 *
 * An entry with an explicit `detail` is skipped either way — `compact` has nothing left
 * to give, and `full` is the user overruling the fit (and `entryDetail` would honour that
 * at render time, so demoting it would only make the measurement lie).
 */
export function reductionOrder(
  content: CvContent,
  detailed: Record<string, number>,
  isFavourite: (id: string) => boolean = () => false,
): Step[] {
  const demotes: { id: string; frac: number; section: string }[] = [];
  for (const [section, list] of Object.entries(content)) {
    // Only entries the layout renders in full can be demoted, and the top
    // MIN_DETAILED of each section stay full whatever happens.
    const budget = Math.min(detailed[section] ?? 0, list.length);
    for (let i = MIN_DETAILED; i < budget; i++) {
      const e = list[i];
      if (e.detail) continue;
      demotes.push({ id: e.id, frac: (i + 1) / list.length, section });
    }
  }
  demotes.sort((a, b) => b.frac - a.frac || a.section.localeCompare(b.section));
  return [
    ...demotes.map((d) => ({ kind: "demote" as const, id: d.id })),
    ...dropOrder(content, isFavourite).map((id) => ({
      kind: "drop" as const,
      id,
    })),
  ];
}

/**
 * Apply the first k steps: dropped entries are gone, demoted ones are reported *beside*
 * the content (never written into `entry.detail` — that field is the user's). Immutable:
 * a measurement must not rewrite the editor's draft. An id that is both demoted and
 * dropped is only dropped — reporting it as "shortened" would be a lie.
 */
export function applySteps(
  content: CvContent,
  steps: Step[],
): { content: CvContent; demoted: Set<string> } {
  const dropped = new Set(
    steps.filter((s) => s.kind === "drop").map((s) => s.id),
  );
  const demoted = new Set(
    steps
      .filter((s) => s.kind === "demote" && !dropped.has(s.id))
      .map((s) => s.id),
  );
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    out[section] = list.filter((e) => !dropped.has(e.id));
  }
  return { content: out, demoted };
}

/**
 * Render a candidate and count its pages — the only impure part, injected. The demotion
 * set is the second argument because that is how the real render takes it (`CvPages`'
 * `demoted` prop), so what the search measures is what the export draws.
 */
export type PagesFor = (
  content: CvContent,
  demoted: Set<string>,
) => Promise<number>;

const NO_DEMOTIONS: Set<string> = new Set();

export type ReduceResult = {
  content: CvContent;
  demotedIds: string[];
  droppedIds: string[];
  pages: number;
  fits: boolean; // false: even the min-keep floor overflows the budget
};

/**
 * Smallest prefix of the reduction ladder that fits `maxPages`, by binary search — the
 * page count is monotonically non-increasing in the step count (a demotion never adds
 * lines, a drop never adds entries). Same search as the old `fitCv`; what changed is that
 * a step can now be a demotion, and the chosen prefix is split by kind at the end.
 */
export async function reduceCv(
  content: CvContent,
  detailed: Record<string, number>,
  maxPages: number,
  pagesFor: PagesFor,
  isFavourite?: (id: string) => boolean,
): Promise<ReduceResult> {
  const steps = reductionOrder(content, detailed, isFavourite);
  const pagesAt = (k: number) => {
    const c = applySteps(content, steps.slice(0, k));
    return pagesFor(c.content, c.demoted);
  };
  const resultAt = (k: number, pages: number, fits: boolean): ReduceResult => {
    const taken = steps.slice(0, k);
    const c = applySteps(content, taken);
    return {
      content: c.content,
      demotedIds: taken
        .filter((s) => s.kind === "demote" && c.demoted.has(s.id))
        .map((s) => s.id),
      droppedIds: taken.filter((s) => s.kind === "drop").map((s) => s.id),
      pages,
      fits,
    };
  };

  const full = await pagesAt(0);
  if (full <= maxPages) return resultAt(0, full, true);

  let lo = 0; // known: doesn't fit
  let hi = steps.length;
  let hiPages = await pagesAt(hi);
  if (hiPages > maxPages) return resultAt(hi, hiPages, false);
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    const p = await pagesAt(mid);
    if (p <= maxPages) {
      hi = mid;
      hiPages = p;
    } else {
      lo = mid;
    }
  }
  return resultAt(hi, hiPages, true);
}
```

**d.** the grow pass — the pool, the order, the search:

```ts
/* ---------- the grow pass ---------- */

/**
 * How far past the editorial cap the fit may go. Page space is not the only constraint —
 * nobody wants 40 skills just because they fit.
 */
export const GROW_HEADROOM = 1.5;

/** What each section has *beyond* its cap, in rank order — the grow pass's candidate
 *  pool. Uncapped sections are already all in, so they contribute nothing. */
export function beyondCap(
  content: CvContent,
  maxEntries: Record<string, number>,
  headroom = GROW_HEADROOM,
): CvContent {
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    const cap = maxEntries[section];
    if (cap == null) continue;
    out[section] = list.slice(cap, Math.ceil(cap * headroom));
  }
  return out;
}

/**
 * Ids in add-first order — `dropOrder` read backwards. Shallowest position fraction
 * first (the best of what the cap cut), pins and favourites ahead of the rest, and every
 * tiebreak reversed. Being a mirror matters: an entry the drop order would shed last is
 * the one the grow order takes back first, so the two passes can never fight over the
 * same entry. (The fraction is relative to the *pool*, which is what "how far past the
 * cap" means here — a section with more spare depth gets the earlier slot.)
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
      a.size - b.size ||
      b.section.localeCompare(a.section),
  );
  return cands.map((c) => c.id);
}

/** Put `ids` back into `content`, each into its own section, in the rank order the
 *  full (pre-cap) content defines — not at the end. Immutable. */
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

export type GrowResult = {
  content: CvContent;
  addedIds: string[];
  pages: number;
};

/**
 * Largest number of pool entries that still fits `maxPages`, by binary search — the page
 * count is monotonically non-decreasing in the add count. Measures with no demotions:
 * `fitContent` only grows when the reduce pass made none.
 */
export async function growCv(
  content: CvContent,
  full: CvContent,
  pool: CvContent,
  maxPages: number,
  pagesFor: PagesFor,
  isFavourite?: (id: string) => boolean,
): Promise<GrowResult> {
  const order = addOrder(pool, isFavourite);
  const pagesAt = (k: number) =>
    pagesFor(applyAdd(content, full, order.slice(0, k)), NO_DEMOTIONS);

  if (order.length === 0)
    return { content, addedIds: [], pages: await pagesAt(0) };

  const all = await pagesAt(order.length);
  if (all <= maxPages)
    return {
      content: applyAdd(content, full, order),
      addedIds: order,
      pages: all,
    };

  let lo = 0; // known: fits (it is the fitted content)
  let hi = order.length; // known: doesn't fit
  let loPages: number | null = null; // only measured if the search never advances lo
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
    pages: loPages ?? (await pagesAt(lo)),
  };
}
```

**e.** the orchestrator and the cache key — one call the editor and the export both use:

```ts
/* ---------- the orchestrator ---------- */

export type PreflightResult = ReduceResult & { addedIds: string[] };

/**
 * cap → reduce down → grow up. Exactly one of the last two ever does anything: if the
 * capped content overflows we demote and then drop, and if it leaves room we add. `full`
 * is the active content BEFORE the cap, so the grow pass has somewhere to grow from.
 *
 * The grow pass deliberately does NOT promote compact entries back to full: the layout's
 * `detailed` budget is editorial intent ("two jobs described, the rest listed"), not a
 * floor to be spent up. Leftover space buys more entries, not more prose.
 */
export async function fitContent(
  full: CvContent,
  maxEntries: Record<string, number>,
  detailed: Record<string, number>,
  maxPages: number,
  pagesFor: PagesFor,
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
 * Cache key for a preflight: everything a render depends on. The whole spec goes in
 * (it is a small object, and picking fields out of it is how the key silently goes
 * stale); so does the CV header, because a contact line and a profile summary are page
 * content, not chrome. The QR block is deliberately absent — it is absolutely positioned
 * and proven layout-invariant (`render-templates.test.ts`), and the URL it adds to the
 * contact line is already in `cvHeader`.
 *
 * Cheap and stable: the editor recomputes it on every keystroke, so it must not do work.
 */
export function preflightKey(args: {
  spec: LayoutSpec;
  content: CvContent;
  sectionsOff?: string[]; // filled by [fullstack]-cv-section-toggles
  cvHeader: { name: string; contact: string; summary: string };
  letterBody: string;
  letterMeta: unknown;
}): string {
  return JSON.stringify([
    args.spec,
    args.content,
    args.sectionsOff ?? [],
    args.cvHeader,
    args.letterBody,
    args.letterMeta,
  ]);
}
```

### 2. `frontend/src/lib/cv-doc.ts` — the detail override

Next to `togglePin` (line 189), same shape as its neighbours:

```ts
/** The user's per-entry detail override. `entryDetail` reads this before the fit's
 *  demotion set, so a stored value beats whatever the page fit would have preferred —
 *  which is exactly why the fit never writes this field itself. */
export function setDetail(
  content: CvContent,
  section: string,
  index: number,
  detail: "full" | "compact",
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  const next = list.map((e, i) => (i === index ? { ...e, detail } : e));
  return { ...content, [section]: next };
}
```

### 3. `frontend/src/components/applications/use-preflight.ts` (new)

```ts
/**
 * Background page-fit measurement for the editor. Renders the real documents off-screen
 * (react-pdf into a Blob, never mounted) and reports what the export WILL do — which
 * entries get shortened, which get cut, which get pulled back in, how many pages, whether
 * the letter spills.
 *
 * Three things keep it cheap:
 *  - a debounce, so typing doesn't queue renders;
 *  - a run token, so a superseded measurement's result is discarded rather than raced in;
 *  - a module-level cache keyed by `preflightKey`, shared with the export card — pressing
 *    Download right after the editor settled reuses the measurement instead of redoing it.
 */
import { useEffect, useRef, useState } from "react";
import type { CvContent } from "@/lib/cv-doc";
import { stripSoftStub, type LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import {
  fitContent,
  preflightKey,
  type PreflightResult,
} from "@/lib/render/fit";
import { isFavouriteLookup } from "@/lib/render/parts";
import type { LayoutSpec } from "@/lib/render/spec";
import { CvDocument, LetterDocument, pdfPages } from "@/lib/render/templates";

export type Preflight = {
  result: PreflightResult | null;
  letterPages: number | null;
  measuring: boolean;
};

const IDLE: Preflight = { result: null, letterPages: null, measuring: false };

const CACHE = new Map<string, Preflight>();
const CACHE_MAX = 12; // a handful of editor states; this is a measurement, not a store.

export function readPreflightCache(key: string): Preflight | undefined {
  return CACHE.get(key);
}

const DEBOUNCE_MS = 800;

export function usePreflight(args: {
  spec: LayoutSpec | undefined;
  db: CvEntriesResponse | undefined;
  content: CvContent; // active content, pre-cap
  name: string;
  contact: string;
  summary: string;
  meta: LetterMeta;
  body: string;
}): Preflight {
  const { spec, db, content, name, contact, summary, meta, body } = args;
  // Only *finished* measurements are state; "which state am I looking at" is derived
  // below from the key, so the effect never sets state synchronously (the react-hooks
  // lint rejects that, and it would cascade a render per keystroke anyway).
  const [done, setDone] = useState<{ key: string; value: Preflight } | null>(
    null,
  );
  const token = useRef(0);

  const key = spec
    ? preflightKey({
        spec,
        content,
        cvHeader: { name, contact, summary },
        letterBody: body,
        letterMeta: meta,
      })
    : "";

  useEffect(() => {
    if (!spec || !key || CACHE.has(key)) return;
    const mine = ++token.current;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const result = await fitContent(
            content,
            spec.cv.max_entries,
            spec.cv.detailed,
            spec.cv.pages,
            (c, demoted) =>
              pdfPages(
                CvDocument({
                  spec,
                  name,
                  content: c,
                  db,
                  demoted,
                  contact,
                  summary,
                }),
              ),
            isFavouriteLookup(db),
          );
          const stripped = stripSoftStub(body);
          const letterPages = stripped
            ? await pdfPages(LetterDocument({ spec, meta, body: stripped }))
            : null;
          if (token.current !== mine) return; // superseded
          const value: Preflight = { result, letterPages, measuring: false };
          if (CACHE.size >= CACHE_MAX) CACHE.delete(CACHE.keys().next().value!);
          CACHE.set(key, value);
          setDone({ key, value });
        } catch {
          if (token.current === mine) setDone({ key, value: IDLE });
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // `key` already folds in content / header / letter: listing those objects would
    // re-fire the measurement on every identity change instead of every real change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, spec, db]);

  if (!spec) return IDLE;
  const cached = CACHE.get(key);
  if (cached) return cached;
  return done?.key === key
    ? done.value
    : { result: null, letterPages: null, measuring: true };
}
```

Note the deliberate dependency list and the `.ts` (not `.tsx`) extension: the documents are called
as plain functions, which typechecks fine against `pdfPages` and keeps JSX out of a hook file.

### 4. `frontend/src/components/applications/content-card.tsx`

**a.** imports: add `AlignLeft` and `Minus` to the `lucide-react` block; add `activeContent` and
`setDetail` to the `@/lib/cv-doc` block; add `contactLine` to the **existing** `@/lib/letter-doc`
block; replace the `overCapIds` import line with

```tsx
import { entryDetail } from "@/lib/render/parts";
```

and add `import { usePreflight } from "./use-preflight";` next to the `LetterEditor` import.

**b.** replace the `overCap` block (lines 184–188) with the hook and the three id sets:

```tsx
const hasCv = SECTION_ORDER.some((s) => (cvDraft[s] ?? []).length > 0);
const maxEntries = spec.data?.cv.max_entries ?? {};

// The real page fit, measured in the background off the live draft — not the crude
// template cap. It is what the export will do, so the editor can show it up front.
const preflight = usePreflight({
  spec: spec.data,
  db: careerDb.data,
  content: activeContent(cvDraft),
  name: letterMeta.sender.name || "CV",
  contact: contactLine(letterMeta.sender, {
    socials: profile.data?.show_socials ?? false,
  }),
  summary: profile.data?.bio ?? "",
  meta: letterMeta,
  body: coverLetter,
});
const willCut = new Set(preflight.result?.droppedIds ?? []);
const willShorten = new Set(preflight.result?.demotedIds ?? []);
const willAdd = new Set(preflight.result?.addedIds ?? []);
```

**c.** the section list (line 237): `overIds={overCap}` becomes four props, and a status line goes
under the map:

```tsx
                cap={maxEntries[section]}
                detailed={spec.data?.cv.detailed ?? {}}
                willCut={willCut}
                willShorten={willShorten}
                willAdd={willAdd}
                freshIds={fresh.ids}
              />
            ))}
            <p className="text-xs text-muted-foreground">
              {preflight.measuring
                ? "measuring the layout…"
                : preflight.result
                  ? `${preflight.result.pages} of ${spec.data?.cv.pages ?? 1} page(s) used` +
                    (preflight.result.addedIds.length
                      ? ` — ${preflight.result.addedIds.length} extra entr${
                          preflight.result.addedIds.length === 1 ? "y" : "ies"
                        } added to fill it`
                      : "")
                  : ""}
            </p>
```

**d.** `<LetterEditor>` (line 274) gains `letterPages={preflight.letterPages}`.

**e.** `CvEditorSection`'s signature: `overIds: Set<string>` becomes

```tsx
detailed: Record<string, number>;
willCut: Set<string>;
willShorten: Set<string>;
willAdd: Set<string>;
```

with the destructuring updated to match.

**f.** inside the entry map (line 327): `const isOver = willCut.has(e.id);` and one line more —

```tsx
const detail = entryDetail(e, i, section, detailed, willShorten);
```

The amber-triangle title (line 338) now describes the real reason:

```tsx
                <span title="The page fit has to cut this entry — deselect or shorten something else to keep it.">
```

**g.** after the relevance badge (line 361–363), the three fit badges and the detail toggle — the
fit's choice is a default, not a verdict:

```tsx
{
  willCut.has(e.id) && (
    <Badge variant="outline" className="text-amber-600">
      won't fit
    </Badge>
  );
}
{
  willShorten.has(e.id) && (
    <Badge variant="outline" className="text-muted-foreground">
      title only
    </Badge>
  );
}
{
  willAdd.has(e.id) && (
    <Badge variant="outline" className="text-emerald-600">
      fills the page
    </Badge>
  );
}
<Button
  variant="ghost"
  size="icon"
  aria-label={detail === "full" ? "Show title only" : "Show description"}
  title={
    detail === "full"
      ? "Show the title only — keeps the position, drops the description."
      : "Show the full description."
  }
  onClick={() =>
    onEdit((c) =>
      setDetail(c, section, i, detail === "full" ? "compact" : "full"),
    )
  }
>
  {detail === "full" ? (
    <AlignLeft className="h-4 w-4" />
  ) : (
    <Minus className="h-4 w-4" />
  )}
</Button>;
```

### 5. `frontend/src/components/applications/letter-editor.tsx`

The prop (after `onBody`, line 62):

```tsx
/** Measured page count from the editor's preflight; null while unmeasured. */
letterPages: number | null;
```

and the signal, right after the `<Textarea>` (before the `{sel && anchor && (` popover):

```tsx
{
  letterPages != null && letterPages > 1 && (
    <p className="text-xs text-destructive">
      This letter runs to {letterPages} pages — it should be one.
    </p>
  );
}
```

The _how much_ to cut, and the shortening loop, are guide `[fullstack]-letter-fit`. This is only the
signal it builds on.

### 6. `frontend/src/components/applications/export-card.tsx`

Import line 30 becomes two lines:

```tsx
import { capContent, fitContent, preflightKey } from "@/lib/render/fit";
import { readPreflightCache } from "./use-preflight";
```

(`capContent` is still used by `onDownloadMd`.) Then replace lines 102–124 of `buildPdf`:

```tsx
const full = activeContent(app.cv_content ?? {});
// The editor measured this exact state moments ago (same key builder, same inputs):
// reuse it instead of re-rendering the CV four times. A stale draft, a different
// contact line or the QR's URL all change the key, so a miss is a real difference.
const cached = readPreflightCache(
  preflightKey({
    spec: s,
    content: full,
    cvHeader: { name, contact, summary },
    letterBody: app.cover_letter,
    letterMeta: meta,
  }),
);
const fit =
  scope === "letter"
    ? null
    : (cached?.result ??
      (await fitContent(
        full,
        s.cv.max_entries,
        s.cv.detailed,
        s.cv.pages,
        (c, demoted) =>
          pdfPages(
            <CvDocument
              spec={s}
              name={name}
              content={c}
              db={db}
              demoted={demoted}
              contact={contact}
              summary={summary}
              portfolio={portfolio}
            />,
          ),
        isFavouriteLookup(db),
      )));
// Demotions travel beside the content, never inside it (see fit.ts): the render
// resolves them through `entryDetail`, exactly as the measuring render did.
const demoted = new Set(fit?.demotedIds ?? []);
```

and pass that set at **both** CV render sites: `demoted={demoted}` on the `scope === "cv"`
`<CvDocument>` (line ~157) and `demoted,` inside `ApplicationDocument`'s `cv={{ … }}` (line ~180).
Miss one and the export quietly grows the page the fit just measured away.

`onDownloadMd` stays as it is: markdown is the editorial artefact and honours `entry.detail` only —
it must not inherit a machine demotion.

### 7. `frontend/src/lib/render/preview.ts`

`BuiltPdf.fit` becomes `PreflightResult | null` (and the type import changes with it). Then
`fitNotices` reports the two new outcomes — a description silently missing from the PDF is exactly
the surprise this module exists to prevent:

```ts
  } else if (built.fit) {
    const dropped = built.fit.droppedIds.length;
    if (dropped > 0) {
      out.push({
        level: "info",
        text:
          `${dropped} lowest-ranked entr${dropped === 1 ? "y was" : "ies were"} dropped to fit ` +
          `${pageBudget} page(s). Deselect or reorder to override.`,
      });
    }
    const shortened = built.fit.demotedIds.length;
    if (shortened > 0) {
      out.push({
        level: "info",
        text:
          `${shortened} ${shortened === 1 ? "entry was" : "entries were"} shortened to ` +
          `${shortened === 1 ? "its" : "their"} heading to fit ${pageBudget} page(s).`,
      });
    }
    const added = built.fit.addedIds.length;
    if (added > 0) {
      out.push({
        level: "info",
        text:
          `${added} extra entr${added === 1 ? "y was" : "ies were"} added to fill ` +
          `${pageBudget} page(s).`,
      });
    }
  }
```

(The `!built.fit.fits` warning branch above it is unchanged, and still swallows the rest — an
unfittable CV dropped everything, so counts on top of it are noise.)

## Tests

**No step 0 — the tests are already unskipped and red.** They were rewritten at activation against
the verified contracts above.

| file                                          | state                                                        | covers                                                                                                                                        |
| --------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/tests/lib/render-preflight.test.ts` | **new** (replaces the old skip-marked `render-grow.test.ts`) | the whole preflight: `reductionOrder`, `applySteps`, `reduceCv`, `beyondCap`, `addOrder`, `applyAdd`, `growCv`, `fitContent`, `preflightKey`. |
| `frontend/tests/lib/render-fit.test.ts`       | trimmed                                                      | the `fitCv` and `overCapIds` blocks are gone with the functions; `dropOrder` / `applyDrop` / `capContent` / `countPdfPages` stay.             |
| `frontend/tests/lib/render-preview.test.ts`   | extended                                                     | `fitNotices` for shortened and added entries; the fixture is a `PreflightResult`.                                                             |
| `frontend/tests/lib/cv-doc.test.ts`           | extended                                                     | `setDetail`: writes one entry, flips, immutable, out-of-range is a no-op.                                                                     |

`pagesFor` is faked as a line count, so the suite is deterministic and fast (the real react-pdf
measurement is already covered by the render suites). Two fakes: a detail-blind one, and
`pagesByDetail`, which mirrors `entryDetail`'s precedence exactly — explicit `entry.detail`, then
the `demoted` set, then rank — and charges 3 lines for a full entry and 1 for a compact one.

Points worth knowing before you read the assertions:

- **the ladder**: 4 jobs, `detailed: { jobs: 3 }`, 10 lines. At 8 lines/page one demotion fixes it
  and _nothing is dropped_; at 5 lines/page both demotions are spent and then `job:4` drops.
- **the mirror**: `addOrder` is asserted against `dropOrder` reversed over the ids they share —
  `dropOrder` protects each section's min-keep floor and `addOrder` has no floor to protect, so
  "identical reversed" is only true over the intersection.
- **the headroom**: cap 2 with `GROW_HEADROOM` 1.5 → `ceil(3)` slots → exactly one entry may come
  back, even on a page with room for eight. Uncapped sections are never grow candidates.
- **the honesty guards**: an entry that is demoted _and_ dropped is reported once, as a drop; an
  entry the user pinned to `full` is never demoted, and the ladder pays with a drop instead.

```bash
cd frontend && npx vitest run tests/lib/render-preflight.test.ts tests/lib/render-fit.test.ts \
  tests/lib/render-preview.test.ts tests/lib/cv-doc.test.ts
```

**Red set at activation** (`npx vitest run`, whole suite): `43 failed | 353 passed | 52 skipped`.
The 43 are exactly this guide — 36 in `render-preflight`, 3 in `render-preview`, 4 in `cv-doc`. The
52 skips belong to other guides (`effectiveCaps` / `toggleSection` → section toggles, `labelFor` →
education degree, plus the dormant executor-rework SPA blocks). Anything else red is yours.

The hook is not unit-tested (hooks are still deferred, memory `frontend-test-layout`) — everything
it decides lives in `fit.ts`.

## Verification

1. Suite red → green; `npx tsc -b`; `npx eslint src` (the hook's `set-state-in-effect` rule is
   strict here — the derived-state shape above is what passes it).
2. Open an application whose CV is comfortably under one page. Within about a second of the editor
   settling, the status line reads `1 of 1 page(s) used — N extra entries added to fill it`, and
   those entries carry a green **fills the page** badge.
3. Export it: the PDF contains exactly the badged entries, and the toast/preview footer says the
   same N. Editor and export must agree — if they don't, the cache key is missing an input.
4. Add entries by hand until it overflows _slightly_: the first thing that happens is a deep job
   picking up a grey **title only** badge — not an amber **won't fit**. Export and check: that job's
   heading and dates are still on the page, its description is gone. Push it further and the amber
   drops start once the demotions are spent.
5. Click the detail toggle on the top job to force it compact, and on a deep one to force it full:
   both must survive save + reload, and both must beat whatever the fit wanted (the forced-full job
   keeps its description even while the fit is looking for lines to cut).
6. Type continuously in the letter body for ~10 s: the CPU should show _one_ measurement after you
   stop, not one per keystroke.
7. Paste a very long letter: the "runs to N pages" warning appears under the textarea.
8. Switch layouts (1-page ↔ 2-page): the preflight re-measures and the badges move.
9. Tick **include QR** in the export card and download: the page count must match what the editor
   showed (the QR is layout-invariant; the cache simply misses because the contact line changed).

## Follow-up — Results round 1 (2026-08-07)

Two items from the Results below: the **"won't fit" badge is unreliable**, and the compact
**certifications / languages** blocks look silly full width. Both diagnosed, coded and verified in a
worktree off `170f734` with your implementation in place (`tsc -b` clean, eslint clean, 409 green);
the tests are on disk and red (12).

### 8. Why "won't fit" comes and goes — two causes, both reproduced

**Cause 1: the cap's cuts are reported nowhere.** `fitContent` caps _before_ it searches, and
`droppedIds` only ever names what the _reduce_ pass dropped out of the already-capped content. An
entry the **cap** removed is invisible to every id list the result carries. Reproduced:

```
9 jobs, cap 5 (pool = 3 by headroom), page holds 7
→ content  [job:1 … job:7]
→ added    [job:6, job:7]          ← green badge, correct
→ dropped  []                      ← job:8 and job:9 are simply *gone*, unbadged
```

That is the whole "sometimes it shows, sometimes not": you see amber only when the CV overflows the
_page_. The much commoner case — more entries than the section's budget — went silent, and worse,
its neighbours two rows up got a cheerful green **fills the page**. Before this guide, `overCapIds`
covered exactly this; deleting it left the hole.

**Cause 2: the cap ignores pins.** `capContent` slices by rank, so a pinned entry below the cap line
is cut like any other; the grow pass then offers it back _only_ if the page has room. Reproduced:

```
jobs 1–5 + a pinned job:9 at position 6, cap 5, page holds 5
→ capped   [job:1 … job:5]          ← the pin is already gone
→ content  [job:1 … job:5]
→ dropped  []  added  []            ← the pin vanished, and nothing says so
```

That is your "pinned, but low position" case. A pin is the user saying _this one is on the CV_; the
editorial cap does not get to overrule it silently. The page fit still may — it drops pins last —
but that at least gets reported.

**8a. `capContent` keeps pins** (`fit.ts`):

```ts
const cap = maxEntries[section];
// A pin is the user saying "this one is on the CV" — the editorial cap does not
// get to overrule it. The page fit still can, and drops pins last (`dropOrder`).
out[section] = cap != null ? list.filter((e, i) => i < cap || e.pinned) : list;
```

**8b. `beyondCap` skips them**, or a never-cut entry gets badged as added back:

```ts
const cap = maxEntries[section];
if (cap == null) continue;
// Pinned entries past the cap are already kept by `capContent` — offering them
// again would badge a never-cut entry as "added to fill the page".
out[section] = list
  .slice(cap, Math.ceil(cap * headroom))
  .filter((e) => !e.pinned);
```

**8c. `PreflightResult` grows `cutIds`** — everything handed in that the render does not show, minus
what is already reported as a page-fit drop. The two lists are disjoint and together account for
every missing entry:

```ts
export type PreflightResult = ReduceResult & {
  addedIds: string[];
  /** In the content, but not on the page and not a page-fit drop: what the template
   *  cap cut and the grow pass did not buy back. Disjoint from `droppedIds`. */
  cutIds: string[];
};

const idsOf = (c: CvContent) =>
  Object.values(c).flatMap((list) => list.map((e) => e.id));

/** Everything the caller handed in that the result does not render, minus what is
 *  already reported as a page-fit drop. */
function cutBy(full: CvContent, kept: CvContent, dropped: string[]): string[] {
  const present = new Set(idsOf(kept));
  const reported = new Set(dropped);
  return idsOf(full).filter((id) => !present.has(id) && !reported.has(id));
}
```

and both `fitContent` returns fill it in:

```ts
if (fit.droppedIds.length > 0 || fit.demotedIds.length > 0 || !fit.fits)
  return {
    ...fit,
    addedIds: [],
    cutIds: cutBy(full, fit.content, fit.droppedIds),
  };
```

```ts
return {
  content: grown.content,
  demotedIds: [],
  droppedIds: [],
  addedIds: grown.addedIds,
  cutIds: cutBy(full, grown.content, []),
  pages: grown.pages,
  fits: true,
};
```

**8d. `content-card.tsx` badges both, with the reason in the tooltip.** Next to `willCut`:

```tsx
// Past the section's template budget and not bought back by the grow pass: also
// absent from the CV, but for a different reason, so the tooltip differs.
const overBudget = new Set(preflight.result?.cutIds ?? []);
```

pass `overBudget={overBudget}` alongside `willCut`, add `overBudget: Set<string>` to
`CvEditorSection`'s props, and inside the entry map:

```tsx
const isOver = willCut.has(e.id) || overBudget.has(e.id);
```

```tsx
              {isOver && (
                <span
                  title={
                    overBudget.has(e.id)
                      ? `Past this section's layout budget (${cap}) — it is not on the rendered CV. Reorder it up, or pin it to force it in.`
                      : "The page fit has to cut this entry — deselect or shorten something else to keep it."
                  }
                >
```

and the amber badge fires on `isOver` rather than on `willCut` alone. One badge text for both — the
user's question is "is this on my CV?", and the tooltip answers "why not".

`preview.ts` is **not** touched: `cutIds` stays out of `fitNotices`. A per-entry badge in the editor
is the right place for it; in a send-time toast it would fire on nearly every export, and the
machine layer already lists those entries as `cut_for_space`. There is a test pinning that decision
so it doesn't get "fixed" later by accident.

### 9. Certifications and languages side by side

Full width each, two three-line blocks read as more important than they are and eat the bottom of
the page (screenshot `.claude/screenshots/pdf_preview6.png`). Side by side they stay visible and
stop shouting — and in a half-width column they read better as one entry per line than as one
run-on joined sentence.

**9a. the spec grammar** (`spec.ts`) — a nested array inside `sidebar` is "these render as one row".
Kept inside `sidebar` rather than in a second field so the one list still reads top-to-bottom the
way the page does:

```ts
    /** Compact sections after the main flow. A nested array is one row of equal
     *  columns — the layout's way of saying "these two side by side". */
    sidebar: (string | string[])[];
```

`FALLBACK_SPEC` becomes `sidebar: ["skills", ["certifications", "languages"]]`, and the parser
learns the nesting (next to the existing `sections` helper):

```ts
const sidebarGroups = (
  names: (string | string[])[] | undefined,
  fallback: (string | string[])[],
): (string | string[])[] =>
  (names ?? fallback).map((n) =>
    Array.isArray(n)
      ? n.map((s) => LEGACY_SECTIONS[s] ?? s)
      : (LEGACY_SECTIONS[n] ?? n),
  );
```

used as `sidebar: sidebarGroups(r.cv?.sidebar, f.cv.sidebar)`.

**9b. the stacked column** (`templates.tsx`) — `CvSectionView` gains one flag:

```tsx
  compact,
  stacked,
```

```tsx
  /** Inside a side-by-side row: one entry per line instead of one joined run, so a
   *  half-width column reads as a list rather than as wrapped prose. */
  stacked?: boolean;
```

and, immediately **before** the existing `if (compact) {` branch:

```tsx
if (compact && stacked) {
  return (
    <View>
      <Text style={styles.sectionTitle}>{SECTION_TITLES[section]}</Text>
      {entries.map((e) => {
        const p = entryParts(db, section, e);
        // The qualifier column, same idiom as the main flow's dates and the skills
        // block's category labels: whatever the entry is *rated* by goes left, muted,
        // and the name reads down a clean edge. A certification is qualified by when it
        // was issued, a language by how well it is spoken — one of the two is always
        // empty, so this is one rule, not a section switch.
        const hint = p.dateFrom || p.meta;
        return (
          <View key={e.id} style={styles.stackedRow}>
            <Text style={styles.stackedHints}>{hint}</Text>
            <Text style={[styles.content, styles.stackedLine]}>{p.heading}</Text>
          </View>
        );
      })}
    </View>
  );
}
```

**9c. the styles** (`cvStyles`, next to `compact`):

```ts
    // Side-by-side compact sections: equal columns, the gutter on every column but
    // the last. Keeps two short blocks (certifications, languages) from each eating a
    // full-width line and reading as more important than they are.
    sideRow: { flexDirection: "row" },
    sideCol: { flex: 1 },
    sideGutter: { paddingRight: base * 2 },
    stackedRow: { flexDirection: "row", marginBottom: base / 6 },
    // Narrower than the main flow's `hints` (27mm), because this sits inside a
    // half-width column — but wide enough for the longest real qualifier,
    // "conversational" (~52pt at 7.5pt), so it never wraps.
    stackedHints: {
      width: mm(20),
      color: spec.colors.muted,
      fontSize: small,
      paddingRight: base * 0.4,
    },
    // Same lineHeight trap as `summary`: declare the fontSize the multiplier resolves
    // against, or 1.3 silently means 1.3 × react-pdf's 18pt default.
    stackedLine: { fontSize: small, lineHeight: 1.3 },
```

(No `gap` — `paddingRight` on all but the last column is the version I verified renders.)

**9d. `CvPages`** — the sidebar map becomes group-aware:

```tsx
{
  spec.cv.sidebar.map((group) => {
    if (!Array.isArray(group)) {
      return (
        <CvSectionView
          key={group}
          section={group as SectionKey}
          content={content}
          db={db}
          styles={styles}
          compact
        />
      );
    }
    // An empty section must not hold a column open — with one survivor the row
    // collapses to the plain full-width rendering.
    const present = group.filter((s) => (content[s] ?? []).length > 0);
    if (present.length === 0) return null;
    if (present.length === 1) {
      return (
        <CvSectionView
          key={present[0]}
          section={present[0] as SectionKey}
          content={content}
          db={db}
          styles={styles}
          compact
        />
      );
    }
    return (
      <View key={group.join("+")} style={styles.sideRow}>
        {present.map((s, i) => (
          <View
            key={s}
            style={
              i < present.length - 1
                ? [styles.sideCol, styles.sideGutter]
                : styles.sideCol
            }
          >
            <CvSectionView
              section={s as SectionKey}
              content={content}
              db={db}
              styles={styles}
              compact
              stacked
            />
          </View>
        ))}
      </View>
    );
  });
}
```

The single-survivor collapse is the case you actually hit: with certifications deselected, languages
must not sit in half a page next to a blank column.

**9e. the stored layouts.** `backend/jac/resources/default_layout.json` **and**
`two_page_layout.json` both need

```json
    "sidebar": ["skills", ["certifications", "languages"]],
```

followed by `cd backend && python manage.py seed_system_defaults` — without the reseed the DB keeps
serving the old spec and the page will not change, whatever the frontend does.

### Tests (round 1)

Red set: **12 failed | 397 passed | 52 skipped**.

| file                       | new                                                                                                                                                                                                                                                                                             |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `render-fit.test.ts`       | `capContent` keeps a pinned entry past the cap.                                                                                                                                                                                                                                                 |
| `render-preflight.test.ts` | `beyondCap` skips pins; `cutIds` reports the cap's cuts, stays disjoint from `droppedIds`, and the two together account for every missing entry; empty when everything fits; a pinned deep entry survives the cap, is not counted as "added", and whatever the page fit drops instead is named. |
| `render-preview.test.ts`   | the `PreflightResult` fixture gains `cutIds`; `fitNotices` stays quiet about cap cuts (pins the decision above).                                                                                                                                                                                |
| `render-spec.test.ts`      | nested groups parse, legacy names inside a group are normalized, the fallback pairs certifications with languages.                                                                                                                                                                              |
| `render-templates.test.ts` | real render: the two headings share a line in two columns; a column stacks its entries one per line; a single survivor collapses to exactly the full-width rendering.                                                                                                                           |

The render assertions use `pdfPositionedRuns` / `runAt` — x/y of the painted runs, so they test
_alignment_, which is the actual claim, instead of mere presence.

### Verification (round 1)

1. Suite red → green; `npx tsc -b`; `npx eslint src`.
2. Reseed (`python manage.py seed_system_defaults`), reload an application, **Preview PDF**:
   Certifications and Languages sit next to each other, one entry per line, under a full-width
   Skills block.
3. Deselect every certification: Languages goes back to full width, no blank column.
4. The badge cases you listed, in the editor — each should now be unambiguous:
   - an entry past the section budget (e.g. the 8th job with a cap of 5, beyond the grow headroom) →
     amber **won't fit**, tooltip naming the budget;
   - the same entry pinned → **no badge**, and it appears in the exported PDF;
   - an entry the page fit cuts → amber **won't fit**, tooltip naming the page;
   - an entry past the cap that the grow pass takes back → green **fills the page** (unchanged).
5. Export and diff against the badges: every entry with an amber badge is absent from the PDF, every
   entry without one is present. That is the invariant the whole preflight rests on.

## Results

<!-- human: raw test output, observed issues, what works -->

### Round 1 (2026-08-06)

tests are green,
"fills the page" info shows up
the "wont fit" warning is not accurate or doesn't show always. haven't figure out, when its missing and when it shows. so we need to check the edge cases here

- pinned, but low possition
- medium position, but not pinned,
- etc.

on a different note, check the screen shot
![/Users/lukas/Projects/jac/.claude/screenshots/pdf_preview6.png](pdf_preview6.png)
@.claude/screenshots/pdf_preview6.png

the certification tab has been updated, but i had forgotten to verify it (certs had been deselected in my standard application). this way it looks really stupid. we had a pin in the language layout anyways. theyu are similar in layout right now, here is my idea: have them next to each other (multicomlumn layout). makes them less important, but still shows everything

### Round 2

first: the cv uses the space very good, and therefore i am quite happy with the result, so anything i say now is nit picking on high level and we really need to decide if changing thnigs make it better

1. question regarding skill section: since it is just more like a skill cloud, i was wondering, why skills wont fit,
2. the language and certification segment still look like before. have you condsidered the multicoloumn layout like i asked in the last revision? why have you decided against it?
