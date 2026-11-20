import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, csrfHeaders } from "@/lib/api";
import { downloadBlob } from "@/lib/export";
import type { Page } from "./paginated";

/* ---------- types ---------- */

// One row of GET /api/jac/skill/tokens/
export type AccessTokenRow = {
  id: number;
  name: string;
  prefix: string; // the key's first 12 characters, to tell tokens apart
  created_at: string;
  last_used_at: string | null;
};

/** What minting a token answers: the row plus its key, in this one response only. */
export type NewAccessToken = AccessTokenRow & { key: string };

/** What saving an application the skill wrote answers (the skill's shape, plus warnings). */
export type ImportedApplication = {
  id: number;
  title: string;
  url: string;
  warnings: string[];
};

/* ---------- pure helpers (unit-tested) ---------- */

/** The shell lines Claude Code reads the skill's address and token from. */
export function envLines(origin: string, key: string): string {
  return `export JAC_URL=${origin}\nexport JAC_TOKEN=${key}`;
}

// DRF keys that name no field: their messages read on their own.
const FIELDLESS = new Set(["detail", "non_field_errors"]);

/** DRF's errors as readable lines: `{posting: {text: ["…"]}}` reads `posting.text: …`. */
export function validationLines(data: unknown, path: string[] = []): string[] {
  if (typeof data === "string") {
    const field = path.join(".");
    return [field ? `${field}: ${data}` : data];
  }
  if (Array.isArray(data)) {
    // A field's list of messages, or one error object per item of a list.
    return data.flatMap((item, i) =>
      validationLines(
        item,
        typeof item === "string" ? path : [...path, `${i}`],
      ),
    );
  }
  if (data && typeof data === "object") {
    return Object.entries(data).flatMap(([key, value]) =>
      validationLines(value, FIELDLESS.has(key) ? path : [...path, key]),
    );
  }
  return [];
}

/** What a failed call says, line by line; `fallback` when it said nothing readable (an HTML
 *  error page, a dropped connection). */
export function errorLines(error: unknown, fallback: string): string[] {
  const lines =
    error instanceof ApiError && error.data && typeof error.data === "object"
      ? validationLines(error.data)
      : [];
  return lines.length ? lines : [fallback];
}

/** The file the skill wrote, parsed. Throws with a message for the user when it isn't one. */
export function parseImportFile(text: string): Record<string, unknown> {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(
      "That file isn't valid JSON. Pick the .json file Claude wrote.",
    );
  }
  if (!data || typeof data !== "object" || Array.isArray(data))
    throw new Error("That file isn't an application from Claude.");
  return data as Record<string, unknown>;
}

/* ---------- query hooks ---------- */

const SKILL_API = "/api/jac/skill/";
const KEY = ["jac", "skill", "tokens"] as const;

/** POST the bundle and return the zip. A raw fetch: `api()` reads the body as text. */
export async function fetchSkillBundle(): Promise<Blob> {
  const res = await fetch(`${SKILL_API}bundle/`, {
    method: "POST",
    headers: csrfHeaders(),
    credentials: "same-origin",
  });
  if (!res.ok) {
    const isJson = res.headers
      .get("content-type")
      ?.includes("application/json");
    throw new ApiError(
      res.status,
      isJson ? await res.json().catch(() => null) : null,
    );
  }
  return res.blob();
}

export function useAccessTokens() {
  return useQuery({
    queryKey: KEY,
    // At most ten per user: one page holds them all.
    queryFn: async () =>
      (await api<Page<AccessTokenRow>>(`${SKILL_API}tokens/`)).results,
  });
}

export function useCreateAccessToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<NewAccessToken>(`${SKILL_API}tokens/`, {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRevokeAccessToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${SKILL_API}tokens/${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Download the skill as a zip. The server mints a token for it, so the list changes. */
export function useDownloadSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      downloadBlob(await fetchSkillBundle(), "jac-skill.zip"),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Save an application from the file the skill wrote, sent as it is: the server validates it. */
export function useImportApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<ImportedApplication>(`${SKILL_API}applications/`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["jac", "applications"] }),
  });
}
