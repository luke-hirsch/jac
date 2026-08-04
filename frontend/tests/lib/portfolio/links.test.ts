import { describe, expect, it } from "vitest";
import {
  anchorId,
  linkTarget,
  outboundUrl,
  pageAnchors,
} from "@/lib/portfolio/content";
import type { PortfolioItem } from "@/lib/queries/portfolio";

/**
 * The link algebra behind a block's nested `links` ([fullstack]-block-links, hyperlink
 * follow-up): where a nested item's title points. Node-env pure-lib only — the rendering
 * (item-card's <a>, the smooth scroll) is click-through verified ([[frontend-test-layout]]).
 */

function item(id: string, extra: Partial<PortfolioItem> = {}): PortfolioItem {
  const [type] = id.split(":");
  return {
    id,
    type: type as PortfolioItem["type"],
    title: id,
    domains: [],
    ...extra,
  };
}

describe("anchorId", () => {
  it("turns a type:pk id into a fragment-safe dom id", () => {
    expect(anchorId("job:12")).toBe("item-job-12");
    expect(anchorId("block:7")).toBe("item-block-7");
  });
});

describe("pageAnchors", () => {
  it("collects the ids of both rendered lists", () => {
    const anchors = pageAnchors([item("block:1")], [item("job:2")]);
    expect([...anchors].sort()).toEqual(["block:1", "job:2"]);
  });

  it("ignores nested links — they are not standalone cards", () => {
    const block = item("block:1", { links: [item("job:2")] });
    expect(pageAnchors([block], [])).toEqual(new Set(["block:1"]));
  });
});

describe("linkTarget", () => {
  it("prefers the on-page anchor when the item also has its own card", () => {
    const job = item("job:2", { url: "https://acme.example" });
    expect(linkTarget(job, pageAnchors([], [job]))).toEqual({
      kind: "anchor",
      href: "#item-job-2",
    });
  });

  it("falls back to the item url when it renders nowhere else", () => {
    const project = item("project:5", { url: "https://proj.example" });
    expect(linkTarget(project, new Set())).toEqual({
      kind: "external",
      href: "https://proj.example",
    });
  });

  it("is null when there is neither a card nor a url", () => {
    expect(linkTarget(item("skill:9"), new Set())).toBeNull();
    expect(linkTarget(item("block:3"), new Set())).toBeNull();
  });
});

describe("outboundUrl", () => {
  it("offers the url alongside an anchor (the ↗ next to an on-page jump)", () => {
    const job = item("job:2", { url: "https://acme.example" });
    const target = linkTarget(job, new Set(["job:2"]));
    expect(outboundUrl(job, target)).toBe("https://acme.example");
  });

  it("is empty when the title link is already the url, or there is no url", () => {
    const project = item("project:5", { url: "https://proj.example" });
    expect(outboundUrl(project, linkTarget(project, new Set()))).toBe("");
    const skill = item("skill:9");
    expect(outboundUrl(skill, linkTarget(skill, new Set(["skill:9"])))).toBe("");
  });
});
