import { countWords } from "@/lib/letter-doc";

/**
 * Where the letter stops fitting page 1, and how much to ask the model to cut.
 *
 * react-pdf will not tell you *where* it broke — but page count is monotone in body
 * length, so a binary search over WORD boundaries (never mid-word: a half word re-wraps
 * differently and the index would lie) finds the last word that still fits in ~log₂(n)
 * renders. For a 250-word letter that's 8 renders, about a second, run once when the
 * preflight reports an overflow.
 */
export type FitIndex = {
  /** Character index in the body: everything from here on does not fit page 1. */
  index: number;
  /** Words that do fit. */
  words: number;
};

/** Character offset of the end of word `n` (1-based), or the whole string. */
export function wordBoundary(body: string, n: number): number {
  if (n <= 0) return 0;
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  let seen = 0;
  while ((m = re.exec(body))) {
    if (++seen === n) return m.index + m[0].length;
  }
  return body.length;
}

export async function fitIndex(
  body: string,
  pagesFor: (text: string) => Promise<number>,
  maxPages = 1,
): Promise<FitIndex | null> {
  const total = countWords(body);
  if (total === 0) return null;
  if ((await pagesFor(body)) <= maxPages) return null; // it already fits

  let lo = 0; // fits
  let hi = total; // does not
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    const p = await pagesFor(body.slice(0, wordBoundary(body, mid)));
    if (p <= maxPages) lo = mid;
    else hi = mid;
  }
  return { index: wordBoundary(body, lo), words: lo };
}

/**
 * The word budget to ask for. Derived from the measurement, not a flat percentage: we know
 * how many words fit, so aim just under it. The 0.95 is slack for the model overshooting
 * and for the closing furniture.
 */
export function shortenTarget(fit: FitIndex): number {
  return Math.max(60, Math.floor(fit.words * 0.95));
}
