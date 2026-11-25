import { beforeAll, describe, expect, it } from "vitest";
import { runPrePaint } from "./_pre-paint";

/**
 * Guide 31, the brand colour: which colour a page wears. The page's own (the server already fell
 * back to the site owner's), else the site owner's, else none (theme.css's fallback), and
 * "not known yet" while either is still loading, so the page keeps what the pre-paint script
 * remembered instead of flashing the fallback.
 *
 * Lazy import, as in appearance.test.ts: the file stays collectible while `@/lib/brand` doesn't
 * exist yet. Hoist it to a top-level import once it does; `Mod` is the guide's contract.
 */

type Mod = {
  BRAND_KEY: string;
  brandFor(
    own: string | null | undefined,
    site: string | null | undefined,
  ): string | null | undefined;
  rememberedBrand(raw: string | null): string | null;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/brand")) as unknown as Mod;
});

describe("brandFor", () => {
  it("wears the page's own colour, whatever the site owner's", () => {
    expect(m.brandFor("#1a7f56", "#8f4700")).toBe("#1a7f56");
    expect(m.brandFor("#1a7f56", "")).toBe("#1a7f56");
  });

  it("doesn't wait for the site owner's when the page has its own", () => {
    expect(m.brandFor("#1a7f56", undefined)).toBe("#1a7f56");
  });

  it("falls back to the site owner's when the page has none", () => {
    for (const none of [null, ""]) {
      expect(m.brandFor(none, "#8f4700")).toBe("#8f4700");
    }
  });

  it("is none when nobody has one: theme.css wears its fallback", () => {
    expect(m.brandFor(null, "")).toBeNull();
    expect(m.brandFor("", null)).toBeNull();
  });

  it("isn't known while the page is still loading its colour", () => {
    expect(m.brandFor(undefined, "#8f4700")).toBeUndefined();
    expect(m.brandFor(undefined, undefined)).toBeUndefined();
  });

  it("isn't known while it needs the site owner's and that hasn't come", () => {
    expect(m.brandFor(null, undefined)).toBeUndefined();
    expect(m.brandFor("", undefined)).toBeUndefined();
  });

  it("hands out #rrggbb in lower case, the short form expanded", () => {
    expect(m.brandFor("#1A7F56", null)).toBe("#1a7f56");
    expect(m.brandFor("#fa5", null)).toBe("#ffaa55");
    expect(m.brandFor(null, "#8F4700")).toBe("#8f4700");
  });

  it("counts junk as no colour", () => {
    expect(m.brandFor("red", "#8f4700")).toBe("#8f4700");
    expect(m.brandFor("#1a7f5z", "url(x)")).toBeNull();
  });
});

describe("rememberedBrand", () => {
  it("takes back what the provider wrote", () => {
    expect(m.BRAND_KEY).toBe("brand");
    expect(m.rememberedBrand("#1a7f56")).toBe("#1a7f56");
  });

  it("is none for nothing, for the empty value, and for anything hand-edited", () => {
    for (const raw of [
      null,
      "",
      "#1A7F56",
      "#fa5",
      "1a7f56",
      "red",
      "#1a7f56; color: red",
    ]) {
      expect(m.rememberedBrand(raw)).toBeNull();
    }
  });
});

describe("the pre-paint script in index.html", () => {
  it("paints the remembered colour before the first frame", () => {
    expect(runPrePaint({ stored: { brand: "#1a7f56" } }).brand).toBe("#1a7f56");
  });

  it("takes exactly what rememberedBrand takes", () => {
    for (const raw of ["#1a7f56", "", "#1A7F56", "#fa5", "red", "#1a7f56;x"]) {
      expect(runPrePaint({ stored: { brand: raw } }).brand).toBe(
        m.rememberedBrand(raw),
      );
    }
    expect(runPrePaint({}).brand).toBeNull();
  });

  it("still paints the colour when the stored appearance is garbage", () => {
    const out = runPrePaint({
      stored: { appearance: "{not json", brand: "#1a7f56" },
    });
    expect(out.brand).toBe("#1a7f56");
  });

  it("survives storage that throws", () => {
    expect(() => runPrePaint({ storageThrows: true })).not.toThrow();
  });
});
