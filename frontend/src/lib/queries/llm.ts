import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "./paginated";

/* ---------- executors (generate panel + every model picker) ---------- */
export type Mode = "manual" | "standard" | "high";

export type CatalogModel = { id: string; label: string; default?: boolean };

// One row of GET /api/llm/executors/
export type ExecutorRow = {
  provider: string;
  label: string;
  self_hosted: boolean;
  configured: boolean;
  reachable: boolean | null; // HirschAI only, null for others
  default: boolean;
  models: CatalogModel[];
  modes: Mode[];
  knobs: KnobSpec;
};
export type KnobSpec = Record<
  string,
  { choices?: string[]; min?: number; max?: number; excludes?: string[] }
>;
// this is the important bits
export function useExecutors() {
  return useQuery({
    queryKey: ["llm", "executors"],
    queryFn: () => api<ExecutorRow[]>("/api/llm/executors/"),
    // The panel must notice HirschAI coming up / going down. The server probe is
    // cached 30 s — poll at that cadence and on refocus; faster buys nothing.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

// error message
export function executorDisabledReason(row: ExecutorRow): string | null {
  if (row.self_hosted) return row.reachable === false ? "offline" : null;
  return row.configured ? null : "no API key";
}

// default or offline (=not reachable AND not configured)
export function defaultExecutorRow(rows: ExecutorRow[]): ExecutorRow | null {
  const usable = rows.filter((r) => executorDisabledReason(r) === null);
  return usable.find((r) => r.default) ?? usable[0] ?? null;
}

// self explanetory
export function defaultModelFor(row: ExecutorRow): string {
  return row.models.find((m) => m.default)?.id ?? row.models[0]?.id ?? "";
}

// default mode. HirschAi doesn't have high mode (yet)
export function defaultModeFor(row: ExecutorRow): Mode {
  return row.modes.includes("high") ? "high" : "standard";
}

// self explanetory
export function providerLabel(rows: ExecutorRow[], provider: string): string {
  return rows.find((r) => r.provider === provider)?.label ?? provider;
}

// see reachable attribute
export type CheckResult =
  | { ok: true; latency_ms: number }
  | { ok: false; error: string };

// belongsd to reachable check
export function checkResultLabel(r: CheckResult): string {
  return r.ok ? `OK · ${r.latency_ms} ms` : r.error;
}

/* ---------- configs (one credential per commercial provider) ---------- */

// llm config model
export type LLMConfigRow = {
  id: number;
  provider: string;
  default: boolean;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

//things that need to be configured, but not read
export type ConfigPayload = {
  provider: string;
  default?: boolean;
  api_key?: string;
};

// clean up the payload (whitepace etc)
export function configPayload(input: {
  provider: string;
  apiKey?: string;
  makeDefault?: boolean;
}): ConfigPayload {
  const p: ConfigPayload = { provider: input.provider };
  const key = (input.apiKey ?? "").trim();
  if (key) p.api_key = key;
  if (input.makeDefault !== undefined) p.default = input.makeDefault;
  return p;
}

/* ---------- queries ---------- */
const URL = "/api/llm/configs/";
const KEY = ["llm", "configs"] as const;

export function useLLMConfigs() {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => (await api<Page<LLMConfigRow>>(URL)).results,
  });
}

export function useCreateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfigPayload) =>
      api<LLMConfigRow>(URL, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ConfigPayload }) =>
      api<LLMConfigRow>(`${URL}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}

export function useDeleteConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`${URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}

export function useCheckConfig() {
  return useMutation({
    mutationFn: (id: number) =>
      api<CheckResult>(`${URL}${id}/check/`, { method: "POST" }),
  });
}
