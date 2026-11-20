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

/** A field's value in a career change. A string[] is a relation list: skill ids ("skill:12")
 *  or domain names. */
export type FieldValue = string | number | boolean | null | string[];

/** One entry a batch touched, with the touched fields only. */
export type CareerChange = {
  id: string; // "job:3", "skill:44"
  label: string; // "Backend Dev at Acme"
  why: string; // Claude's one-line reason
  before: Record<string, FieldValue> | null; // null: the batch created the entry
  after: Record<string, FieldValue> | null;
};

// One row of GET /api/jac/skill/career/changes/
export type CareerChangeBatch = {
  id: number;
  created_at: string;
  source: "claude" | "import"; // applied by the skill's token, or imported here from a file
  undone_at: string | null;
  changes: CareerChange[];
  labels: Record<string, string>; // names for the ids inside before/after values
};

/** What importing changes answers: the batch, plus the ids of the entries it created. */
export type AppliedCareerChanges = CareerChangeBatch & {
  created: Record<string, string>; // {"new:docker": "skill:44"}
};

/** A file the skill wrote, told apart by what it holds. */
export type ImportFile =
  | { kind: "application"; body: Record<string, unknown> }
  | { kind: "changes"; changes: unknown[] };

/** One field of a change as the list shows it. A relation list shows what was added and
 *  removed; any other value before and after, with null for empty. */
export type ChangeRow =
  | {
      kind: "value";
      field: string;
      before: string | null;
      after: string | null;
    }
  | { kind: "list"; field: string; added: string[]; removed: string[] };

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

// What only an application file carries.
const APPLICATION_KEYS = ["posting", "entries", "letter"];

/** The file the skill wrote, parsed: a list under `changes` makes it career changes (sent on
 *  their own: an `applied_as` beside them is the server's call), a posting, entries or letter
 *  an application. Throws with a message for the user when it's neither. */
export function parseImportFile(text: string): ImportFile {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(
      "That file isn't valid JSON. Pick the .json file Claude wrote.",
    );
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const file = data as Record<string, unknown>;
    if (Array.isArray(file.changes))
      return { kind: "changes", changes: file.changes };
    if (APPLICATION_KEYS.some((key) => key in file))
      return { kind: "application", body: file };
  }
  throw new Error(
    "That file isn't an application or career changes from Claude.",
  );
}

/** A value as the list shows it: ids by their label, null when there's nothing. */
function shown(value: unknown, labels: Record<string, string>): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return labels[value] ?? value;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function listOf(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  return value === null || value === undefined ? [] : [String(value)];
}

/** A change's fields as rows, in the order the change names them. A field that reads the same
 *  before and after is left out, so a new entry shows only the fields it was given. */
export function changeRows(
  change: CareerChange,
  labels: Record<string, string>,
): ChangeRow[] {
  const before: Record<string, unknown> = change.before ?? {};
  const after: Record<string, unknown> = change.after ?? {};
  const fields = [...new Set([...Object.keys(after), ...Object.keys(before)])];
  return fields.flatMap((key): ChangeRow[] => {
    const field = key.replace(/_/g, " ");
    if (Array.isArray(before[key]) || Array.isArray(after[key])) {
      const was = listOf(before[key]);
      const now = listOf(after[key]);
      const added = now.filter((v) => !was.includes(v));
      const removed = was.filter((v) => !now.includes(v));
      if (!added.length && !removed.length) return [];
      const name = (v: string) => labels[v] ?? v;
      return [
        {
          kind: "list",
          field,
          added: added.map(name),
          removed: removed.map(name),
        },
      ];
    }
    const was = shown(before[key], labels);
    const now = shown(after[key], labels);
    return was === now
      ? []
      : [{ kind: "value", field, before: was, after: now }];
  });
}

/** A 409 with `stale` as lines: its detail, then each entry edited in jac since, by its name
 *  where `batch` knows it, else by id (an import has no batch yet, and the file carries no
 *  names). Any other failure reads as `errorLines`. */
export function staleLines(
  error: unknown,
  fallback: string,
  batch?: Pick<CareerChangeBatch, "changes" | "labels">,
): string[] {
  const data =
    error instanceof ApiError && error.data && typeof error.data === "object"
      ? (error.data as { detail?: unknown; stale?: unknown; labels?: unknown })
      : {};
  if (!Array.isArray(data.stale) || !data.stale.length)
    return errorLines(error, fallback);
  // The 409 names its own entries; a batch the page already holds may name more.
  const names: Record<string, string> = {
    ...(data.labels && typeof data.labels === "object"
      ? (data.labels as Record<string, string>)
      : {}),
    ...batch?.labels,
  };
  for (const change of batch?.changes ?? [])
    if (change.label) names[change.id] = change.label;
  return [
    ...(typeof data.detail === "string" ? [data.detail] : []),
    ...data.stale.map((id) => names[String(id)] ?? String(id)),
  ];
}

/* ---------- query hooks ---------- */

const SKILL_API = "/api/jac/skill/";
const KEY = ["jac", "skill", "tokens"] as const;
const CHANGES_KEY = ["jac", "skill", "career-changes"] as const;

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

export function useCareerChanges() {
  return useQuery({
    queryKey: CHANGES_KEY,
    // The newest 20 batches, as a plain list: no pages.
    queryFn: () => api<CareerChangeBatch[]>(`${SKILL_API}career/changes/`),
  });
}

/** Apply the changes from a file the skill wrote, sent as they are: the server validates them.
 *  They edit the career, so every `["jac"]` query refetches, this list among them. */
export function useImportCareerChanges() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (changes: unknown[]) =>
      api<AppliedCareerChanges>(`${SKILL_API}career/changes/`, {
        method: "POST",
        body: JSON.stringify({ changes }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jac"] }),
  });
}

export function useUndoCareerChange() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<CareerChangeBatch>(`${SKILL_API}career/changes/${id}/undo/`, {
        method: "POST",
      }),
    // On a 409 too: then another tab undid it first, and this list is the one behind.
    onSettled: () => qc.invalidateQueries({ queryKey: ["jac"] }),
  });
}
