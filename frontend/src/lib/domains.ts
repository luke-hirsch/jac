/**
 * The words and lists behind the domain manager (the CV pages' "Manage domains"). Only the
 * user's own domains can be renamed, merged or deleted; jac's defaults are merge targets.
 */
import type { DomainRow } from "@/lib/queries/jac";

const byName = (a: DomainRow, b: DomainRow) =>
  a.name.localeCompare(b.name, undefined, { sensitivity: "base" });

/** The user's own domains, by name: the ones the manager edits. */
export function ownDomains(rows: DomainRow[]): DomainRow[] {
  return rows.filter((d) => !d.is_default).sort(byName);
}

/** Where a domain can be merged: every other one the user sees, their own and jac's
 *  defaults apart, each by name. */
export function mergeTargets(
  rows: DomainRow[],
  sourceId: number,
): { own: DomainRow[]; defaults: DomainRow[] } {
  const others = rows.filter((d) => d.id !== sourceId).sort(byName);
  return {
    own: others.filter((d) => !d.is_default),
    defaults: others.filter((d) => d.is_default),
  };
}

/** "1 entry", "3 entries". */
export function entriesLabel(n: number): string {
  return `${n} ${n === 1 ? "entry" : "entries"}`;
}

/** What the list shows beside a name: a typo stands out by its 1. */
export function usesLabel(uses: number): string {
  return uses === 0 ? "Not used" : entriesLabel(uses);
}

export function deletePrompt(domain: DomainRow): string {
  const effect =
    domain.uses === 0
      ? "No entry uses it."
      : `${entriesLabel(domain.uses)} ${domain.uses === 1 ? "loses" : "lose"} it.`;
  return `Delete "${domain.name}"? ${effect}`;
}

export function mergePrompt(source: DomainRow, target: DomainRow): string {
  const effect =
    source.uses === 0
      ? `No entry uses "${source.name}".`
      : `Its ${entriesLabel(source.uses)} get "${target.name}".`;
  return `Merge "${source.name}" into "${target.name}"? ${effect} Then "${source.name}" is deleted.`;
}

/** The filter on a CV page after a domain went: it follows a merge to the target, and a
 *  deleted one clears it (a filter on a gone id would make the list a 400). */
export function filterAfterRemoval(
  value: number | "",
  removedId: number,
  replacement: number | null,
): number | "" {
  if (value !== removedId) return value;
  return replacement ?? "";
}
