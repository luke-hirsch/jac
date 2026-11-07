import { describe, expect, it } from "vitest";
import {
  clearStamp,
  readStamp,
  stampKey,
  writeStamp,
} from "@/lib/portfolio/stamp";

/**
 * Every portfolio shares the portfolio host's origin, so localStorage no longer keeps owners apart:
 * the stamp's key does. The privacy page names the key `portfolio.stamp.v1.<handle>`.
 */

// Injectable in-memory storage — the stamp.ts idiom (never stub globals).
function memStorage() {
  const store = new Map<string, string>();
  return {
    store,
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
}

describe("stampKey", () => {
  it("names the owner, as the privacy page says", () => {
    expect(stampKey("jane")).toBe("portfolio.stamp.v1.jane");
  });

  it("handles are case-insensitive, so /Jane/ and /jane/ share one stamp", () => {
    expect(stampKey("Jane")).toBe(stampKey("jane"));
  });
});

describe("a stamp per owner", () => {
  it("two handles in one browser each keep their own stamp", () => {
    const storage = memStorage();
    writeStamp("jane", { kind: "link", slug: "acme" }, storage);
    writeStamp("lukas", { kind: "native", search: { lucky: true } }, storage);

    expect(readStamp("jane", storage)).toEqual({ kind: "link", slug: "acme" });
    expect(readStamp("lukas", storage)).toEqual({
      kind: "native",
      search: { lucky: true },
    });
    expect([...storage.store.keys()].sort()).toEqual([
      "portfolio.stamp.v1.jane",
      "portfolio.stamp.v1.lukas",
    ]);
  });

  it("an owner without a stamp reads none, even when another owner has one", () => {
    const storage = memStorage();
    writeStamp("jane", { kind: "link", slug: "acme" }, storage);
    expect(readStamp("lukas", storage)).toBeNull();
  });

  it("clearing one owner's stamp leaves the other's", () => {
    const storage = memStorage();
    writeStamp("jane", { kind: "link", slug: "acme" }, storage);
    writeStamp("lukas", { kind: "link", slug: "intro" }, storage);
    clearStamp("jane", storage);
    expect(readStamp("jane", storage)).toBeNull();
    expect(readStamp("lukas", storage)).toEqual({
      kind: "link",
      slug: "intro",
    });
  });

  it("corrupt JSON and storage that throws read as no stamp", () => {
    const storage = memStorage();
    storage.setItem(stampKey("jane"), "{not json");
    expect(readStamp("jane", storage)).toBeNull();
    const throwing = {
      getItem: () => {
        throw new Error("private mode");
      },
    };
    expect(readStamp("jane", throwing)).toBeNull();
  });
});
