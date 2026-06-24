import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, allauthErrorsByField, api } from "@/lib/api";

/**
 * Characterisation tests for the fetch transport. `api()` reads `document.cookie`
 * (CSRF) only on unsafe methods and `fetch` always, so those are stubbed per-test;
 * the node default environment has no DOM. `allauthErrorsByField` is pure.
 */

type FakeInit = {
  status?: number;
  json?: unknown;
  text?: string;
  contentType?: string | null;
};

function fakeResponse({ status = 200, json, text, contentType }: FakeInit): Response {
  const ct =
    contentType === undefined
      ? json !== undefined
        ? "application/json"
        : "text/plain"
      : contentType;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (h: string) => (h.toLowerCase() === "content-type" ? ct : null) },
    json: async () => json,
    text: async () => text ?? "",
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("ApiError", () => {
  it("is an Error that defaults its message to the status and keeps the body", () => {
    const e = new ApiError(404, { detail: "nope" });
    expect(e).toBeInstanceOf(Error);
    expect(e.status).toBe(404);
    expect(e.message).toBe("HTTP 404");
    expect(e.data).toEqual({ detail: "nope" });
  });

  it("honours an explicit message", () => {
    expect(new ApiError(500, null, "kaboom").message).toBe("kaboom");
  });
});

describe("allauthErrorsByField", () => {
  it("returns {} for anything that isn't an ApiError", () => {
    expect(allauthErrorsByField(new Error("x"))).toEqual({});
    expect(allauthErrorsByField(undefined)).toEqual({});
  });

  it("maps param'd errors by field and bins param-less ones under __non_field__", () => {
    const e = new ApiError(400, {
      errors: [
        { code: "required", param: "email", message: "Email is required" },
        { code: "invalid", message: "Bad request" },
      ],
    });
    expect(allauthErrorsByField(e)).toEqual({
      email: "Email is required",
      __non_field__: "Bad request",
    });
  });

  it("lets the last message win when a field repeats", () => {
    const e = new ApiError(400, {
      errors: [
        { code: "a", param: "pw", message: "first" },
        { code: "b", param: "pw", message: "second" },
      ],
    });
    expect(allauthErrorsByField(e).pw).toBe("second");
  });
});

describe("api()", () => {
  it("sets a JSON content-type when a body is present and parses a JSON response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ json: { ok: true } }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "" });

    const out = await api("/x", { method: "POST", body: JSON.stringify({ a: 1 }) });

    expect(out).toEqual({ ok: true });
    const init = fetchMock.mock.calls[0][1];
    expect((init.headers as Headers).get("Content-Type")).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
  });

  it("attaches the CSRF token on unsafe methods only", async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ json: {} }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });

    await api("/x", { method: "DELETE" });
    expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-CSRFToken")).toBe("tok123");

    fetchMock.mockClear();
    await api("/x"); // GET — safe, no token
    expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-CSRFToken")).toBeNull();
  });

  it("returns text when the response isn't JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeResponse({ text: "hello", contentType: "text/plain" })),
    );
    vi.stubGlobal("document", { cookie: "" });
    expect(await api("/x")).toBe("hello");
  });

  it("throws an ApiError carrying status and parsed body on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeResponse({ status: 400, json: { errors: [] } })),
    );
    vi.stubGlobal("document", { cookie: "" });
    await expect(api("/x")).rejects.toMatchObject({ status: 400, data: { errors: [] } });
  });
});
