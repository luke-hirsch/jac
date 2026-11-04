/**
 * Three-way merge for the application editor's drafts. The server copy of a field moves for
 * reasons the editor doesn't control (a run's auto-fill, an applied run, the echo of a Save)
 * while the draft may hold unsaved typing. One rule, per field: follow the server only where
 * the draft still equals the previous server value. Values are strings (JSON for the
 * structured fields), the form the editor compares them in anyway.
 */

/** keep = nothing to do; take = the draft has no edits, follow the server; conflict = both
 *  moved, and the draft stays: unsaved typing is never overwritten. */
export type Reseed = "keep" | "take" | "conflict";

export function reseedField(prev: string, next: string, draft: string): Reseed {
  if (next === prev || draft === next) return "keep";
  return draft === prev ? "take" : "conflict";
}

/**
 * `reseedField` over every field of a draft. `sent` is what the last Save sent. A draft still
 * equal to it has no edits of its own, so a server that normalised the echo gets followed
 * (otherwise Save could never clear `dirty`). An echo of it is not news: typing since that
 * Save is unsaved, not in conflict with anything.
 */
export function reseedDrafts<K extends string>(
  prev: Record<K, string>,
  next: Record<K, string>,
  draft: Record<K, string>,
  sent: Record<K, string> | null,
): { take: K[]; conflicts: K[] } {
  const take: K[] = [];
  const conflicts: K[] = [];
  for (const k of Object.keys(next) as K[]) {
    if (next[k] === prev[k]) continue; // this field's server copy didn't move
    const base = draft[k] === sent?.[k] ? draft[k] : prev[k];
    const outcome = reseedField(base, next[k], draft[k]);
    if (outcome === "take") take.push(k);
    if (outcome === "conflict" && next[k] !== sent?.[k]) conflicts.push(k);
  }
  return { take, conflicts };
}

/** DRF's CharField trims on write, so whitespace at the ends of a text field can't be saved
 *  and isn't an edit: compare drafts in this form, or a letter ending in a newline stays dirty
 *  after its Save. */
export function storedText(text: string): string {
  return text.trim();
}
