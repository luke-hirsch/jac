import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
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
  links?: PortfolioItem[];
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

/* ---------- the public side: every call names the owner ---------- */

/** All portfolios share one host, so the owner is a path segment in every public call, and in
 *  every query key: two owners must never share a cache entry. The keys sit under "public", so no
 *  handle can prefix-match the owner-side ["portfolio", "blocks" | "links"] invalidations. */
const pub = (handle: string) => `/api/spa/public/${encodeURIComponent(handle)}`;

export const publicPaths = {
  link: (handle: string, slug: string) =>
    `${pub(handle)}/links/${encodeURIComponent(slug)}/`,
  native: (handle: string, qs = "") =>
    `${pub(handle)}/native/${qs ? `?${qs}` : ""}`,
  meta: (handle: string) => `${pub(handle)}/native/meta/`,
  prewarm: (handle: string) => `${pub(handle)}/native/prewarm/`,
  personalise: (handle: string) => `${pub(handle)}/native/personalise/`,
  personaliseJob: (id: string) =>
    `/api/spa/public/personalise/${encodeURIComponent(id)}/`,
};

export const portfolioLinkQuery = (handle: string, slug: string) =>
  queryOptions({
    queryKey: ["portfolio", "public", handle, "link", slug],
    queryFn: () => api<PortfolioPayload>(publicPaths.link(handle, slug)),
    retry: false, // a 404 is an answer (revoked/unknown), not a flake
  });

export function usePortfolioLink(handle: string, slug: string) {
  return useQuery(portfolioLinkQuery(handle, slug));
}

export function nativeQueryString(search: ExploreSearch): string {
  const params = new URLSearchParams();
  if (search.d?.length) params.set("domains", search.d.join(","));
  if (search.lucky) params.set("lucky", "1");
  if (search.focus) params.set("focus", search.focus);
  if (search.tone) params.set("tone", search.tone);
  return params.toString();
}

export const nativePortfolioQuery = (handle: string, search: ExploreSearch) => {
  const qs = nativeQueryString(search);
  return queryOptions({
    queryKey: ["portfolio", "public", handle, "native", qs],
    queryFn: () => api<PortfolioPayload>(publicPaths.native(handle, qs)),
    retry: false,
  });
};

export function useNativePortfolio(handle: string, search: ExploreSearch) {
  return useQuery(nativePortfolioQuery(handle, search));
}

export type NativeMeta = {
  domains: string[];
  tones: { value: string; label: string }[];
  focuses: { value: string; label: string }[];
};

export const nativeMetaQuery = (handle: string) =>
  queryOptions({
    queryKey: ["portfolio", "public", handle, "meta"],
    queryFn: () => api<NativeMeta>(publicPaths.meta(handle)),
    staleTime: Infinity,
    retry: false,
  });

export function useNativeMeta(handle: string) {
  return useQuery(nativeMetaQuery(handle));
}

/* ---------- a questionnaire visit's AI job: the ranking + the welcome ---------- */

export const PERSONALISE_POLL_MS = 3_000;
/** A cold start is about 3 minutes, and the server waits 4 for it (PORTFOLIO_PERSONALISE_WAIT_S).
 *  Past this the page shows what has arrived, or the standard portfolio. */
export const PERSONALISE_GIVE_UP_MS = 5 * 60_000;

/** Mirrors spa PortfolioPersonaliseStatusView. `ranked` is absent until the ranking is done, and
 *  null when there's none (no question, or it broke). `waking`: the model server is starting. */
export type PersonaliseStatus = {
  status: "pending" | "running" | "done" | "failed";
  waking: boolean;
  ranked?: RankedId[] | null;
  intro: string;
};

/** Queue the job, once per answer: POST, but cached like a read, and never retried, because the
 *  6/h throttle makes retries actively harmful. Skipped for lucky: a reshuffle mustn't burn the
 *  budget. `startedAt` dates the give-up. */
export const personaliseStartQuery = (handle: string, search: ExploreSearch) =>
  queryOptions({
    queryKey: [
      "portfolio",
      "public",
      handle,
      "personalise",
      search.d ?? [],
      search.q ?? "",
      search.focus,
      search.tone,
    ],
    queryFn: async () => {
      const { id } = await api<{ id: string }>(
        publicPaths.personalise(handle),
        {
          method: "POST",
          body: JSON.stringify({
            query: search.q?.trim() ?? "",
            domains: search.d ?? [],
            focus: search.focus ?? "balanced",
            tone: search.tone ?? "neutral",
          }),
        },
      );
      return { id, startedAt: Date.now() };
    },
    enabled: !search.lucky,
    staleTime: Infinity,
    retry: false,
  });

const settled = (job: PersonaliseStatus | undefined) =>
  job?.status === "done" || job?.status === "failed";

/** Where the job stands, polled until it's done or failed. A poll that hiccups is retried: it
 *  mustn't end a wait the visitor chose. */
export const personaliseJobQuery = (id: string | undefined) =>
  queryOptions({
    queryKey: ["portfolio", "public", "personalise-job", id ?? ""],
    queryFn: () => api<PersonaliseStatus>(publicPaths.personaliseJob(id ?? "")),
    enabled: Boolean(id),
    staleTime: Infinity,
    refetchInterval: (query: {
      state: { data: PersonaliseStatus | undefined };
    }) => (settled(query.state.data) ? false : PERSONALISE_POLL_MS),
    retry: 2,
  });
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
  links: string[];
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
  links: string[];
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
  for (const l of input.links) fd.append("links", l);
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
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["portfolio", "blocks"] }),
  });
}

export function useDeleteBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${BLOCKS_URL}${id}/`, { method: "DELETE" }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["portfolio", "blocks"] }),
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
