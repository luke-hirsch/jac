import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  nativeIntroQuery,
  nativeMetaQuery,
  nativePortfolioQuery,
  portfolioLinkQuery,
  portfolioRankQuery,
  publicPaths,
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
    expect(publicPaths.rank("jane")).toBe("/api/spa/public/jane/native/rank/");
    expect(publicPaths.prewarm("jane")).toBe(
      "/api/spa/public/jane/native/prewarm/",
    );
    expect(publicPaths.intro("jane")).toBe(
      "/api/spa/public/jane/native/intro/",
    );
  });

  it("an intro job needs no handle: its id is enough", () => {
    expect(publicPaths.introJob("0b1c")).toBe("/api/spa/public/intro/0b1c/");
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
    ["rank", (h: string) => portfolioRankQuery(h, search).queryKey],
    ["intro", (h: string) => nativeIntroQuery(h, search).queryKey],
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

  it("the rank posts to its owner", async () => {
    await run(portfolioRankQuery("jane", { q: "jazz", d: ["music"] }));
    const [[url, init]] = fetchMock.mock.calls;
    expect(url).toBe("/api/spa/public/jane/native/rank/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      query: "jazz",
      domains: ["music"],
    });
  });

  it("the intro is queued at its owner and polled by job id", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ id: "0b1c" }))
      .mockResolvedValueOnce(json({ status: "done", intro: "Hi" }));
    await expect(run(nativeIntroQuery("jane", { q: "jazz" }))).resolves.toEqual(
      {
        intro: "Hi",
      },
    );
    expect(urls()).toEqual([
      "/api/spa/public/jane/native/intro/",
      "/api/spa/public/intro/0b1c/",
    ]);
  });
});
