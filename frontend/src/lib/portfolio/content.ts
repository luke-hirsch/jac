import type { PortfolioItem, RankedId } from "@/lib/queries/portfolio";

/** Reorder the "more" list by a rank result (the `?q=` embed finale). Ranked ids sort
 *  to the front by descending score; unranked items keep their natural order behind
 *  them (Array.sort is stable). Never filters — a visitor's interest reorders, it
 *  doesn't hide. Ranked ids absent from `more` are simply ignored. */
export function reorderByRank(
  more: PortfolioItem[],
  ranked: RankedId[],
): PortfolioItem[] {
  const score = new Map(ranked.map((r) => [r.id, r.score]));
  return [...more].sort((a, b) => {
    const sa = score.get(a.id);
    const sb = score.get(b.id);
    if (sa === undefined && sb === undefined) return 0;
    if (sa === undefined) return 1;
    if (sb === undefined) return -1;
    return sb - sa;
  });
}

/** A native/link payload with nothing to show. The visitor must never be trapped on a
 *  blank page — portfolio-page renders an empty-state + the escape hatch instead. Takes
 *  the *resolved* lists (a rank reorder never changes counts, so payload.more is fine). */
export function isEmptyPayload(
  featured: PortfolioItem[],
  more: PortfolioItem[],
): boolean {
  return featured.length === 0 && more.length === 0;
}
