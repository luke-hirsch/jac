import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "./paginated";

/* ---------- provider model ---------- */

export type Provider = "anthropic" | "openai" | "google" | "custom" | "ollama";

export type LLMConfigRow = {
  id: number;
  alias: string;
  provider: Provider;
  model: string;
  url: string;
  max_tokens: number | null;
  extra: Record<string, unknown>;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

/** A discrete `extra` input rendered for the commercial (structured) providers. */
export type ExtraFieldSpec =
  | {
      kind: "select";
      key: string;
      label: string;
      options: string[];
      help?: string;
    }
  | { kind: "number"; key: string; label: string; help?: string };

export type ProviderSpec = {
  label: string;
  modelPlaceholder: string;
  url: "hidden" | "optional" | "required";
  apiKey: "required" | "optional";
  /** "structured" → render `extraFields`; "json" → render the raw JSON textarea. */
  extra: "structured" | "json";
  extraFields: ExtraFieldSpec[];
  /** placeholder/help shown above the JSON textarea (json providers only). */
  jsonHint?: string;
};

// Keys map 1:1 to LLMConfig.Provider.choices on the backend.
export const PROVIDER_SPECS: Record<Provider, ProviderSpec> = {
  anthropic: {
    label: "Anthropic",
    modelPlaceholder: "claude-opus-4-8",
    url: "hidden",
    apiKey: "required",
    extra: "structured",
    extraFields: [
      {
        kind: "number",
        key: "max_uses",
        label: "Web search max uses",
        help: "Cap on web_search tool calls per run (default 5). Leave blank for default.",
      },
    ],
  },
  openai: {
    label: "OpenAI",
    modelPlaceholder: "gpt-5.1",
    url: "optional",
    apiKey: "required",
    extra: "structured",
    extraFields: [
      {
        kind: "select",
        key: "reasoning_effort",
        label: "Reasoning effort",
        options: ["", "minimal", "low", "medium", "high"],
        help: "Reasoning models only (o-series / gpt-5.x). Ignored otherwise.",
      },
    ],
  },
  google: {
    label: "Google",
    modelPlaceholder: "gemini-2.5-pro",
    url: "hidden",
    apiKey: "required",
    extra: "structured",
    extraFields: [
      {
        kind: "select",
        key: "search_tool",
        label: "Search tool",
        options: ["", "google_search"],
        help: "Grounding tool for web search (default google_search).",
      },
    ],
  },
  custom: {
    label: "Custom (OpenAI-compatible HTTP)",
    modelPlaceholder: "llama3",
    url: "required",
    apiKey: "optional",
    extra: "json",
    extraFields: [],
    jsonHint:
      'Forwarded into the request payload, e.g. {"think": false, "timeout": 120}',
  },
  ollama: {
    label: "Ollama (native /api/chat)",
    modelPlaceholder: "llama3.2:1b",
    url: "required",
    apiKey: "optional",
    extra: "json",
    extraFields: [],
    jsonHint:
      'Native fields, e.g. {"think": false, "embed_model": "qwen3-embedding:0.6b", "keep_alive": "5m", "options": {"num_predict": 512}}',
  },
};

/* ---------- form state <-> payload ---------- */

export type ConfigFormState = {
  alias: string;
  provider: Provider;
  model: string;
  url: string;
  max_tokens: string; // text input; "" = unset
  api_key: string; // "" = don't send (keep existing key on edit)
  extraJson: string; // json providers
  extraFields: Record<string, string>; // structured providers, keyed by ExtraFieldSpec.key
};

export type ConfigPayload = {
  alias: string;
  provider: Provider;
  model: string;
  url: string;
  max_tokens: number | null;
  extra: Record<string, unknown>;
  api_key?: string; // present only when the user entered one
};

/** Build `extra` from the structured field values, dropping empties; numbers parsed. */
export function buildStructuredExtra(
  spec: ProviderSpec,
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of spec.extraFields) {
    const raw = (values[f.key] ?? "").trim();
    if (!raw) continue;
    out[f.key] = f.kind === "number" ? Number(raw) : raw;
  }
  return out;
}

