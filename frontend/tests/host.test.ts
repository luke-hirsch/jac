import { afterEach, describe, expect, it, vi } from "vitest";
import {
  apexOrigin,
  baseDomain,
  jacOrigin,
  leaveTo,
  parseHost,
  portfolioOrigin,
} from "@/lib/host";

// Three fixed hosts: the apex, jac. and portfolio. Owners are paths on the portfolio host now,
// never subdomains. Pure parsing against a fixed base domain (node env, no jsdom).

const BASE = "luke-hirsch.de";

describe("parseHost", () => {
  it("the apex and www resolve to apex", () => {
    expect(parseHost("luke-hirsch.de", BASE)).toBe("apex");
    expect(parseHost("www.luke-hirsch.de", BASE)).toBe("apex");
  });

  it("jac. is the authed app", () => {
    expect(parseHost("jac.luke-hirsch.de", BASE)).toBe("jac");
  });

  it("portfolio. is every owner's portfolio", () => {
    expect(parseHost("portfolio.luke-hirsch.de", BASE)).toBe("portfolio");
  });

  it("is case-insensitive and strips a trailing dot (FQDN form)", () => {
    expect(parseHost("JAC.luke-hirsch.de", BASE)).toBe("jac");
    expect(parseHost("portfolio.luke-hirsch.de.", BASE)).toBe("portfolio");
  });

  it("the old app. host is nothing special any more", () => {
    expect(parseHost("app.luke-hirsch.de", BASE)).toBe("apex");
  });

  it("a would-be handle subdomain is no portfolio: it falls back to apex", () => {
    expect(parseHost("jane.luke-hirsch.de", BASE)).toBe("apex");
    expect(parseHost("a.portfolio.luke-hirsch.de", BASE)).toBe("apex");
  });

  it("a foreign host falls back to apex", () => {
    expect(parseHost("evil.com", BASE)).toBe("apex");
    expect(parseHost("jac.evil.com", BASE)).toBe("apex");
  });

  it("works against the localhost dev base", () => {
    expect(parseHost("localhost", "localhost")).toBe("apex");
    expect(parseHost("jac.localhost", "localhost")).toBe("jac");
    expect(parseHost("portfolio.localhost", "localhost")).toBe("portfolio");
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
    expect(parseHost("jac.example.org")).toBe("jac");
    expect(parseHost("portfolio.example.org")).toBe("portfolio");
  });

  it("falls back to localhost in vite dev, where the placeholder is still there", () => {
    vi.stubGlobal("document", page("__BASE_DOMAIN__"));
    expect(baseDomain()).toBe("localhost");
  });

  it("copes with no document at all", () => {
    expect(baseDomain()).toBe("localhost");
  });
});

describe("origins", () => {
  afterEach(() => vi.unstubAllGlobals());

  const at = (protocol: string, port: string) =>
    vi.stubGlobal("window", { location: { protocol, port } });

  it("the apex is the bare domain, keeping scheme and port", () => {
    at("https:", "");
    expect(apexOrigin(BASE)).toBe("https://luke-hirsch.de");
    at("http:", "5173");
    expect(apexOrigin("localhost")).toBe("http://localhost:5173");
  });

  it("the app is on jac., keeping scheme and port", () => {
    at("https:", "");
    expect(jacOrigin(BASE)).toBe("https://jac.luke-hirsch.de");
    at("http:", "5173");
    expect(jacOrigin("localhost")).toBe("http://jac.localhost:5173");
  });

  it("portfolios are on portfolio., keeping scheme and port", () => {
    at("https:", "");
    expect(portfolioOrigin(BASE)).toBe("https://portfolio.luke-hirsch.de");
    at("http:", "5173");
    expect(portfolioOrigin("localhost")).toBe(
      "http://portfolio.localhost:5173",
    );
  });
});

describe("leaveTo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("replaces the location and never settles, so no second guard runs", async () => {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { replace } });
    let settled = false;
    leaveTo("https://jac.luke-hirsch.de/cv").finally(() => (settled = true));
    await new Promise((r) => setTimeout(r, 0));
    expect(replace).toHaveBeenCalledExactlyOnceWith(
      "https://jac.luke-hirsch.de/cv",
    );
    expect(settled).toBe(false);
  });
});
