import { describe, expect, it } from "vitest";
import {
  nativeStamp,
  readStamp,
  writeStamp,
} from "@/lib/portfolio/stamp";
import { isEmptyPayload, reorderByRank } from "@/lib/portfolio/content";
import type { PortfolioItem } from "@/lib/queries/portfolio";

/**
 * Red-first unit tests for the pure helpers behind the portfolio reset fix
 * (guide `[frontend]-portfolio-reset-fix.md`). No DOM, no network.
 *
 * The behavioural fixes (stamp not rewritten on passive /explore load, escape
 * hatch -> "/", lucky reshuffle, 404-branch escape) live in React components and
 * are covered by the guide's manual Verification, not here — the tests/ regime is
 * node-env pure-lib only (see [[frontend-test-layout]]). These two helpers are the
 * fix's testable seam.
 */

// Injectable in-memory storage — the stamp.ts idiom (never stub globals).
function memStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
}

describe("nativeStamp", () => {
  it("drops the free-text query, keeps domains and lucky", () => {
    expect(nativeStamp({ d: ["music"], lucky: false, q: "jazz" })).toEqual({
      kind: "native",
      search: { d: ["music"], lucky: false },
    });
  });

  it("carries a lucky-only answer", () => {
    expect(nativeStamp({ lucky: true })).toEqual({
      kind: "native",
      search: { d: undefined, lucky: true },
    });
  });

  it("builds a stamp that round-trips through write/read + the schema", () => {
    const storage = memStorage();
    writeStamp(
      nativeStamp({ d: ["software development"], lucky: false, q: "drop me" }),
      storage,
    );
    // q is gone; the rest survives a JSON round-trip through the zod schema.
    expect(readStamp(storage)).toEqual({
      kind: "native",
      search: { d: ["software development"], lucky: false },
    });
  });
});

describe("isEmptyPayload", () => {
  const item = (id: string): PortfolioItem => ({
    id,
    type: "job",
    title: "",
    domains: [],
  });

  it("is true when both featured and more are empty", () => {
    expect(isEmptyPayload([], [])).toBe(true);
  });

  it("is false when featured has items", () => {
    expect(isEmptyPayload([item("job:1")], [])).toBe(false);
  });

  it("is false when more has items", () => {
    expect(isEmptyPayload([], [item("block:2")])).toBe(false);
  });
});

describe("reorderByRank", () => {
  const item = (id: string): PortfolioItem => ({
    id,
    type: "job",
    title: "",
    domains: [],
  });

  it("sorts ranked ids to the front by descending score", () => {
    const more = [item("job:1"), item("job:2"), item("job:3")];
    const ranked = [
      { id: "job:3", score: 0.9 },
      { id: "job:1", score: 0.5 },
    ];
    expect(reorderByRank(more, ranked).map((i) => i.id)).toEqual([
      "job:3",
      "job:1",
      "job:2",
    ]);
  });

  it("never filters and keeps unranked items in natural order", () => {
    const more = [item("a:1"), item("a:2"), item("a:3")];
    const out = reorderByRank(more, []);
    expect(out.map((i) => i.id)).toEqual(["a:1", "a:2", "a:3"]);
    expect(out).toHaveLength(3);
  });

  it("ignores ranked ids that aren't in the list", () => {
    const more = [item("job:1")];
    expect(reorderByRank(more, [{ id: "block:99", score: 1 }]).map((i) => i.id)).toEqual(
      ["job:1"],
    );
  });
});
