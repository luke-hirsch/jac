/**
 * German letter register: which pronouns contradict the letter's form of "you".
 *
 * Mirror of backend `jac/register.py` `register_leaks` — same patterns, same
 * sentence-start rule; keep the two in step (the tests share their cases). The server's
 * check only ever saw the body a run produced; this copy re-checks every edit and every
 * AI rewrite in the editor, for free. A flag, never a rewrite — same as the server.
 */

/** Every form a letter can carry, in register.py's order. "" = none / not German. */
export const ADDRESS_FORMS = ["sie", "ihr", "du"] as const;
export type AddressForm = (typeof ADDRESS_FORMS)[number];

/** How each form is written in running text — "Sie" is capitalised, the others not. */
export const FORM_WORD: Record<AddressForm, string> = {
  sie: "Sie",
  ihr: "ihr",
  du: "du",
};

/** The register only exists in German ("de", "de-AT", …). */
export function isGerman(language: string): boolean {
  return (language ?? "").toLowerCase().slice(0, 2) === "de";
}

// Python's \b is Unicode-aware; JS's is ASCII-only even with /u, so "Büdu" would split
// at the "ü" and leak a "du". These lookarounds are the Unicode word boundary.
const W = "[\\p{L}\\p{N}_]";
const word = (alternatives: string, flags = "") =>
  new RegExp(`(?<!${W})(?:${alternatives})(?!${W})`, `gu${flags}`);

// Case-sensitive on purpose: lowercase "sie" is she/they.
const FORMAL = word("Sie|Ihnen|Ihr|Ihre[mnrs]?");
// Bare "ihr" is left out (also "her"/"their"); spelled out so "EUR" can't match.
const PLURAL = word("euch|euer|eure[mnrs]?|euere[mnrs]?", "i");
const SINGULAR = word("du|dich|dir|dein|deine[mnrs]?", "i");

// At a sentence start capitalisation says nothing: "Sie" may be "they", "Ihr" the
// plural you. "Ihnen"/"Ihre…" keep counting there.
const AMBIGUOUS_AT_START = new Set(["Sie", "Ihr"]);
const SENTENCE_START = /(?:^|[.!?:;]\s*|\n\s*)$/;

const WRONG: Record<AddressForm, RegExp[]> = {
  ihr: [FORMAL, SINGULAR],
  sie: [PLURAL, SINGULAR],
  du: [FORMAL, PLURAL],
};

function hits(pattern: RegExp, text: string): { at: number; word: string }[] {
  const out: { at: number; word: string }[] = [];
  for (const m of text.matchAll(pattern)) {
    const at = m.index ?? 0;
    if (
      pattern === FORMAL &&
      AMBIGUOUS_AT_START.has(m[0]) &&
      SENTENCE_START.test(text.slice(0, at))
    )
      continue;
    out.push({ at, word: m[0] });
  }
  return out;
}

/** Pronouns in `body` that contradict `form`, deduped, in order of appearance. [] for
 *  non-German letters and for no form. */
export function registerLeaks(
  body: string,
  language: string,
  form: string,
): string[] {
  if (!isGerman(language) || !form) return [];
  const patterns = WRONG[form as AddressForm] ?? [];
  const found = patterns
    .flatMap((p) => hits(p, body ?? ""))
    .sort((a, b) => a.at - b.at)
    .map((h) => h.word);
  return [...new Set(found)];
}
