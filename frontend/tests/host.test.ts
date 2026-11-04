import { afterEach, describe, expect, it, vi } from "vitest";
import { apexOrigin, baseDomain, parseHost } from "@/lib/host";

// Host-aware routing (portfolio-multiuser guide 3). Pure parsing against a fixed base
// domain — the tests/ regime's sweet spot (node env, no jsdom). Red until src/lib/host.ts.

const BASE = "luke-hirsch.de";

describe("parseHost", () => {
  it("the apex and www resolve to apex", () => {
    expect(parseHost("luke-hirsch.de", BASE)).toEqual({ kind: "apex" });
    expect(parseHost("www.luke-hirsch.de", BASE)).toEqual({ kind: "apex" });
  });

  it("the app subdomain is the authed host", () => {
    expect(parseHost("app.luke-hirsch.de", BASE)).toEqual({ kind: "app" });
  });

  it("a plain subdomain is that user's handle", () => {
    expect(parseHost("jane.luke-hirsch.de", BASE)).toEqual({
      kind: "handle",
      handle: "jane",
    });
  });

  it("is case-insensitive", () => {
    expect(parseHost("JANE.luke-hirsch.de", BASE)).toEqual({
      kind: "handle",
      handle: "jane",
    });
  });

  it("strips a trailing dot (FQDN form)", () => {
    expect(parseHost("jane.luke-hirsch.de.", BASE)).toEqual({
      kind: "handle",
      handle: "jane",
    });
  });

  it("nested subdomains fall back to apex (never a handle)", () => {
    expect(parseHost("a.b.luke-hirsch.de", BASE)).toEqual({ kind: "apex" });
  });

  it("a foreign host falls back to apex", () => {
    expect(parseHost("evil.com", BASE)).toEqual({ kind: "apex" });
  });

  it("works against the localhost dev base", () => {
    expect(parseHost("jane.localhost", "localhost")).toEqual({
      kind: "handle",
      handle: "jane",
    });
    expect(parseHost("app.localhost", "localhost")).toEqual({ kind: "app" });
    expect(parseHost("localhost", "localhost")).toEqual({ kind: "apex" });
  });
});

describe("baseDomain", () => {
  afterEach(() => vi.unstubAllGlobals());

  const page = (content: string | null) => ({
    querySelector: () =>
      content === null ? null : { getAttribute: () => content },
  });

  it("reads the domain nginx filled in, so one image serves any domain", () => {
    vi.stubGlobal("document", page("example.org"));
    expect(baseDomain()).toBe("example.org");
    expect(parseHost("app.example.org")).toEqual({ kind: "app" });
  });

  it("falls back to localhost in vite dev, where the placeholder is still there", () => {
    vi.stubGlobal("document", page("__BASE_DOMAIN__"));
    expect(baseDomain()).toBe("localhost");
  });

  it("copes with no document at all", () => {
    expect(baseDomain()).toBe("localhost");
  });
});

describe("apexOrigin", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is the bare domain, keeping scheme and port", () => {
    vi.stubGlobal("window", { location: { protocol: "https:", port: "" } });
    expect(apexOrigin(BASE)).toBe("https://luke-hirsch.de");

    vi.stubGlobal("window", { location: { protocol: "http:", port: "5173" } });
    expect(apexOrigin("localhost")).toBe("http://localhost:5173");
  });
});
