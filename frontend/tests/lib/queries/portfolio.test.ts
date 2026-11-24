import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PERSONALISE_POLL_MS,
  nativeMetaQuery,
  nativePortfolioQuery,
  personaliseJobQuery,
  personaliseStartQuery,
  portfolioLinkQuery,
  publicPaths,
  type PersonaliseStatus,
} from "@/lib/queries/portfolio";

/**
 * Every portfolio shares one host, so every public call names its owner, and so does every query
 * key: two owners in one tab must never share a cache entry.
 */

const json = (body: unknown) =>
  ({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => body,
  }) as unknown as Response;

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => json({}));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "" });
});

afterEach(() => vi.unstubAllGlobals());

const run = (q: { queryFn?: unknown }) =>
  (q.queryFn as () => Promise<unknown>)();
const urls = () => fetchMock.mock.calls.map(([url]) => url);

describe("publicPaths", () => {
  it("names the owner in every per-owner endpoint", () => {
    expect(publicPaths.link("jane", "acme")).toBe(
      "/api/spa/public/jane/links/acme/",
    );
    expect(publicPaths.native("jane")).toBe("/api/spa/public/jane/native/");
    expect(publicPaths.native("jane", "lucky=1")).toBe(
      "/api/spa/public/jane/native/?lucky=1",
    );
    expect(publicPaths.meta("jane")).toBe("/api/spa/public/jane/native/meta/");
    expect(publicPaths.prewarm("jane")).toBe(
      "/api/spa/public/jane/native/prewarm/",
    );
    expect(publicPaths.personalise("jane")).toBe(
      "/api/spa/public/jane/native/personalise/",
    );
  });

  it("a personalise job needs no handle: its id is enough", () => {
    expect(publicPaths.personaliseJob("0b1c")).toBe(
      "/api/spa/public/personalise/0b1c/",
    );
  });

  it("encodes what a visitor typed into the address bar", () => {
    expect(publicPaths.link("a/b", "c?d")).toBe(
      "/api/spa/public/a%2Fb/links/c%3Fd/",
    );
  });
});

describe("query keys", () => {
  const search = {
    d: ["music"],
    focus: "technical",
    tone: "formal",
    q: "jazz",
  };

  it.each([
    ["link", (h: string) => portfolioLinkQuery(h, "acme").queryKey],
    ["native", (h: string) => nativePortfolioQuery(h, search).queryKey],
    ["meta", (h: string) => nativeMetaQuery(h).queryKey],
    ["personalise", (h: string) => personaliseStartQuery(h, search).queryKey],
  ] as const)("%s: two owners never share a cache entry", (_, key) => {
    expect(key("jane")).toContain("jane");
    expect(key("jane")).not.toEqual(key("lukas"));
    // Under "public", so a handle can't prefix-match the owner-side ["portfolio", "links"].
    expect(key("links").slice(0, 3)).toEqual(["portfolio", "public", "links"]);
  });
});

describe("what the queries fetch", () => {
  it("a link, from its owner", async () => {
    await run(portfolioLinkQuery("jane", "acme"));
    expect(urls()).toEqual(["/api/spa/public/jane/links/acme/"]);
  });

  it("the native page and its meta, from their owner", async () => {
    await run(nativePortfolioQuery("jane", { d: ["music"], lucky: true }));
    await run(nativeMetaQuery("jane"));
    expect(urls()).toEqual([
      "/api/spa/public/jane/native/?domains=music&lucky=1",
      "/api/spa/public/jane/native/meta/",
    ]);
  });

  it("a visit's AI job is queued at its owner, with everything it needs", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: "0b1c" }));
    const started = await run(
      personaliseStartQuery("jane", {
        q: "jazz",
        d: ["music"],
        focus: "technical",
        tone: "formal",
      }),
    );
    const [[url, init]] = fetchMock.mock.calls;
    expect(url).toBe("/api/spa/public/jane/native/personalise/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      query: "jazz",
      domains: ["music"],
      focus: "technical",
      tone: "formal",
    });
    expect(started).toMatchObject({ id: "0b1c" });
    expect(typeof (started as { startedAt: unknown }).startedAt).toBe("number");
  });

  it("a lucky draw queues nothing: it uses no AI", () => {
    expect(personaliseStartQuery("jane", { lucky: true }).enabled).toBe(false);
    expect(personaliseStartQuery("jane", { q: "jazz" }).enabled).toBe(true);
  });

  it("the job is read by its id alone", async () => {
    await run(personaliseJobQuery("0b1c"));
    expect(urls()).toEqual(["/api/spa/public/personalise/0b1c/"]);
    expect(personaliseJobQuery(undefined).enabled).toBe(false);
  });

  it("polls until the job is done or failed, then stops", () => {
    const interval = personaliseJobQuery("0b1c").refetchInterval as (q: {
      state: { data: PersonaliseStatus | undefined };
    }) => number | false;
    const at = (status?: PersonaliseStatus["status"]) =>
      interval({
        state: {
          data: status && { status, waking: false, intro: "" },
        },
      });
    expect(at()).toBe(PERSONALISE_POLL_MS);
    expect(at("pending")).toBe(PERSONALISE_POLL_MS);
    expect(at("running")).toBe(PERSONALISE_POLL_MS);
    expect(at("done")).toBe(false);
    expect(at("failed")).toBe(false);
  });
});
