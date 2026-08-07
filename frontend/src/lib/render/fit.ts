/**
 * Fit the (active) cv_content to a page budget by dropping ranked tail entries.
 *
 * Ranking is scale-free by design: within a section the stored order IS the rank, but the
 * scores are incomparable across rungs and sections (light = cosine, standard = 0–3 labels,
 * strong = none — see memory `no-json-llm-io` / `project_jac`). So the drop order uses
 * *position fraction* within the section — the deepest tail entry relative to its section's
 * size drops first — rather than raw scores.
 */
import type { CvContent } from "@/lib/cv-doc";
import { MIN_DETAILED } from "./parts";
import type { LayoutSpec } from "./spec";

/** Per-section floor the auto-fit never drops below (default 1 per non-empty section). */
export const MIN_KEEP: Record<string, number> = { skills: 3 };
const minKeep = (section: string) => MIN_KEEP[section] ?? 1;

/**
 * Ids in drop-first order. Ties: bigger section first, then section name. Favourites
 * (via `isFavourite`, built from the career DB) drop only after every non-favourite;
 * pinned entries (explicit per-application intent) drop last of all.
 * The first `minKeep(section)` entries of each section are never dropped.
 */
export function dropOrder(
  content: CvContent,
  isFavourite: (id: string) => boolean = () => false,
): string[] {
  type Cand = {
    id: string;
    frac: number;
    size: number;
    section: string;
    fav: boolean;
    pin: boolean;
  };
  const cands: Cand[] = [];
  for (const [section, list] of Object.entries(content)) {
    const floor = minKeep(section);
    list.forEach((e, i) => {
      if (i < floor) return;
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
      Number(a.pin) - Number(b.pin) ||
      Number(a.fav) - Number(b.fav) ||
      b.frac - a.frac ||
      b.size - a.size ||
      a.section.localeCompare(b.section),
  );
  return cands.map((c) => c.id);
}

export function applyDrop(content: CvContent, ids: string[]): CvContent {
  const dropped = new Set(ids);
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    out[section] = list.filter((e) => !dropped.has(e.id));
  }
  return out;
}

/**
 * Cut each section to the template's entry budget (LayoutSpec.cv.max_entries) — the
 * hard editorial cap applied *before* the page-budget fit. Order is rank, so the cap
 * keeps the top of each section. Sections without a cap pass through untouched.
 */
export function capContent(
  content: CvContent,
  maxEntries: Record<string, number>,
): CvContent {
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    const cap = maxEntries[section];
    // A pin is the user saying "this one is on the CV" — the editorial cap does not
    // get to overrule it. The page fit still can, and drops pins last (`dropOrder`).
    out[section] =
      cap != null ? list.filter((e, i) => i < cap || e.pinned) : list;
  }
  return out;
}

/**
 * Rough vertical cost of one entry in each section, in "lines". Only the ratios matter —
 * they convert an entry budget into a page-space budget, which is the thing a switched-off
 * section actually frees. A job is a heading + meta + a few description lines; a skill is a
 * fraction of one joined sidebar line.
 */
export const SECTION_WEIGHT: Record<string, number> = {
  jobs: 5,
  educations: 4,
  projects: 4,
  certifications: 2,
  skills: 1,
  languages: 1,
};
const weight = (section: string) => SECTION_WEIGHT[section] ?? 2;

/** Growth is clamped: one toggle should loosen the layout, not abolish it. */
export const MAX_CAP_GROWTH = 2;

/**
 * The template's per-section caps, with the weight freed by switched-off sections spread
 * over the sections that remain, proportionally to what they already are. Switched-off
 * sections drop out of the result entirely.
 *
 * Deliberately NOT one-slot-for-one-slot: 4 certification slots are worth ~8 lines, which
 * is one and a half jobs, not four. Integer caps round, so a small release can be
 * invisible on a small section — the page fit is what spends the difference.
 */
export function effectiveCaps(
  maxEntries: Record<string, number>,
  sectionsOff: string[] = [],
): Record<string, number> {
  const off = new Set(sectionsOff);
  let freed = 0;
  let kept = 0;
  for (const [section, cap] of Object.entries(maxEntries)) {
    const w = cap * weight(section);
    if (off.has(section)) freed += w;
    else kept += w;
  }
  const growth = kept > 0 ? Math.min(1 + freed / kept, MAX_CAP_GROWTH) : 1;
  const out: Record<string, number> = {};
  for (const [section, cap] of Object.entries(maxEntries)) {
    if (off.has(section)) continue;
    out[section] = Math.max(1, Math.round(cap * growth));
  }
  return out;
}

/**
 * Page count of a rendered PDF, from its object dictionaries: one "/Type /Page" per page
 * ("/Type /Pages" is the tree node — excluded). react-pdf/pdfkit writes dictionaries
 * uncompressed, so a latin1 decode of the bytes is scannable.
 */
export function countPdfPages(pdfText: string): number {
  const m = pdfText.match(/\/Type\s*\/Page(?![a-zA-Z])/g);
  return m ? m.length : 0;
}

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
    // Pinned entries past the cap are already kept by `capContent` — offering them
    // again would badge a never-cut entry as "added to fill the page".
    out[section] = list
      .slice(cap, Math.ceil(cap * headroom))
      .filter((e) => !e.pinned);
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

/* ---------- the orchestrator ---------- */

export type PreflightResult = ReduceResult & {
  addedIds: string[];
  /** In the content, but not on the page and not a page-fit drop: what the template
   *  cap cut and the grow pass did not buy back. Disjoint from `droppedIds`. */
  cutIds: string[];
};

const idsOf = (c: CvContent) =>
  Object.values(c).flatMap((list) => list.map((e) => e.id));

/** Everything the caller handed in that the result does not render, minus what is
 *  already reported as a page-fit drop. Between the two lists, every entry that is
 *  not on the page is named — which is what the editor's badges promise. */
function cutBy(full: CvContent, kept: CvContent, dropped: string[]): string[] {
  const present = new Set(idsOf(kept));
  const reported = new Set(dropped);
  return idsOf(full).filter((id) => !present.has(id) && !reported.has(id));
}

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
    return {
      ...fit,
      addedIds: [],
      cutIds: cutBy(full, fit.content, fit.droppedIds),
    };

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
    cutIds: cutBy(full, grown.content, []),
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
