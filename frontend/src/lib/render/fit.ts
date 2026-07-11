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

/** Per-section floor the auto-fit never drops below (default 1 per non-empty section). */
export const MIN_KEEP: Record<string, number> = { skills: 3 };
const minKeep = (section: string) => MIN_KEEP[section] ?? 1;

/**
 * Ids in drop-first order. Ties: bigger section first, then section name. Favourites
 * (via `isFavourite`, built from the career DB) drop only after every non-favourite.
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
      });
    });
  }
  cands.sort(
    (a, b) =>
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
    out[section] = cap != null ? list.slice(0, cap) : list;
  }
  return out;
}

/**
 * Ids past the template budget, counting only active (non-deselected) entries — the
 * editor's warning set: these render nowhere unless the user trims elsewhere.
 * Deselected entries are never flagged (they don't render at all).
 */
export function overCapIds(
  content: CvContent,
  maxEntries: Record<string, number>,
): Set<string> {
  const over = new Set<string>();
  for (const [section, list] of Object.entries(content)) {
    const cap = maxEntries[section];
    if (cap == null) continue;
    let active = 0;
    for (const e of list) {
      if (e.deselected) continue;
      active += 1;
      if (active > cap) over.add(e.id);
    }
  }
  return over;
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

export type FitResult = {
  content: CvContent;
  droppedIds: string[];
  pages: number;
  fits: boolean; // false: even the min-keep floor overflows the budget
};

/**
 * Smallest drop count that fits `maxPages`, by binary search — the page count is
 * monotonically non-increasing in the drop count. `pagesFor` renders a candidate and counts
 * its pages (the only impure part, injected: ~log2(n) renders per export).
 */
export async function fitCv(
  content: CvContent,
  maxPages: number,
  pagesFor: (c: CvContent) => Promise<number>,
  isFavourite?: (id: string) => boolean,
): Promise<FitResult> {
  const order = dropOrder(content, isFavourite);
  const pagesAt = (k: number) =>
    pagesFor(applyDrop(content, order.slice(0, k)));

  const full = await pagesAt(0);
  if (full <= maxPages)
    return { content, droppedIds: [], pages: full, fits: true };

  let lo = 0; // known: doesn't fit
  let hi = order.length;
  let hiPages = await pagesAt(hi);
  if (hiPages > maxPages) {
    return {
      content: applyDrop(content, order),
      droppedIds: order,
      pages: hiPages,
      fits: false,
    };
  }
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
  return {
    content: applyDrop(content, order.slice(0, hi)),
    droppedIds: order.slice(0, hi),
    pages: hiPages,
    fits: true,
  };
}
