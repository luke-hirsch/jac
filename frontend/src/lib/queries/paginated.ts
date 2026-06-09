import { api } from "@/lib/api";

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
