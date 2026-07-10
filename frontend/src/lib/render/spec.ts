/**
 * The declarative layout spec stored as `ApplicationLayout.template` (a media file; source of
 * truth: backend/jac/resources/*.json, seeded by seed_default_domains). Parsing is defensive —
 * a user-uploaded spec may be partial or use the legacy singular "education" section name.
 */
import { useQuery } from "@tanstack/react-query";
import type { LayoutRow } from "@/lib/queries/jac";

export type LayoutSpec = {
  version: number;
  page: { size: "A4" | "LETTER"; margin: [number, number] }; // [vertical, horizontal] pt
  font: { family: string; base_pt: number };
  colors: { accent: string; text: string; muted: string };
  cv: { pages: number; sections: string[]; sidebar: string[] };
  cover_letter: { din5008: boolean };
};

export const FALLBACK_SPEC: LayoutSpec = {
  version: 1,
  page: { size: "A4", margin: [56, 48] },
  font: { family: "Helvetica", base_pt: 10 },
  colors: { accent: "#1a5fb4", text: "#1c1c1c", muted: "#6b6b6b" },
  cv: {
    pages: 1,
    sections: ["jobs", "educations", "projects", "certifications"],
    sidebar: ["skills", "languages"],
  },
  cover_letter: { din5008: true },
};

/** Legacy spec section names → cv_content keys. */
const LEGACY_SECTIONS: Record<string, string> = { education: "educations" };

export function parseLayoutSpec(raw: unknown): LayoutSpec {
  const r = (raw ?? {}) as {
    version?: number;
    page?: Partial<LayoutSpec["page"]>;
    font?: Partial<LayoutSpec["font"]>;
    colors?: Partial<LayoutSpec["colors"]>;
    cv?: Partial<LayoutSpec["cv"]>;
    cover_letter?: Partial<LayoutSpec["cover_letter"]>;
  };
  const f = FALLBACK_SPEC;
  const sections = (names: string[] | undefined, fallback: string[]) =>
    (names ?? fallback).map((n) => LEGACY_SECTIONS[n] ?? n);
  return {
    version: r.version ?? f.version,
    page: {
      size: r.page?.size ?? f.page.size,
      margin: r.page?.margin ?? f.page.margin,
    },
    font: {
      family: r.font?.family ?? f.font.family,
      base_pt: r.font?.base_pt ?? f.font.base_pt,
    },
    colors: { ...f.colors, ...(r.colors ?? {}) },
    cv: {
      pages: r.cv?.pages ?? f.cv.pages,
      sections: sections(r.cv?.sections, f.cv.sections),
      sidebar: sections(r.cv?.sidebar, f.cv.sidebar),
    },
    cover_letter: {
      din5008: r.cover_letter?.din5008 ?? f.cover_letter.din5008,
    },
  };
}

/**
 * The FileField URL may be absolute (host of whoever served the API); fetch same-origin via
 * the pathname so the vite `/media` proxy (dev) / nginx (prod) serves it.
 */
export function templatePath(url: string, origin?: string): string {
  try {
    return new URL(url, origin ?? window.location.origin).pathname;
  } catch {
    return url;
  }
}

export function useLayoutSpec(layout: LayoutRow | undefined) {
  return useQuery({
    queryKey: [
      "jac",
      "layout-spec",
      layout?.id ?? "none",
      layout?.template ?? "",
    ],
    queryFn: async (): Promise<LayoutSpec> => {
      if (!layout?.template) return FALLBACK_SPEC;
      const res = await fetch(templatePath(layout.template));
      if (!res.ok) throw new Error(`layout template: HTTP ${res.status}`);
      return parseLayoutSpec(await res.json());
    },
    enabled: layout !== undefined,
    staleTime: 5 * 60 * 1000,
  });
}
