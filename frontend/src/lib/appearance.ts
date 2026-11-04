/**
 * Theme + contrast resolution. Pure on purpose: the same logic runs in three places —
 * the React provider, the pre-paint script in index.html (hand-inlined, keep them in
 * sync!), and the tests.
 */
export type Theme = "system" | "light" | "dark";
export type Contrast = "normal" | "high";
export type Appearance = { theme: Theme; contrast: Contrast };

export const APPEARANCE_KEY = "appearance";
export const DEFAULT_APPEARANCE: Appearance = {
  theme: "system",
  contrast: "normal",
};

/** All classes this module may ever set — the provider removes these before adding, so a
 *  setting change never leaves a stale class behind. */
export const ALL_APPEARANCE_CLASSES = ["dark", "contrast-high"] as const;

/** `system` follows the OS; anything else is an explicit override. */
export function resolveTheme(
  theme: Theme,
  systemDark: boolean,
): "light" | "dark" {
  if (theme === "dark") return "dark";
  if (theme === "light") return "light";
  return systemDark ? "dark" : "light";
}

/**
 * The classes that belong on <html>. `.contrast-high` is additive — the CSS has a
 * `.dark.contrast-high` block for the combination, so both classes stay on together.
 */
export function themeClasses(a: Appearance, systemDark: boolean): string[] {
  const out: string[] = [];
  if (resolveTheme(a.theme, systemDark) === "dark") out.push("dark");
  if (a.contrast === "high") out.push("contrast-high");
  return out;
}

/** Any value, defaulted field by field: values outside the enums are never trusted. */
export function coerceAppearance(raw: unknown): Appearance {
  const v = (raw ?? {}) as Partial<Record<keyof Appearance, unknown>>;
  return {
    theme:
      v.theme === "dark" || v.theme === "light" || v.theme === "system"
        ? v.theme
        : DEFAULT_APPEARANCE.theme,
    contrast: v.contrast === "high" ? "high" : DEFAULT_APPEARANCE.contrast,
  };
}

/** Whatever is in localStorage — a half-written or hand-edited value must never leave the
 *  app themeless. */
export function readAppearance(raw: string | null): Appearance {
  try {
    return coerceAppearance(JSON.parse(raw ?? "{}"));
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}
