/**
 * Puts the brand colour on <html> as `--brand`; theme.css turns it into the primary colour. On
 * <html>, not on a wrapper: theme.css declares its tokens on :root, so only there do they
 * re-resolve with it, and toasts, popovers and dialogs are portalled to <body> anyway.
 *
 * One writer, three sources, in order: the page that claims a colour (useBrand), the site
 * owner's (`/api/spa/public/brand/`), theme.css's fallback (no `--brand` at all). Until one is
 * known, what the pre-paint script in index.html remembered stays on.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { BrandContext, type ClaimBrand } from "@/components/brand";
import { api } from "@/lib/api";
import { BRAND_KEY, brandFor } from "@/lib/brand";

type Claim = { accent: string | null | undefined };

export function BrandProvider({ children }: { children: ReactNode }) {
  const site = useQuery({
    queryKey: ["brand"],
    queryFn: () => api<{ brand: string }>("/api/spa/public/brand/"),
    staleTime: Infinity,
    retry: false,
  });
  const [claimed, setClaimed] = useState<Claim | null>(null);
  const claim = useCallback<ClaimBrand>((accent) => {
    const mine: Claim = { accent };
    setClaimed(mine);
    // A newer claim (the next page, or this one with a new colour) outlives this one's end.
    return () => setClaimed((c) => (c === mine ? null : c));
  }, []);

  const brand = brandFor(
    claimed ? claimed.accent : null,
    site.isError ? "" : site.data?.brand,
  );
  useEffect(() => {
    if (brand === undefined) return; // not known yet: keep what's on <html>
    const root = document.documentElement;
    if (brand) root.style.setProperty("--brand", brand);
    else root.style.removeProperty("--brand"); // an empty value would count as set
    try {
      localStorage.setItem(BRAND_KEY, brand ?? "");
    } catch {
      /* the next load paints the fallback first, then this */
    }
  }, [brand]);

  return (
    <BrandContext.Provider value={claim}>{children}</BrandContext.Provider>
  );
}