/** Parse the raw JSON textarea into an object, or throw a friendly Error. */
export function parseExtraJson(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error("Extra config must be valid JSON.");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(
      'Extra config must be a JSON object, e.g. {"think": false}.',
    );
  }
  return parsed as Record<string, unknown>;
}

/** Assemble the request body. Throws (via parseExtraJson) on bad JSON. */
export function toPayload(s: ConfigFormState): ConfigPayload {
  const spec = PROVIDER_SPECS[s.provider];
  const extra =
    spec.extra === "json"
      ? parseExtraJson(s.extraJson)
      : buildStructuredExtra(spec, s.extraFields);
  const payload: ConfigPayload = {
    alias: s.alias.trim(),
    provider: s.provider,
    model: s.model.trim(),
    url: spec.url === "hidden" ? "" : s.url.trim(),
    max_tokens: s.max_tokens.trim() ? Number(s.max_tokens) : null,
    extra,
  };
  const key = s.api_key.trim();
  if (key) payload.api_key = key; // omit when blank → keep existing key
  return payload;
}

/** Seed form state from an existing row (or empty defaults for a new config). */
export function rowToState(row?: LLMConfigRow): ConfigFormState {
  const provider: Provider = row?.provider ?? "anthropic";
  const spec = PROVIDER_SPECS[provider];
  const extra = row?.extra ?? {};
  return {
    alias: row?.alias ?? "",
    provider,
    model: row?.model ?? "",
    url: row?.url ?? "",
    max_tokens: row?.max_tokens != null ? String(row.max_tokens) : "",
    api_key: "",
    extraJson:
      spec.extra === "json" && Object.keys(extra).length
        ? JSON.stringify(extra, null, 2)
        : "",
    extraFields: Object.fromEntries(
      spec.extraFields.map((f) => [
        f.key,
        extra[f.key] != null ? String(extra[f.key]) : "",
      ]),
    ),
  };
}

/** When the provider changes mid-form, reset only the provider-specific fields. */
export function switchProvider(
  s: ConfigFormState,
  provider: Provider,
): ConfigFormState {
  const spec = PROVIDER_SPECS[provider];
  return {
    ...s,
    provider,
    url: spec.url === "hidden" ? "" : s.url,
    extraJson: "",
    extraFields: Object.fromEntries(spec.extraFields.map((f) => [f.key, ""])),
  };
}

/* ---------- resolved alias capabilities (generation UI) ---------- */

export type AliasStrength = "light" | "standard" | "strong";

/** One row of GET /api/llm/aliases/ — the alias as the pipeline resolves it,
 *  including the "default" settings fallback. */
export type AliasInfo = {
  alias: string;
  provider: Provider | "";
  model: string;
  strength: AliasStrength;
  supports_embed: boolean;
  supports_web_search: boolean;
};

/** Providers that cost nothing to call — mirrors backend
 *  llm_connector.conf.FREE_PROVIDERS. The light grade is the zero-cost showcase
 *  rung, so its picker refuses paid providers outright. */
export const FREE_PROVIDERS: ReadonlySet<string> = new Set(["ollama", "custom"]);

export function isFreeProvider(provider: Provider | ""): boolean {
  return FREE_PROVIDERS.has(provider);
}

/**
 * Aliases that can actually run a given grade — what the model picker offers:
 *  - light rides on embeddings AND stays free: only embed-capable, non-paid
 *    (ollama/custom) aliases qualify — the showcase rung never spends money;
 *  - standard/strong need at least that generative strength (a 1B chat model
 *    can't do the strong rung; the strong picker drops the light server default).
 * Blank grade = auto-detect from the alias, so anything goes.
 */
