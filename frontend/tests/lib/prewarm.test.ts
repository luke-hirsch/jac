import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Asking for the model server ahead of a request: fire and forget, and typing doesn't post on every
 * keystroke. The server holds the real limits; a visitor's knock carries the solved check (guide
 * 30). A fresh module per test, for a fresh quiet window.
 */

const noContent = () =>
  ({
    ok: true,
    status: 204,
    headers: { get: () => null },
    text: async () => "",
  }) as unknown as Response;

async function load() {
  vi.resetModules();
  return import("@/lib/prewarm");
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock = vi.fn(async () => noContent());
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "" });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("prewarm", () => {
  it("posts to the app's endpoint when someone is about to edit", async () => {
    const { prewarmForEditing } = await load();
    prewarmForEditing();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/llm/prewarm/");
    expect(init.method).toBe("POST");
  });

  it("posts to the owner's public endpoint for a visitor", async () => {
    const { prewarmForVisitor } = await load();
    prewarmForVisitor("jane", "cGF5bG9hZA==");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/spa/public/jane/native/prewarm/",
    );
  });

  it("a visitor's knock carries the solved check: no wake without one (spa/captcha.py)", async () => {
    const { prewarmForVisitor } = await load();
    prewarmForVisitor("jane", "cGF5bG9hZA==");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ captcha: "cGF5bG9hZA==" });
  });

  it("the editor's knock needs no check: a signed-in user's own request", async () => {
    const { prewarmForEditing } = await load();
    prewarmForEditing();
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
  });

  it("keeps a quiet window per owner: one visitor's knock doesn't silence another portfolio", async () => {
    const { prewarmForVisitor } = await load();
    prewarmForVisitor("jane", "one");
    prewarmForVisitor("jane", "two");
    prewarmForVisitor("lukas", "three");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/spa/public/jane/native/prewarm/",
      "/api/spa/public/lukas/native/prewarm/",
    ]);
  });

  it("doesn't post on every keystroke", async () => {
    const { prewarmForEditing } = await load();
    for (let i = 0; i < 5; i++) prewarmForEditing();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("asks again once the quiet window is over", async () => {
    const { prewarmForEditing } = await load();
    prewarmForEditing();
    vi.advanceTimersByTime(5 * 60 * 1000);
    prewarmForEditing();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps a failure to itself", async () => {
    fetchMock.mockRejectedValue(new Error("offline"));
    const { prewarmForVisitor } = await load();
    expect(() => prewarmForVisitor("jane", "cGF5bG9hZA==")).not.toThrow();
    await vi.runAllTimersAsync(); // an unhandled rejection would fail the run here
  });
});
