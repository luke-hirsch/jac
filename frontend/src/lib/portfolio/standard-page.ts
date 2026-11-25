import type {
  StandardPageInput,
  StandardPageRow,
} from "@/lib/queries/portfolio";

/** The standard-page card's form: the owner's title, intro and Highlights for the page their
 *  portfolio shows a visitor who skips the questions. Pure, so the tests/ regime covers it. */

export type StandardDraft = {
  title: string;
  intro: string;
  featured: string[]; // "job:12" | "block:7" …, in the owner's order
};

/** The saved page → the form. */
export function standardDraft(row: StandardPageRow): StandardDraft {
  return {
    title: row.title,
    intro: row.intro,
    featured: row.content.featured,
  };
}

/** The form → the PATCH body. The whole page every time, so what's saved is what the form
 *  shows. Trimmed like the server trims, so `standardChanged` compares what would be stored. */
export function standardBody(draft: StandardDraft): StandardPageInput {
  return {
    title: draft.title.trim(),
    intro: draft.intro.trim(),
    content: { featured: draft.featured },
  };
}

/** Nothing of the owner's own: visitors see the favourites under the display name and the bio,
 *  exactly as before there was a standard page. */
export function isStandardEmpty(draft: StandardDraft): boolean {
  const body = standardBody(draft);
  return !body.title && !body.intro && body.content.featured.length === 0;
}

/** Would a save change anything? Order counts: it's the order visitors see. */
export function standardChanged(
  row: StandardPageRow,
  draft: StandardDraft,
): boolean {
  const saved = standardBody(standardDraft(row));
  const next = standardBody(draft);
  return (
    saved.title !== next.title ||
    saved.intro !== next.intro ||
    saved.content.featured.join() !== next.content.featured.join()
  );
}