export function aliasesForGrade(
  aliases: AliasInfo[],
  grade: AliasStrength | "",
): AliasInfo[] {
  switch (grade) {
    case "light":
      return aliases.filter(
        (a) => a.supports_embed && isFreeProvider(a.provider),
      );
    case "standard":
      return aliases.filter((a) => a.strength !== "light");
    case "strong":
      return aliases.filter((a) => a.strength === "strong");
    default:
      return aliases;
  }
}

/* ---------- per-grade model pins ---------- */

/** GET/PUT /api/llm/pins/ — the user's favourite alias per grade (null = unset). */
export type GradePins = Record<AliasStrength, string | null>;

/** The pinned alias for a grade, when it is actually offerable for that grade. */
export function pinnedAliasFor(
  grade: AliasStrength | "",
  pins: GradePins | undefined,
  allowed: AliasInfo[],
): string | null {
  if (!grade || !pins) return null;
  const pinned = pins[grade];
  if (!pinned) return null;
  return allowed.some((a) => a.alias === pinned) ? pinned : null;
}

/** Brand names for the "Search with …" recipient-address buttons. Providers
 *  without a household model name fall back to the alias itself. */
const SEARCH_BRANDS: Partial<Record<Provider, string>> = {
  anthropic: "Claude",
  openai: "GPT",
  google: "Gemini",
};

/**
 * One button per web-search-capable provider ("Search with Claude" / "… GPT" /
 * "… Gemini"), first alias of a provider wins — no button at all when nothing
 * capable is configured.
 */
export function addressSearchOptions(
  aliases: AliasInfo[],
): { alias: string; label: string }[] {
  const out: { alias: string; label: string }[] = [];
  const seen = new Set<string>();
  for (const a of aliases) {
    if (!a.supports_web_search) continue;
    const brand = (a.provider && SEARCH_BRANDS[a.provider]) || a.alias;
    if (seen.has(brand)) continue;
    seen.add(brand);
    out.push({ alias: a.alias, label: `Search with ${brand}` });
  }
  return out;
}

/* ---------- connectivity check ---------- */

/** Result of POST /api/llm/configs/<id>/check/ — the API twin of `llm_check`.
 *  A failed probe is a result (ok: false), not an HTTP error. */
export type CheckResult =
  | { ok: true; latency_ms: number }
  | { ok: false; error: string };

/** Inline row label: "OK · 812 ms" on success, the raw error text on failure. */
export function checkResultLabel(r: CheckResult): string {
  return r.ok ? `OK · ${r.latency_ms} ms` : r.error;
}

/* ---------- query hooks ---------- */

const URL = "/api/llm/configs/";
const KEY = ["llm", "configs"] as const;

export function useLLMConfigs() {
  return useQuery({
    queryKey: KEY,
    // The endpoint is paginated (PAGE_SIZE 100); a user's alias set fits one page.
    queryFn: async () => (await api<Page<LLMConfigRow>>(URL)).results,
  });
}

export function useLLMAliases() {
  return useQuery({
    queryKey: ["llm", "aliases"],
    queryFn: () => api<AliasInfo[]>("/api/llm/aliases/"),
  });
}

const PINS_KEY = ["llm", "pins"] as const;

export function useGradePins() {
  return useQuery({
    queryKey: PINS_KEY,
    queryFn: () => api<GradePins>("/api/llm/pins/"),
  });
}

/** Upsert one grade's pin; alias "" clears it. The server replies with the full pins dict. */
export function useSetGradePin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { strength: AliasStrength; alias: string }) =>
      api<GradePins>("/api/llm/pins/", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (pins) => qc.setQueryData(PINS_KEY, pins),
  });
}

export function useCreateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfigPayload) =>
      api<LLMConfigRow>(URL, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
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
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`${URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useCheckConfig() {
  return useMutation({
    mutationFn: (id: number) =>
      api<CheckResult>(`${URL}${id}/check/`, { method: "POST" }),
  });
}
