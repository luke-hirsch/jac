import { beforeAll, describe, expect, it } from "vitest";

/**
 * `[fullstack]-appearance-settings`: theme + contrast resolution. SKIP-MARKED — not the
 * active guide. **Step 0: delete every `.skip` in this file.**
 *
 * The module is imported lazily inside `beforeAll` so this file stays *collectible* while
 * `@/lib/appearance` does not exist yet (a static import of a missing module fails at
 * collection, which would pollute the active guide's red set). Once you implement the
 * module, feel free to hoist it to a normal top-level import — the local `Mod` type below
 * is the contract the guide specifies.
 */

type Appearance = { theme: string; contrast: string };
type Mod = {
  APPEARANCE_KEY: string;
  DEFAULT_APPEARANCE: Appearance;
  ALL_APPEARANCE_CLASSES: readonly string[];
  resolveTheme(theme: string, systemDark: boolean): "light" | "dark";
  themeClasses(a: Appearance, systemDark: boolean): string[];
  readAppearance(raw: string | null): Appearance;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/appearance")) as unknown as Mod;
});

describe.skip("resolveTheme", () => {
  it("honours an explicit choice regardless of the system", () => {
    expect(m.resolveTheme("dark", false)).toBe("dark");
    expect(m.resolveTheme("light", true)).toBe("light");
  });

  it("follows the system both ways when set to system", () => {
    expect(m.resolveTheme("system", true)).toBe("dark");
    expect(m.resolveTheme("system", false)).toBe("light");
  });
});

describe.skip("themeClasses", () => {
  it("puts nothing on <html> for the plain light default", () => {
    expect(m.themeClasses({ theme: "system", contrast: "normal" }, false)).toEqual([]);
  });

  it("adds dark alone", () => {
    expect(m.themeClasses({ theme: "dark", contrast: "normal" }, false)).toEqual([
      "dark",
    ]);
  });

  it("adds contrast-high alone — it is not a dark-mode-only setting", () => {
    expect(m.themeClasses({ theme: "light", contrast: "high" }, true)).toEqual([
      "contrast-high",
    ]);
  });

  it("composes both, because the CSS has a .dark.contrast-high block", () => {
    expect(m.themeClasses({ theme: "system", contrast: "high" }, true)).toEqual([
      "dark",
      "contrast-high",
    ]);
  });

  it("only ever emits classes the provider knows how to remove again", () => {
    for (const theme of ["system", "light", "dark"]) {
      for (const contrast of ["normal", "high"]) {
        for (const sys of [true, false]) {
          for (const c of m.themeClasses({ theme, contrast }, sys)) {
            expect(m.ALL_APPEARANCE_CLASSES).toContain(c);
          }
        }
      }
    }
  });
});

describe.skip("readAppearance", () => {
  it("defaults when there is nothing stored yet", () => {
    expect(m.readAppearance(null)).toEqual(m.DEFAULT_APPEARANCE);
  });

  it("survives a hand-mangled localStorage value", () => {
    expect(m.readAppearance("{not json")).toEqual(m.DEFAULT_APPEARANCE);
  });

  it("fills in the missing half of a partial object", () => {
    expect(m.readAppearance('{"theme":"dark"}')).toEqual({
      theme: "dark",
      contrast: "normal",
    });
  });

  it("rejects values outside the enums instead of trusting them", () => {
    expect(m.readAppearance('{"theme":"neon","contrast":"extreme"}')).toEqual(
      m.DEFAULT_APPEARANCE,
    );
  });

  it("round-trips what the provider writes", () => {
    const a = { theme: "light", contrast: "high" };
    expect(m.readAppearance(JSON.stringify(a))).toEqual(a);
  });
});
