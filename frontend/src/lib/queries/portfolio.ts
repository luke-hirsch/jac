import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

/** Mirrors spa PortfolioLinkSerializer (owner-side). `content` is `{}` on a fresh
 *  application link (before the sent-freeze), so its keys are optional. */
export type PortfolioLinkRow = {
  id: number;
  slug: string;
  kind: "manual" | "application";
  title: string;
  intro: string;
  application: number | null;
  content: { featured?: string[]; domains?: string[]; hide_explore?: boolean };
  revoked_at: string | null;
  url: string; // absolute, FRONTEND_URL-based — the QR encodes exactly this
  visits: number;
  created_at: string;
  updated_at: string;
};

/** Idempotent get-or-create — safe to call again after a reload. */
export function createApplicationLink(applicationId: number) {
  return api<PortfolioLinkRow>(
    `/api/jac/applications/${applicationId}/portfolio-link/`,
    { method: "POST" },
  );
}

export function revokePortfolioLink(id: number) {
  return api<PortfolioLinkRow>(
    `/api/spa/portfolio/manage/links/${id}/revoke/`,
    { method: "POST" },
  );
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

/* ---------- owner-side management (guide 5) ---------- */

const BLOCKS_URL = "/api/spa/portfolio/manage/blocks/";
const LINKS_URL = "/api/spa/portfolio/manage/links/";

/** Mirrors spa PortfolioBlockSerializer. `image` is a media URL (or "" / null); domains
 *  are pks (the block M2M — links store domain *names* instead). */
export type PortfolioBlockRow = {
  id: number;
  kind: "text" | "image";
  title: string;
  body: string;
  image: string | null;
  alt_text: string;
  domains: number[];
  favourite: boolean;
  order: number;
  is_active: boolean;
  updated_at: string;
};

export type BlockInput = {
  kind: "text" | "image";
  title: string;
  body: string;
  alt_text: string;
  domains: number[];
  favourite: boolean;
  order: number;
  is_active: boolean;
};

export type LinkInput = {
  slug: string;
  title: string;
  intro: string;
  content: { featured: string[]; domains: string[]; hide_explore: boolean };
};

/* blocks */

export function usePortfolioBlocks() {
  return useQuery({
    queryKey: ["portfolio", "blocks"],
    queryFn: () => api<PortfolioBlockRow[]>(BLOCKS_URL),
  });
}

function blockMultipart(input: BlockInput, image: File): FormData {
  const fd = new FormData();
  fd.set("kind", input.kind);
  fd.set("title", input.title);
  fd.set("body", input.body);
  fd.set("alt_text", input.alt_text);
  fd.set("favourite", String(input.favourite));
  fd.set("order", String(input.order));
  fd.set("is_active", String(input.is_active));
  for (const d of input.domains) fd.append("domains", String(d));
  fd.set("image", image);
  return fd;
}

/** Create or update a block. With a new `image` File it goes multipart (required to
 *  create an image block — the serializer rejects kind=image without a file); otherwise
 *  plain JSON, which keeps domain edits (incl. clearing) clean. Caveat: clearing all
 *  domains *in the same save as an image swap* isn't expressible over multipart — do the
 *  tag change and the image change in separate saves if you hit it. */
export function useSaveBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
      image,
    }: {
      id?: number;
      input: BlockInput;
      image?: File | null;
    }) => {
      const url = id ? `${BLOCKS_URL}${id}/` : BLOCKS_URL;
      const method = id ? "PATCH" : "POST";
      const body = image ? blockMultipart(input, image) : JSON.stringify(input);
      return api<PortfolioBlockRow>(url, { method, body });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "blocks"] }),
  });
}

export function useDeleteBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${BLOCKS_URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "blocks"] }),
  });
}

/* links */

export function usePortfolioLinks() {
  return useQuery({
    queryKey: ["portfolio", "links"],
    queryFn: () => api<PortfolioLinkRow[]>(LINKS_URL),
  });
}

export function useCreateLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: LinkInput) =>
      api<PortfolioLinkRow>(LINKS_URL, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}

export function useUpdateLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<LinkInput> }) =>
      api<PortfolioLinkRow>(`${LINKS_URL}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}

export function useDeleteLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${LINKS_URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}

/** Soft-kill (guide 4's `revokePortfolioLink`) with list invalidation. */
export function useRevokeLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => revokePortfolioLink(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}
