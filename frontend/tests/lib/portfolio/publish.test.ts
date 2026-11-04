import { describe, expect, it } from "vitest";
import {
  isLive,
  portfolioAddress,
  publishNotice,
  type PublishState,
} from "@/lib/portfolio/publish";

const state = (over: Partial<PublishState> = {}): PublishState => ({
  handle: "jane",
  portfolio_public: false,
  portfolio_always_public: false,
  ...over,
});

describe("portfolioAddress", () => {
  it("is the handle under the base domain", () => {
    expect(portfolioAddress("jane", "example.com")).toBe("jane.example.com");
  });
});

describe("isLive", () => {
  it("is off for a new signup", () => {
    expect(isLive(state())).toBe(false);
  });

  it("is on once published", () => {
    expect(isLive(state({ portfolio_public: true }))).toBe(true);
  });

  it("is on for the site owner, whatever the field says", () => {
    expect(isLive(state({ portfolio_always_public: true }))).toBe(true);
  });
});

describe("publishNotice", () => {
  it("states the unpublished state plainly, and that shared links still work", () => {
    const text = publishNotice(state(), "example.com");
    expect(text).toContain("Not published");
    expect(text).toContain("jane.example.com");
    expect(text).toContain("A link you share");
  });

  it("says who can read a published portfolio", () => {
    const text = publishNotice(
      state({ portfolio_public: true }),
      "example.com",
    );
    expect(text).toContain("Published");
    expect(text).toContain("anyone");
    expect(text).toContain("jane.example.com");
  });

  it("doesn't call the site owner's portfolio unpublished", () => {
    const text = publishNotice(
      state({ portfolio_always_public: true }),
      "example.com",
    );
    expect(text).toContain("always public");
    expect(text).not.toContain("Not published");
  });
});
