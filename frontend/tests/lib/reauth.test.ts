import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { withReauth } from "@/lib/reauth";

/**
 * `withReauth` runs fn; on a 401 that lists a `reauthenticate` flow it prompts
 * for a password, POSTs it, and retries fn exactly once. Everything else
 * propagates. `window.prompt` / `document.cookie` / `fetch` are stubbed since
 * the node environment has no DOM.
 */
const reauthError = () => new ApiError(401, { data: { flows: [{ id: "reauthenticate" }] } });

afterEach(() => vi.unstubAllGlobals());

describe("withReauth", () => {
  it("returns the result without prompting when fn succeeds", async () => {
    const prompt = vi.fn();
    vi.stubGlobal("window", { prompt });
    const fn = vi.fn().mockResolvedValue("ok");

    expect(await withReauth(fn)).toBe("ok");
    expect(prompt).not.toHaveBeenCalled();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("rethrows anything that isn't a reauth-able 401", async () => {
    await expect(withReauth(vi.fn().mockRejectedValue(new Error("boom")))).rejects.toThrow("boom");

    const noFlow = new ApiError(401, { data: { flows: [{ id: "login" }] } });
    await expect(withReauth(vi.fn().mockRejectedValue(noFlow))).rejects.toBe(noFlow);
  });

  it("aborts (without retrying) when the user dismisses the prompt", async () => {
    vi.stubGlobal("window", { prompt: vi.fn().mockReturnValue(null) });
    const fn = vi.fn().mockRejectedValue(reauthError());

    await expect(withReauth(fn)).rejects.toThrow(/cancelled/i);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("reauthenticates and retries fn once a password is supplied", async () => {
    vi.stubGlobal("window", { prompt: vi.fn().mockReturnValue("hunter2") });
    vi.stubGlobal("document", { cookie: "csrftoken=tok" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({}),
      text: async () => "",
    });
    vi.stubGlobal("fetch", fetchMock);

    const fn = vi.fn().mockRejectedValueOnce(reauthError()).mockResolvedValueOnce("done");

    expect(await withReauth(fn)).toBe("done");
    expect(fn).toHaveBeenCalledTimes(2);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/reauthenticate");
    expect(JSON.parse(init.body as string)).toEqual({ password: "hunter2" });
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("tok");
  });
});
