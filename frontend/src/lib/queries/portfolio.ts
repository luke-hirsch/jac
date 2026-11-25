import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  blockRequestBody,
  type BlockFiles,
  type BlockForm,
} from "@/lib/portfolio/block-form";
import {
  needsPersonalise,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";

/** spa.models.PortfolioBlock.Kind. What each kind holds: spa/blocks.py. */
export type BlockKind =
  | "text"
  | "image"
  | "quote"
  | "figures"
  | "cta"
  | "linklist"
  | "media";
export type BlockFigure = { value: string; label: string };
export type BlockLinkItem = { title: string; url: string; note?: string };
/** A block's own fields, by kind; `{}` for text, image and media. */
export type BlockData = {
  by?: string; // quote
  role?: string; // quote
  figures?: BlockFigure[]; // figures
  label?: string; // cta
  url?: string; // cta
  items?: BlockLinkItem[]; // linklist
};

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
  /** Jobs and projects: the skills on this page they used (spa.portfolio._link_skills). */
  skills?: string[];
  // block-only:
  kind?: BlockKind;
  body?: string;
  data?: BlockData;
  /** An image block's image; a media block's poster. */
  image_url?: string | null;
  media_url?: string | null;
  /** "video/mp4" | "video/webm" | "audio/mpeg" | "audio/mp4" | "audio/ogg", from the stored file. */
  media_type?: string | null;
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
 *  6/h throttle makes retries actively harmful. Only for an answer with picks or a question
 *  (needsPersonalise): the standard page and a lucky reshuffle mustn't burn the budget.
 *  `startedAt` dates the give-up. */
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
    enabled: needsPersonalise(search),
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

/** Mirrors spa PortfolioStandardSerializer: the owner's standard page. Before its first save
 *  the server answers the empty page, with `updated_at` null. `url` shows it without the
 *  questions. */
export type StandardPageRow = {
  title: string;
  intro: string;
  content: { featured: string[] };
  url: string;
  updated_at: string | null;
};

export type StandardPageInput = {
  title: string;
  intro: string;
  content: { featured: string[] };
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
const STANDARD_URL = "/api/spa/portfolio/manage/standard/";

/** Mirrors spa PortfolioBlockSerializer. `image` and `media` are media URLs (or null);
 *  domains are pks (the block M2M — links store domain *names* instead). */
export type PortfolioBlockRow = {
  id: number;
  kind: BlockKind;
  title: string;
  body: string;
  image: string | null;
  media: string | null;
  data: BlockData;
  alt_text: string;
  domains: number[];
  links: string[];
  favourite: boolean;
  order: number;
  is_active: boolean;
  updated_at: string;
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

/** Create or update a block: JSON, or multipart when a file goes along
 *  (`blockRequestBody`). */
export function useSaveBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      form,
      files,
    }: {
      id?: number;
      form: BlockForm;
      files?: BlockFiles;
    }) => {
      const url = id ? `${BLOCKS_URL}${id}/` : BLOCKS_URL;
      const method = id ? "PATCH" : "POST";
      return api<PortfolioBlockRow>(url, {
        method,
        body: blockRequestBody(form, files),
      });
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

/* the standard page */

export function useStandardPage() {
  return useQuery({
    queryKey: ["portfolio", "standard"],
    queryFn: () => api<StandardPageRow>(STANDARD_URL),
  });
}

/** The first save makes the row; every later one edits it. */
export function useSaveStandardPage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: StandardPageInput) =>
      api<StandardPageRow>(STANDARD_URL, {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
    onSuccess: (row) => qc.setQueryData(["portfolio", "standard"], row),
  });
}
