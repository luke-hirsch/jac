/**
 * The user's writing rules: the two checks jac runs on a letter, in the editor.
 *
 * Mirror of backend `jac/style.py`: same patterns, same sentence split, same wording, so the
 * editor's warning reads like the one a run or an import gave. Keep the two in step (the tests
 * share their cases). The server only checks what a run or Claude produced; this copy re-checks
 * every edit and every AI rewrite. A flag, never a rewrite.
 */

export const NO_DASHES = "no_dashes";
export const NO_ANTITHESIS = "no_antithesis";
/** Each one is a toggle on the profile, `style_<name>`. */
export const CHECKS = [NO_DASHES, NO_ANTITHESIS] as const;
export type StyleCheck = (typeof CHECKS)[number];

const RULE: Record<StyleCheck, string> = {
  no_dashes: "no em or en dashes as punctuation",
  no_antithesis: "no 'not X but Y' antithesis",
};

export type LetterPart = "body" | "subject" | "salutation" | "closing";
const PART: Record<LetterPart, string> = {
  body: "The body",
  subject: "The subject",
  salutation: "The salutation",
  closing: "The closing",
};

const EM_DASH = "—";
const DASH = /[—–-]/g;
// Space on the line: a newline ends a clause, it doesn't separate one.
const HSPACE = " \t  ";

// Python's \b is Unicode-aware; JS's is ASCII-only even with /u (see register.ts). These
// lookarounds are the Unicode word boundary.
const W = "[\\p{L}\\p{N}_]";
const S = `(?<!${W})`;
const E = `(?!${W})`;

// Within one sentence, across its clauses. Specific before general, as in style.py.
const ANTITHESES = [
  `${S}nicht\\s+nur${E}.*?${S}sondern${E}(?:\\s+auch${E})?`,
  `${S}kein(?:e[mnr]?)?${E}.*?${S}sondern${E}(?:\\s+auch${E})?`,
  `${S}nicht${E}.*?${S}sondern${E}(?:\\s+auch${E})?`,
  `${S}not\\s+(?:just|only|merely)${E}.*?${S}but${E}`,
  `${S}(?:isn['’]t|is\\s+not|(?:it|this|that)['’]s\\s+not)${E}.*?(?:[,;:—–]|\\s-\\s)\\s*it['’]s${E}`,
  `${S}not\\s+an?${E}.*?${S}but\\s+an?${E}`,
].map((p) => new RegExp(p, "iu"));

// A full stop, !, ? or … before space ends a sentence, and so does a line break; a full stop
// after an abbreviation, an initial or a day ("z. B.", "bzw.", "e.g.", "am 1. März") doesn't.
const BREAK = /(?<=[.!?…])\s+|\n+/g;
const ABBREVIATIONS = new Set([
  // German
  "abs",
  "bzw",
  "ca",
  "d.h",
  "dipl",
  "dr",
  "evtl",
  "ggf",
  "inkl",
  "ing",
  "nr",
  "o.ä",
  "prof",
  "s.o",
  "s.u",
  "sog",
  "u.a",
  "u.u",
  "vgl",
  "z.b",
  "z.t",
  "zzgl",
  // English
  "approx",
  "e.g",
  "i.e",
  "mr",
  "mrs",
  "ms",
  "no",
  "vs",
]);
const OPENERS = "([{\"'„“‚‘»«";
const FRAGMENT_MAX = 120;

const words = (text: string) => text.split(/\s+/).filter(Boolean);
const isDigit = (c: string) => /^\p{Nd}$/u.test(c);

function stripEnd(text: string, chars: string): string {
  let end = text.length;
  while (end > 0 && chars.includes(text[end - 1])) end--;
  return text.slice(0, end);
}

function stripStart(text: string, chars: string): string {
  let start = 0;
  while (start < text.length && chars.includes(text[start])) start++;
  return text.slice(start);
}

/** The checks the user has on. Until the profile has loaded, none: a check the user turned
 *  off mustn't flash on (style.py's `style_checks` defaults to all, for a missing profile). */
export function styleChecks(
  profile:
    | { style_no_dashes?: boolean; style_no_antithesis?: boolean }
    | undefined,
): StyleCheck[] {
  if (!profile) return [];
  return CHECKS.filter((c) => profile[`style_${c}`] !== false);
}

/** Whether the dash at `i` separates clauses. Every em dash does. An en dash does with space
 *  beside it; a hyphen only as " - ". Neither does in a range (2020–2023, S. 4–6, 10 - 12), a
 *  compound (Django-App, E-Commerce, Front- und Backend), before a number (–20 %) or as a
 *  bullet at the start of a line. */
