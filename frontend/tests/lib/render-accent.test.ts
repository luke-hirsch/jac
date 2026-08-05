import { beforeAll, describe, expect, it } from "vitest";
import { FALLBACK_SPEC } from "@/lib/render/spec";
import type { LayoutSpec } from "@/lib/render/spec";

/**
 * `[fullstack]-appearance-settings`: the CV accent, kept legible. SKIP-MARKED — not the
 * active guide. **Step 0: delete every `.skip` in this file.**
 *
 * Lazy import (see appearance.test.ts for why). The point of the clamp: an accent lands on
 * the name and every section title, so a pale pick would print as near-invisible on white
 * and disappear entirely in greyscale.
 */

type Mod = {
  MIN_CONTRAST: number;
  ACCENT_PRESETS: readonly string[];
  normalizeHex(raw: string | null | undefined): string | null;
  luminance(hex: string): number;
  contrastRatio(hex: string): number;
  readableAccent(raw: string, min?: number): string;
  applyAccent(spec: LayoutSpec, accent: string | undefined): LayoutSpec;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/render/accent")) as unknown as Mod;
});

describe.skip("normalizeHex", () => {
  it("accepts the shapes a colour input or a human can produce", () => {
    expect(m.normalizeHex("#1A5FB4")).toBe("#1a5fb4");
    expect(m.normalizeHex("1a5fb4")).toBe("#1a5fb4");
    expect(m.normalizeHex("#abc")).toBe("#aabbcc");
    expect(m.normalizeHex("  #1a5fb4  ")).toBe("#1a5fb4");
  });

  it("rejects anything else rather than half-parsing it", () => {
    expect(m.normalizeHex("red")).toBeNull();
    expect(m.normalizeHex("#12345")).toBeNull();
    expect(m.normalizeHex("")).toBeNull();
    expect(m.normalizeHex(null)).toBeNull();
    expect(m.normalizeHex(undefined)).toBeNull();
  });
});

describe.skip("luminance / contrastRatio", () => {
  it("anchors on the two extremes", () => {
    expect(m.luminance("#ffffff")).toBeCloseTo(1, 3);
    expect(m.luminance("#000000")).toBeCloseTo(0, 3);
    expect(m.contrastRatio("#ffffff")).toBeCloseTo(1, 2);
    expect(m.contrastRatio("#000000")).toBeCloseTo(21, 1);
  });

  it("measures against white — the CV page is always white", () => {
    // A mid grey sits between the extremes; the direction is what matters.
    expect(m.contrastRatio("#808080")).toBeGreaterThan(1);
    expect(m.contrastRatio("#808080")).toBeLessThan(21);
  });
});

describe.skip("readableAccent", () => {
  it("leaves the layout's default blue exactly as chosen", () => {
    expect(m.contrastRatio("#1a5fb4")).toBeGreaterThanOrEqual(m.MIN_CONTRAST);
    expect(m.readableAccent("#1a5fb4")).toBe("#1a5fb4");
  });

  it("darkens a pale pick until it clears AA on white", () => {
    const out = m.readableAccent("#f4d03f"); // pale yellow: ~1.5:1 as chosen
    expect(m.contrastRatio("#f4d03f")).toBeLessThan(m.MIN_CONTRAST);
    expect(m.contrastRatio(out)).toBeGreaterThanOrEqual(m.MIN_CONTRAST);
  });

  it("keeps the hue — a darkened yellow is still yellow, not grey", () => {
    const [r, g, b] = [1, 3, 5].map((i) =>
      parseInt(m.readableAccent("#f4d03f").slice(i, i + 2), 16),
    );
    expect(r).toBeGreaterThan(b);
    expect(g).toBeGreaterThan(b);
  });

  it("terminates on white instead of looping forever", () => {
    const out = m.readableAccent("#ffffff");
    expect(m.contrastRatio(out)).toBeGreaterThanOrEqual(m.MIN_CONTRAST);
  });

  it("passes junk straight back so a bad value can never crash a render", () => {
    expect(m.readableAccent("not a colour")).toBe("not a colour");
  });

  it("ships presets that all already pass the clamp", () => {
    for (const hex of m.ACCENT_PRESETS) {
      expect(m.readableAccent(hex)).toBe(hex);
    }
  });
});

describe.skip("applyAccent", () => {
  it("is the identity when the user has not picked one", () => {
    expect(m.applyAccent(FALLBACK_SPEC, "")).toBe(FALLBACK_SPEC);
    expect(m.applyAccent(FALLBACK_SPEC, undefined)).toBe(FALLBACK_SPEC);
    expect(m.applyAccent(FALLBACK_SPEC, "nonsense")).toBe(FALLBACK_SPEC);
  });

  it("overrides only the accent, clamped, leaving the rest of the spec alone", () => {
    const out = m.applyAccent(FALLBACK_SPEC, "#f4d03f");
    expect(out.colors.accent).not.toBe(FALLBACK_SPEC.colors.accent);
    expect(m.contrastRatio(out.colors.accent)).toBeGreaterThanOrEqual(
      m.MIN_CONTRAST,
    );
    expect(out.colors.text).toBe(FALLBACK_SPEC.colors.text);
    expect(out.colors.muted).toBe(FALLBACK_SPEC.colors.muted);
    expect(out.cv).toEqual(FALLBACK_SPEC.cv);
    expect(FALLBACK_SPEC.colors.accent).toBe("#1a5fb4"); // no mutation
  });
});
