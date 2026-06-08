function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : null;
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, data: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.status = status;
    this.data = data;
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (UNSAFE.has(method)) {
    const token = readCookie("csrftoken");
    if (token) headers.set("X-CSRFToken", token);
  }

  const res = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export type AllauthError = { message: string; code: string; param?: string };

export function allauthErrorsByField(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const data = error.data as { errors?: AllauthError[] } | undefined;
  const map: Record<string, string> = {};
  for (const e of data?.errors ?? []) {
    if (e.param) map[e.param] = e.message;
    else map["__non_field__"] = e.message;
  }
  return map;
}