function isSeparator(text: string, i: number): boolean {
  const dash = text[i];
  if (dash === EM_DASH) return true;
  const lineStart = i > 0 ? text.lastIndexOf("\n", i - 1) + 1 : 0;
  if (!text.slice(lineStart, i).trim()) return false; // a bullet
  const after = text.slice(i + 1, i + 2); // "" at the end of the text
  const spaceBefore = HSPACE.includes(text[i - 1]);
  const spaceAfter = after !== "" && HSPACE.includes(after);
  if (dash === "-") {
    if (!(spaceBefore && spaceAfter)) return false;
  } else {
    if (isDigit(after)) return false; // a minus sign, or the end of a range
    if (!(spaceBefore || spaceAfter || after === "" || after === "\n"))
      return false;
  }
  const prev = stripEnd(text.slice(0, i), HSPACE).slice(-1);
  const next = stripStart(text.slice(i + 1), HSPACE).slice(0, 1);
  return !(isDigit(prev) && isDigit(next));
}

/** The hit with a few words either side, on one line. */
function around(text: string, start: number, end: number, n = 3): string {
  const left = words(text.slice(0, start));
  const right = words(text.slice(end));
  const glueLeft = /\s/.test(text.slice(Math.max(0, start - 1), start))
    ? " "
    : "";
  const glueRight = /\s/.test(text.slice(end, end + 1)) ? " " : "";
  let quote =
    `${left.slice(-n).join(" ")}${glueLeft}${text.slice(start, end)}${glueRight}${right.slice(0, n).join(" ")}`.trim();
  if (left.length > n) quote = `…${quote}`;
  if (right.length > n) quote = `${quote}…`;
  return words(quote).join(" ");
}

/** Every dash used as punctuation, quoted with its neighbours, deduped, in order. */
export function dashFragments(text: string): string[] {
  const t = text ?? "";
  const found: string[] = [];
  for (const m of t.matchAll(DASH)) {
    const at = m.index ?? 0;
    if (!isSeparator(t, at)) continue;
    const quote = around(t, at, at + 1);
    if (!found.includes(quote)) found.push(quote);
  }
  return found;
}

/** Whether the full stop, !, ? or … that closes `before` ends a sentence. */
function endsSentence(before: string): boolean {
  if (!before.endsWith(".")) return true;
  const last = words(before.slice(0, -1)).pop() ?? "";
  const word = stripStart(last, OPENERS).toLowerCase();
  if (ABBREVIATIONS.has(word)) return false;
  if ([...word].length === 1 && /\p{L}/u.test(word)) return false; // an initial, "z. B."
  return !/^\p{Nd}{1,2}$/u.test(word); // a day or a rank, not a year
}

export function sentences(text: string): string[] {
  const t = text ?? "";
  const out: string[] = [];
  let start = 0;
  for (const m of t.matchAll(BREAK)) {
    const at = m.index ?? 0;
    if (m[0].includes("\n") || endsSentence(t.slice(start, at))) {
      out.push(t.slice(start, at));
      start = at + m[0].length;
    }
  }
  out.push(t.slice(start));
  return out;
}

function shorten(fragment: string): string {
  const chars = [...fragment];
  if (chars.length <= FRAGMENT_MAX) return fragment;
  const half = Math.floor((FRAGMENT_MAX - 3) / 2);
  return `${chars.slice(0, half).join("").trimEnd()} … ${chars.slice(-half).join("").trimStart()}`;
}

/** One quote per sentence that builds a "not X but Y" antithesis, deduped, in order. */
export function antithesisFragments(text: string): string[] {
  const found: string[] = [];
  for (const sentence of sentences(text)) {
    for (const pattern of ANTITHESES) {
      const m = pattern.exec(sentence);
      if (!m) continue;
      // The hit and the word it turns to: "nicht nur X, sondern auch Y".
      const tail = words(sentence.slice(m.index + m[0].length))
        .slice(0, 2)
        .join(" ");
      const quote = shorten(words(`${m[0]} ${tail}`).join(" "));
      if (!found.includes(quote)) found.push(quote);
      break;
    }
  }
  return found;
}

const FINDERS: Record<StyleCheck, (text: string) => string[]> = {
  no_dashes: dashFragments,
  no_antithesis: antithesisFragments,
};

/** A warning per hit: which part, which rule, and the words that break it, worded as the
 *  server words it. `checks` are the ones the user has on. */
export function styleWarnings(
  parts: Partial<Record<LetterPart, string>>,
  checks: readonly string[],
): string[] {
  const out: string[] = [];
  for (const [part, text] of Object.entries(parts)) {
    for (const check of CHECKS) {
      if (!checks.includes(check)) continue;
      for (const fragment of FINDERS[check](text ?? "")) {
        out.push(
          `${PART[part as LetterPart] ?? part} breaks the ${check} rule (${RULE[check]}): "${fragment}"`,
        );
      }
    }
  }
  return out;
}
