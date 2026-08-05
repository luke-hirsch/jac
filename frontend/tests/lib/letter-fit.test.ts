import { beforeAll, describe, expect, it } from "vitest";

/**
 * `[fullstack]-letter-fit`: where the letter stops fitting page 1. SKIP-MARKED — not the
 * active guide. **Step 0: delete every `.skip` in this file.**
 *
 * react-pdf will not report *where* it broke, but page count is monotone in body length,
 * so a binary search over word boundaries finds the last word that fits. The searches are
 * driven by a fake `pagesFor` (N words per page) so the suite stays deterministic — and so
 * the render-count assertion below actually means something.
 *
 * Lazy import (see appearance.test.ts) so the file stays collectible while
 * `@/lib/render/letter-fit` does not exist yet.
 */

type FitIndex = { index: number; words: number };
type Mod = {
  wordBoundary(body: string, n: number): number;
  fitIndex(
    body: string,
    pagesFor: (text: string) => Promise<number>,
    maxPages?: number,
  ): Promise<FitIndex | null>;
  shortenTarget(fit: FitIndex): number;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/render/letter-fit")) as unknown as Mod;
});

const words = (n: number) =>
  Array.from({ length: n }, (_, i) => `w${i + 1}`).join(" ");

/** Fake renderer: `perPage` words fit on a page. */
function pager(perPage: number) {
  let calls = 0;
  const pagesFor = async (text: string) => {
    calls++;
    const n = text.trim() ? text.trim().split(/\s+/).length : 0;
    return Math.max(1, Math.ceil(n / perPage));
  };
  return { pagesFor, renders: () => calls };
}

describe.skip("wordBoundary", () => {
  const body = "alpha beta gamma";

  it("returns the end of the nth word, never a mid-word offset", () => {
    expect(body.slice(0, m.wordBoundary(body, 1))).toBe("alpha");
    expect(body.slice(0, m.wordBoundary(body, 2))).toBe("alpha beta");
  });

  it("clamps at both ends", () => {
    expect(m.wordBoundary(body, 0)).toBe(0);
    expect(m.wordBoundary(body, -3)).toBe(0);
    expect(m.wordBoundary(body, 99)).toBe(body.length);
  });

  it("handles newlines and runs of whitespace like word separators", () => {
    const wrapped = "alpha\n\nbeta   gamma";
    expect(wrapped.slice(0, m.wordBoundary(wrapped, 2))).toBe("alpha\n\nbeta");
  });
});

describe.skip("fitIndex", () => {
  it("returns null when the letter already fits", async () => {
    const { pagesFor } = pager(100);
    expect(await m.fitIndex(words(50), pagesFor)).toBeNull();
  });

  it("returns null for an empty body", async () => {
    const { pagesFor } = pager(10);
    expect(await m.fitIndex("", pagesFor)).toBeNull();
  });

  it("finds the exact last word that fits", async () => {
    const body = words(30);
    const { pagesFor } = pager(10);
    const fit = await m.fitIndex(body, pagesFor);
    expect(fit).not.toBeNull();
    expect(fit!.words).toBe(10);
    expect(body.slice(0, fit!.index).trim().split(/\s+/)).toHaveLength(10);
    expect(await pagesFor(body.slice(0, fit!.index))).toBe(1);
  });

  it("respects a larger page budget", async () => {
    const { pagesFor } = pager(10);
    const fit = await m.fitIndex(words(50), pagesFor, 2);
    expect(fit!.words).toBe(20);
  });

  it("is a binary search, not a linear scan", async () => {
    const p = pager(10);
    await m.fitIndex(words(400), p.pagesFor);
    // log2(400) ≈ 9, plus the initial "does it fit at all" render and slack.
    expect(p.renders()).toBeLessThan(20);
  });
});

describe.skip("shortenTarget", () => {
  it("aims just under what actually fits", () => {
    const t = m.shortenTarget({ index: 0, words: 200 });
    expect(t).toBeLessThan(200);
    expect(t).toBeGreaterThan(170);
  });

  it("never asks for a letter shorter than a letter", () => {
    expect(m.shortenTarget({ index: 0, words: 3 })).toBeGreaterThanOrEqual(60);
  });
});
