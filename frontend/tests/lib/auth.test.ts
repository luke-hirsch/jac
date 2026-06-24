import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSession, signOut } from "@/lib/auth";

/**
 * The two non-hook auth helpers encode allauth's surprising contract: a 401/410
 * on the session endpoint *is* the (anonymous) session payload, and a 401 on
 * DELETE means the sign-out succeeded. The `useAuth` hook itself isn't covered
 * here — it needs @testing-library/react, which isn't installed.
 */

function fakeResponse(status: number, json: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => json,
    text: async () => "",
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchSession", () => {
  it("returns the session body on success", async () => {
    const body = { status: 200, meta: { is_authenticated: true } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse(200, body)));
    expect(await fetchSession()).toEqual(body);
  });

  it("unwraps a 401/410 body as the anonymous session instead of throwing", async () => {
    const body = { status: 401, meta: { is_authenticated: false }, data: { flows: [] } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse(401, body)));
    expect(await fetchSession()).toEqual(body);

    const gone = { status: 410, meta: { is_authenticated: false } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse(410, gone)));
    expect(await fetchSession()).toEqual(gone);
  });

  it("rethrows unexpected statuses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse(500, {})));
    await expect(fetchSession()).rejects.toMatchObject({ status: 500 });
  });
});

describe("signOut", () => {
  it("resolves on the 401 allauth returns for a successful sign-out", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse(401, {})));
    vi.stubGlobal("document", { cookie: "csrftoken=t" });
    await expect(signOut()).resolves.toBeUndefined();
  });

  it("rethrows a non-401 failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse(500, {})));
    vi.stubGlobal("document", { cookie: "csrftoken=t" });
    await expect(signOut()).rejects.toMatchObject({ status: 500 });
  });
});
