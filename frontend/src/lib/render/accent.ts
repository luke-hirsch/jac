/**
 * The CV accent colour, kept legible. A user-chosen accent lands on section titles, the
 * name, and the section rules — a pale one turns those into invisible ink on white, and
 * dies completely when the CV is printed in greyscale. So the accent is clamped to a WCAG
 * AA contrast ratio against the page before it ever reaches the renderer.
 */
import type { LayoutSpec } from "./spec";

/** WCAG AA for normal text. Section titles are 1.2 × base — still "normal" size at 9pt. */
export const MIN_CONTRAST = 4.5;

/** Sober defaults for the picker — a CV is not a place to discover neon. All of them
 *  already clear MIN_CONTRAST, so the clamp leaves them exactly as shown. */
export const ACCENT_PRESETS = [
  "#1a5fb4", // the layout default
  "#1c1c1c",
  "#26619c",
  "#1a7f56",
  "#8f4700",
  "#7a1f6b",
] as const;

type Rgb = [number, number, number];

/** `#abc` / `#aabbcc` / `aabbcc` → `#aabbcc`; anything else → null (fall back to the layout). */
export function normalizeHex(raw: string | null | undefined): string | null {
  const s = (raw ?? "").trim().replace(/^#/, "");
  if (/^[0-9a-fA-F]{3}$/.test(s))
    return `#${s[0]}${s[0]}${s[1]}${s[1]}${s[2]}${s[2]}`.toLowerCase();
  if (/^[0-9a-fA-F]{6}$/.test(s)) return `#${s}`.toLowerCase();
  return null;
}

function channels(hex: string): Rgb {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex(rgb: Rgb): string {
  return `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;
}

/** WCAG relative luminance. */
export function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Contrast ratio against white — the CV page is white, always. */
export function contrastRatio(hex: string): number {
  return 1.05 / (luminance(hex) + 0.05);
}

/**
 * Darken until the accent clears `min` on white. Multiplicative steps keep the hue and
 * saturation (a lemon yellow stays yellow, just olive enough to read); 40 steps of 0.94
 * reach near-black, so the loop always terminates. An accent that already passes comes
 * back exactly as given.
 */
export function readableAccent(raw: string, min = MIN_CONTRAST): string {
  const hex = normalizeHex(raw);
  if (!hex) return raw;
  let rgb = channels(hex);
  for (let i = 0; i < 40 && contrastRatio(toHex(rgb)) < min; i++) {
    rgb = [rgb[0] * 0.94, rgb[1] * 0.94, rgb[2] * 0.94];
  }
  return toHex(rgb);
}

/** The user's accent over the layout's, clamped. A blank/invalid accent changes nothing. */
export function applyAccent(
  spec: LayoutSpec,
  accent: string | undefined,
): LayoutSpec {
  const hex = normalizeHex(accent);
  if (!hex) return spec;
  return { ...spec, colors: { ...spec.colors, accent: readableAccent(hex) } };
}
