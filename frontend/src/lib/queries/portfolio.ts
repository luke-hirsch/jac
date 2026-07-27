import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";

/** Mirrors spa/portfolio.py build_payload — server-joined, redacted, self-contained. */
export type PortfolioItem = {
  id: string; // "job:12" | "block:7" …
  type:
    | "job"
    | "project"
    | "skill"
    | "education"
    | "certification"
    | "language"
    | "block";
  title: string;
  subtitle?: string;
  description?: string;
  started?: string | null;
  ended?: string | null;
  url?: string;
  domains: string[];
  // block-only:
  kind?: "text" | "image";
  body?: string;
  image_url?: string | null;
  alt_text?: string;
};

export type PortfolioPayload = {
  kind: "manual" | "application" | "native";
  title: string;
  intro: string;
  owner: {
    display_name: string;
    bio: string;
    avatar_url: string | null;
    website?: string;
    linkedin_url?: string;
    github_url?: string;
  };
  featured: PortfolioItem[];
  more: PortfolioItem[];
};

export type RankedId = { id: string; score: number };

export function usePortfolioLink(slug: string) {
  return useQuery({
    queryKey: ["portfolio", "link", slug],
    queryFn: () => api<PortfolioPayload>(`/api/spa/portfolio/links/${slug}/`),
    retry: false, // a 404 is an answer (revoked/unknown), not a flake
  });
}

export function useNativePortfolio(search: ExploreSearch) {
  const params = new URLSearchParams();
  if (search.d?.length) params.set("domains", search.d.join(","));
  if (search.lucky) params.set("lucky", "1");
  const qs = params.toString();
  return useQuery({
    queryKey: ["portfolio", "native", qs],
    queryFn: () =>
      api<PortfolioPayload>(`/api/spa/portfolio/native/${qs ? `?${qs}` : ""}`),
    retry: false,
  });
}

/** The embed finale — POST but semantically a read; cached per (q, d) and never
 *  retried: the 6/h throttle makes retries actively harmful. */
export function usePortfolioRank(search: ExploreSearch) {
  const q = search.q?.trim() ?? "";
  return useQuery({
    queryKey: ["portfolio", "rank", q, search.d ?? []],
    queryFn: () =>
      api<{ ranked: RankedId[] }>("/api/spa/portfolio/native/rank/", {
        method: "POST",
        body: JSON.stringify({ query: q, domains: search.d ?? [] }),
      }),
    enabled: q.length > 0,
    staleTime: Infinity,
    retry: false,
  });
}
