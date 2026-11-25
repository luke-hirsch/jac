import { beforeAll, describe, expect, it } from "vitest";
import { runPrePaint } from "./_pre-paint";

/**
 * `[fullstack]-appearance-settings`: theme + contrast resolution.
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
  coerceAppearance(raw: unknown): Appearance;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/appearance")) as unknown as Mod;
});

describe("resolveTheme", () => {
  it("honours an explicit choice regardless of the system", () => {
    expect(m.resolveTheme("dark", false)).toBe("dark");
    expect(m.resolveTheme("light", true)).toBe("light");
  });

  it("follows the system both ways when set to system", () => {
    expect(m.resolveTheme("system", true)).toBe("dark");
    expect(m.resolveTheme("system", false)).toBe("light");
  });
});

describe("themeClasses", () => {
  /* Guide 31: theme.css's light-dark() picks by color-scheme, which without a class follows the
     system. So light is said as plainly as dark: a light setting on a dark system must win. */
  it("says light for the plain light default", () => {
    expect(
      m.themeClasses({ theme: "system", contrast: "normal" }, false),
    ).toEqual(["light"]);
  });

  it("says light when the setting is light and the system is dark", () => {
    expect(
      m.themeClasses({ theme: "light", contrast: "normal" }, true),
    ).toEqual(["light"]);
  });

  it("says dark alone", () => {
    expect(
      m.themeClasses({ theme: "dark", contrast: "normal" }, false),
    ).toEqual(["dark"]);
  });

  it("adds contrast-high to light: it is not a dark-mode-only setting", () => {
    expect(m.themeClasses({ theme: "light", contrast: "high" }, true)).toEqual([
      "light",
      "contrast-high",
    ]);
  });

  it("composes dark and contrast-high: theme.css's high-contrast block has both", () => {
    expect(m.themeClasses({ theme: "system", contrast: "high" }, true)).toEqual(
      ["dark", "contrast-high"],
    );
  });

  it("always says exactly one of light and dark", () => {
    for (const theme of ["system", "light", "dark"]) {
      for (const contrast of ["normal", "high"]) {
        for (const sys of [true, false]) {
          const modes = m
            .themeClasses({ theme, contrast }, sys)
            .filter((c) => c === "light" || c === "dark");
          expect(modes).toHaveLength(1);
        }
      }
    }
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

describe("the pre-paint script in index.html", () => {
  it("puts on <html> exactly what themeClasses would", () => {
    for (const theme of ["system", "light", "dark"]) {
      for (const contrast of ["normal", "high"]) {
        for (const systemDark of [true, false]) {
          const stored = { appearance: JSON.stringify({ theme, contrast }) };
          expect(runPrePaint({ stored, systemDark }).classes).toEqual(
            m.themeClasses({ theme, contrast }, systemDark),
          );
        }
      }
    }
  });

  it("follows the system on a first visit", () => {
    for (const systemDark of [true, false]) {
      expect(runPrePaint({ systemDark }).classes).toEqual(
        m.themeClasses(m.DEFAULT_APPEARANCE, systemDark),
      );
    }
  });
});

describe("readAppearance", () => {
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

describe("coerceAppearance", () => {
  it("takes the two fields off a whole profile row and ignores the rest", () => {
    const row = {
      id: 1,
      theme: "dark",
      contrast: "high",
      accent_color: "#1a5fb4",
    };
    expect(m.coerceAppearance(row)).toEqual({
      theme: "dark",
      contrast: "high",
    });
  });

  it("defaults anything that is not an object with valid values", () => {
    for (const raw of [
      null,
      undefined,
      42,
      "dark",
      { theme: 1, contrast: true },
    ]) {
      expect(m.coerceAppearance(raw)).toEqual(m.DEFAULT_APPEARANCE);
    }
  });
});
