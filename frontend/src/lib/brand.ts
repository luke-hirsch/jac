import { normalizeHex } from "@/lib/render/accent";

/**
 * The brand colour: the one colour of the UI that comes from an account rather than from
 * theme.css. It goes on <html> as `--brand`, and theme.css makes the primary (and the focus
 * ring, the network's recent end) from it, readable in light and in dark mode. Pure, so the
 * tests cover it; the hook is components/brand.ts, the writer components/brand-provider.tsx.
 */

/** Where the last colour is remembered, so the next load paints in it at once. The pre-paint
 *  script in index.html reads it: keep the two in step. */
export const BRAND_KEY = "brand";

/**
 * The colour a page wears.
 *
 * - `own`: the page's colour, as the server resolved it (the signed-in user's or the portfolio
 *   owner's, else the site owner's). `undefined` while the page is still loading it; `null` or
 *   "" when it has none, or no page says.
 * - `site`: the site owner's (`/api/spa/public/brand/`), `undefined` until it's known.
 *
 * Returns the colour as `#rrggbb`, `null` for none (theme.css's fallback), or `undefined` while
 * it can't be known yet: the page then keeps what's on <html>, the colour the pre-paint script
 * remembered. A page's own colour never waits for the site owner's.
 */
export function brandFor(
  own: string | null | undefined,
  site: string | null | undefined,
): string | null | undefined {
  if (own === undefined) return undefined;
  const mine = normalizeHex(own);
  if (mine) return mine;
  const theirs = normalizeHex(site);
  if (theirs) return theirs;
  return site === undefined ? undefined : null;
}

/** A remembered colour, if it's one the provider wrote: `#rrggbb`, lower case. Anything else
 *  (nothing, "", a hand-edited value) is none. index.html's pre-paint script runs the same test. */
export function rememberedBrand(raw: string | null): string | null {
  return raw !== null && /^#[0-9a-f]{6}$/.test(raw) ? raw : null;
}
