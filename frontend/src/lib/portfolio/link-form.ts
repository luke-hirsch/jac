/** Pure list algebra for the manual-link "featured content" picker.
 *
 * `content.featured` is an ordered array of mixed ids — career-DB entries
 * ("job:12", the `lib/cv-doc` grammar) and portfolio blocks ("block:7"). The pool of
 * pickable items is the live career DB plus the owner's blocks; the featured array is
 * an ordered subset of their ids. Everything here is immutable and HTTP-free — the
 * `frontend/tests/` regime's sweet spot (mixed-id list ops).
 */
import {
  SECTION_ORDER,
  entryId,
  labelFor,
  parseEntryId,
  type AnyRow,
} from "@/lib/cv-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { PortfolioBlockRow } from "@/lib/queries/portfolio";

export type FeaturedCandidate = {
  id: string; // "job:12" | "block:7"
  type: string; // career singular ("job", "skill", …) or "block"
  label: string;
};

/** Group heading a candidate falls under in the picker (matches cv-doc section titles
 *  where it can; blocks get their own group). */
export const CANDIDATE_GROUPS = [
  "jobs",
  "educations",
  "projects",
  "skills",
  "certifications",
  "languages",
  "blocks",
] as const;

/** The full pool: every career entry (section order, cv-doc labels) then every block.
 *  A block's label is its title, falling back to "<kind> block" for untitled ones. */
export function candidates(
  db: CvEntriesResponse | undefined,
  blocks: PortfolioBlockRow[],
): FeaturedCandidate[] {
  const out: FeaturedCandidate[] = [];
  if (db) {
    for (const section of SECTION_ORDER) {
      for (const row of db[section] as AnyRow[]) {
        const id = entryId(section, row.id);
        out.push({ id, type: parseEntryId(id)!.type, label: labelFor(section, row) });
      }
    }
  }
  for (const b of blocks) {
    out.push({
      id: `block:${b.id}`,
      type: "block",
      label: b.title || `${b.kind} block`,
    });
  }
  return out;
}

/** The featured ids resolved to candidates, in featured order. Ids no longer in the
 *  pool (a deleted career row / block) drop silently — the cv-doc philosophy. */
export function resolveFeatured(
  featured: string[],
  pool: FeaturedCandidate[],
): FeaturedCandidate[] {
  const byId = new Map(pool.map((c) => [c.id, c]));
  return featured
    .map((id) => byId.get(id))
    .filter((c): c is FeaturedCandidate => c !== undefined);
}

/** Add an id to the end, or remove it if already featured. */
export function toggleFeatured(featured: string[], id: string): string[] {
  return featured.includes(id)
    ? featured.filter((f) => f !== id)
    : [...featured, id];
}

/** Swap the entry at `index` with its neighbour (mirrors cv-doc `moveEntry`, flat). */
export function moveFeatured(
  featured: string[],
  index: number,
  delta: -1 | 1,
): string[] {
  const target = index + delta;
  if (
    index < 0 ||
    index >= featured.length ||
    target < 0 ||
    target >= featured.length
  ) {
    return featured;
  }
  const next = [...featured];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** Drop any featured id whose row/block no longer exists — call on load so a saved
 *  link doesn't carry ghosts the picker can't render. Order preserved. */
export function pruneFeatured(
  featured: string[],
  pool: FeaturedCandidate[],
): string[] {
  const ids = new Set(pool.map((c) => c.id));
  return featured.filter((id) => ids.has(id));
}

/** Toggle a plain string in a list — the link's domain-*name* picker (blocks use pks,
 *  a different picker). Kept here so both are unit-covered. */
export function toggleName(names: string[], name: string): string[] {
  return names.includes(name)
    ? names.filter((n) => n !== name)
    : [...names, name];
}
