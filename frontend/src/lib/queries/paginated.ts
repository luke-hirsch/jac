import { api } from "@/lib/api";

// Mirrors DRF's `PAGE_SIZE` (REST_FRAMEWORK in settings). Keep in sync with the
// backend — it's only used to render page counts/ranges; navigation itself
// relies on the `next`/`previous` links DRF returns.
export const PAGE_SIZE = 100;

export type Page<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type ListParams = {
  search?: string;
  ordering?: string;
  page?: number;
  filters?: Record<string, string | number | undefined>;
};

export function buildQuery(params: ListParams) {
  const u = new URLSearchParams();
  if (params.search) u.set("search", params.search);
  if (params.ordering) u.set("ordering", params.ordering);
  if (params.page && params.page > 1) u.set("page", String(params.page));
  for (const [k, v] of Object.entries(params.filters ?? {})) {
    if (v !== undefined && v !== "") u.set(k, String(v));
  }
  const q = u.toString();
  return q ? `?${q}` : "";
}

export function fetchPage<T>(url: string, params: ListParams = {}) {
  // buildQuery already returns a leading "?" (or ""), so don't add another.
  return api<Page<T>>(`${url}${buildQuery(params)}`);
}

/**
 * Walk every page and return the flat list. For views that need the whole
 * (filtered) set rather than a window — e.g. the skills bubble cloud, which
 * can't paginate. Pass everything except `page`; this owns the page cursor.
 */
export async function fetchAll<T>(
  url: string,
  params: Omit<ListParams, "page"> = {},
): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; ; page += 1) {
    const res = await fetchPage<T>(url, { ...params, page });
    out.push(...res.results);
    if (!res.next) return out;
  }
}
