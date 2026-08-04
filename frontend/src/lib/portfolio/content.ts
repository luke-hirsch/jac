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

/** DOM id for an item card, from its `type:pk` id ("job:12" → "item-job-12"). A colon is
 *  legal in an id but awkward in a URL fragment / CSS selector, so it becomes a dash. */
export function anchorId(id: string): string {
  return `item-${id.replace(":", "-")}`;
}

/** The ids that render as their own card on the page — the jump targets a block's nested
 *  links may point at. Nested items are *not* included: they aren't standalone cards. */
export function pageAnchors(
  featured: PortfolioItem[],
  more: PortfolioItem[],
): Set<string> {
  return new Set([...featured, ...more].map((i) => i.id));
}

export type LinkTarget =
  | { kind: "anchor"; href: string }
  | { kind: "external"; href: string }
  | null;

/** Where a linked item's title points. On-page wins: if the same entry also renders as a
 *  card (it survived `_drop_claimed` — a block, or an owner-curated `featured` entry), jump
 *  there instead of leaving the site. Otherwise fall back to the entry's own url (project /
 *  company / credential). Neither ⇒ plain text; the item is still shown, just not clickable. */
export function linkTarget(
  item: PortfolioItem,
  anchors: Set<string>,
): LinkTarget {
  if (anchors.has(item.id)) return { kind: "anchor", href: `#${anchorId(item.id)}` };
  if (item.url) return { kind: "external", href: item.url };
  return null;
}

/** The extra "↗" an anchored item still deserves: its own url, when the title link is
 *  already spoken for by the on-page jump. Empty ⇒ no icon (never a dead link). */
export function outboundUrl(item: PortfolioItem, target: LinkTarget): string {
  return target?.kind === "anchor" && item.url ? item.url : "";
}
