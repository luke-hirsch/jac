import { describe, expect, it } from "vitest";
import { parseHost } from "@/lib/host";

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
