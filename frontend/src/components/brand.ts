import { createContext, useContext, useEffect } from "react";

/** A page claims a colour; the returned function gives the claim up. The provider
 *  (brand-provider.tsx) holds the latest claim and writes the one `--brand` on <html>. */
export type ClaimBrand = (accent: string | null | undefined) => () => void;

export const BrandContext = createContext<ClaimBrand>(() => () => {});

/**
 * This page wears `accent`: a portfolio page its owner's (`payload.owner.accent`, or the meta's
 * `accent` before a payload exists), jac the signed-in user's (`profile.brand`). The server has
 * already fallen back to the site owner's. `undefined` while it loads: the page keeps the colour
 * it has. A page that never calls this wears the site owner's. Call it once per page, from the
 * component that has the data.
 */
export function useBrand(accent: string | null | undefined) {
  const claim = useContext(BrandContext);
  useEffect(() => claim(accent), [claim, accent]);
}
