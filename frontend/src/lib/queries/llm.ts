import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "./paginated";

/* ---------- executors (generate panel + every model picker) ---------- */
export type Mode = "manual" | "standard" | "high";

// Per model: what the provider's capability data says it takes (today only `effort`).
export type KnobSpec = Record<string, { choices: string[] }>;

// One model the key reaches, as the last key save or Check stored it.
export type CatalogModel = {
  id: string;
  label: string;
  default?: boolean;
  knobs: KnobSpec;
};

// One row of GET /api/llm/executors/
export type ExecutorRow = {
  provider: string;
  label: string;
  self_hosted: boolean;
  configured: boolean;
  /** HirschAI only: what its last real call found out (never a probe). null = no call lately, which
   *  is normal: it may be asleep, and it wakes when a job needs it. */
  reachable: boolean | null;
  default: boolean;
  /** The key's own list, newest first. Empty for HirschAI (its model is fixed server-side) and
   *  for a key the provider hasn't listed yet. */
  models: CatalogModel[];
  modes: Mode[];
};
// this is the important bits
export function useExecutors() {
  return useQuery({
    queryKey: ["llm", "executors"],
    queryFn: () => api<ExecutorRow[]>("/api/llm/executors/"),
    // Costs the model server nothing: the state comes from real calls, not from a probe.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

// Why a row can't be picked. HirschAI always can: a run queues and waits for it. A key the
// provider hasn't listed yet has nothing to run on: Check on the account page lists it.
export function executorDisabledReason(row: ExecutorRow): string | null {
  if (row.self_hosted) return null;
  if (!row.configured) return "no API key";
  return row.models.length > 0 ? null : "no model list yet";
}

// the row's default, else the first one that can be picked
export function defaultExecutorRow(rows: ExecutorRow[]): ExecutorRow | null {
  const usable = rows.filter((r) => executorDisabledReason(r) === null);
  return usable.find((r) => r.default) ?? usable[0] ?? null;
}

// self explanetory
export function defaultModelFor(row: ExecutorRow): string {
  return row.models.find((m) => m.default)?.id ?? row.models[0]?.id ?? "";
}

// the picked model's knobs; {} for HirschAI or a model the row doesn't list
export function knobsFor(row: ExecutorRow | null, modelId: string): KnobSpec {
  return row?.models.find((m) => m.id === modelId)?.knobs ?? {};
}

// On a model switch: keep only the knob values the new model offers too ("" = unset).
export function keepKnobs(
  values: Record<string, string>,
  spec: KnobSpec,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values).filter(
      ([name, v]) => v !== "" && (spec[name]?.choices ?? []).includes(v),
    ),
  );
}

// default mode. HirschAi doesn't have high mode (yet)
export function defaultModeFor(row: ExecutorRow): Mode {
  return row.modes.includes("high") ? "high" : "standard";
}

// self explanetory
export function providerLabel(rows: ExecutorRow[], provider: string): string {
  return rows.find((r) => r.provider === provider)?.label ?? provider;
}

// see reachable attribute. `models`: how many the key reaches after the check's listing
export type CheckResult =
  | { ok: true; latency_ms: number; models: number }
  | { ok: false; error: string; models: number };

// belongsd to reachable check
export function checkResultLabel(r: CheckResult): string {
  return r.ok ? `OK · ${r.latency_ms} ms · ${modelCount(r.models)}` : r.error;
}

function modelCount(n: number): string {
  return `${n} model${n === 1 ? "" : "s"}`;
}

/* ---------- configs (one credential per commercial provider) ---------- */

// llm config model
export type LLMConfigRow = {
  id: number;
  provider: string;
  default: boolean;
  has_api_key: boolean;
  transfer_consent_at: string | null;
  /** How many models the key reaches, and when the provider last listed them (null: not yet). */
  model_count: number;
  models_checked_at: string | null;
  created_at: string;
  updated_at: string;
};

// the account page's line under a key; `when` formats the timestamp (the locale's, by default)
export function modelListLabel(
  c: Pick<LLMConfigRow, "model_count" | "models_checked_at">,
  when: (iso: string) => string = (iso) => new Date(iso).toLocaleString(),
): string {
  if (!c.models_checked_at) return "No model list yet.";
  return `Reaches ${modelCount(c.model_count)} · checked ${when(c.models_checked_at)}.`;
}

//things that need to be configured, but not read
export type ConfigPayload = {
  provider: string;
  default?: boolean;
  api_key?: string;
  transfer_consent?: boolean;
};

// clean up the payload (whitepace etc)
export function configPayload(input: {
  provider: string;
  apiKey?: string;
  makeDefault?: boolean;
  transferConsent?: boolean;
}): ConfigPayload {
  const p: ConfigPayload = { provider: input.provider };
  const key = (input.apiKey ?? "").trim();
  if (key) p.api_key = key;
  if (input.makeDefault !== undefined) p.default = input.makeDefault;
  if (input.transferConsent) p.transfer_consent = true;
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
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<CheckResult>(`${URL}${id}/check/`, { method: "POST" }),
    // A check refreshes the key's model list: the count here, the pickers' models there.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm"] }),
  });
}
