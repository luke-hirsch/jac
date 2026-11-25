import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { PortfolioItem } from "@/lib/queries/portfolio";

/**
 * Guide 29, the Studio look: what of the public page is logic. The owner's accent made readable
 * on the dark ground, the page split into the mock-up's sections (the "now" card, the tiles, the
 * numbered highlights, the closing line), and the small lines the cards say.
 *
 * Lazy import, as in appearance.test.ts: the file stays collectible while `@/lib/portfolio/studio`
 * doesn't exist yet. Hoist it to a top-level import once it does; `Mod` is the guide's contract.
 */

type Sections = {
  now: PortfolioItem | null;
  figures: PortfolioItem | null;
  highlights: PortfolioItem[];
  more: PortfolioItem[];
  closing: { item: PortfolioItem; repeat: boolean } | null;
};
type Mod = {
  STUDIO_GROUND: string;
  STUDIO_CARD: string;
  STUDIO_ACCENT: string;
  MIN_ACCENT_CONTRAST: number;
  contrastBetween(a: string, b: string): number;
  studioAccent(raw: string | null | undefined): string;
  studioSections(featured: PortfolioItem[], more: PortfolioItem[]): Sections;
  ordinal(n: number): string;
  yearSpan(item: PortfolioItem): string;
  metaLine(item: PortfolioItem): string;
  quoted(body: string): string;
  safeHref(url: string | null | undefined): string | null;
  hostLabel(url: string): string;
  addressLabel(url: string): string;
  scrollMotion(): ScrollBehavior;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/portfolio/studio")) as unknown as Mod;
});

const item = (
  over: Partial<PortfolioItem> & { id: string },
): PortfolioItem => ({
  type: over.id.split(":")[0] as PortfolioItem["type"],
  title: over.id,
  domains: [],
  ...over,
});
const block = (id: string, kind: PortfolioItem["kind"], over = {}) =>
  item({ id, kind, ...over });
const cta = (id: string, url = "mailto:me@example.com") =>
  block(id, "cta", { data: { label: "Write to me", url } });
const figures = (id: string, rows = 2) =>
  block(id, "figures", {
    data: {
      figures: Array.from({ length: rows }, (_, i) => ({
        value: String(i + 1),
        label: `figure ${i + 1}`,
      })),
    },
  });
const ids = (items: PortfolioItem[]) => items.map((i) => i.id);

describe("contrastBetween", () => {
  it("anchors on the extremes, either way round", () => {
    expect(m.contrastBetween("#ffffff", "#000000")).toBeCloseTo(21, 1);
    expect(m.contrastBetween("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(m.contrastBetween("#777777", "#777777")).toBeCloseTo(1, 5);
  });
});

describe("studioAccent", () => {
  it("falls back to the Studio's apricot when the owner picked none", () => {
    expect(m.STUDIO_ACCENT).toBe("#f2a65a");
    expect(m.studioAccent("")).toBe(m.STUDIO_ACCENT);
    expect(m.studioAccent(undefined)).toBe(m.STUDIO_ACCENT);
    expect(m.studioAccent(null)).toBe(m.STUDIO_ACCENT);
    expect(m.studioAccent("not a colour")).toBe(m.STUDIO_ACCENT);
  });

  it("keeps an accent that already reads on the dark ground exactly as picked", () => {
    expect(m.studioAccent("#F2A65A")).toBe("#f2a65a");
    expect(m.studioAccent("#7fb6f0")).toBe("#7fb6f0");
  });

  it("lightens a CV accent picked for white paper until it clears AA on the cards", () => {
    const blue = "#1a5fb4"; // the CV layout's default: ~3:1 on the dark cards
    expect(m.contrastBetween(blue, m.STUDIO_CARD)).toBeLessThan(
      m.MIN_ACCENT_CONTRAST,
    );
    const out = m.studioAccent(blue);
    expect(m.contrastBetween(out, m.STUDIO_CARD)).toBeGreaterThanOrEqual(
      m.MIN_ACCENT_CONTRAST,
    );
    // The ground is darker than the cards, and it's also the ink on an accent button.
    expect(m.contrastBetween(out, m.STUDIO_GROUND)).toBeGreaterThanOrEqual(
      m.MIN_ACCENT_CONTRAST,
    );
  });

  it("keeps the hue: a lightened blue is still blue", () => {
    const [r, g, b] = [1, 3, 5].map((i) =>
      parseInt(m.studioAccent("#1a5fb4").slice(i, i + 2), 16),
    );
    expect(b).toBeGreaterThan(r);
    expect(b).toBeGreaterThan(g);
  });

  it("terminates on black, and the result reads", () => {
    const out = m.studioAccent("#000000");
    expect(out).toMatch(/^#[0-9a-f]{6}$/);
    expect(m.contrastBetween(out, m.STUDIO_CARD)).toBeGreaterThanOrEqual(
      m.MIN_ACCENT_CONTRAST,
    );
  });

  it("takes the short form a colour field can produce", () => {
    expect(m.studioAccent("#fa5")).toBe(m.studioAccent("#ffaa55"));
  });
});

describe("studioSections", () => {
  it("takes the skills out of the grids: they live in the network", () => {
    const s = m.studioSections(
      [item({ id: "job:1" }), item({ id: "skill:1" })],
      [item({ id: "skill:2" }), item({ id: "project:1" })],
    );
    expect(ids(s.highlights)).toEqual(["job:1"]);
    expect(ids(s.more)).toEqual(["project:1"]);
  });

  it("keeps the owner's order in both grids", () => {
    const s = m.studioSections(
      [
        item({ id: "project:2" }),
        block("block:1", "text"),
        item({ id: "job:1" }),
      ],
      [item({ id: "education:1" }), block("block:2", "quote")],
    );
    expect(ids(s.highlights)).toEqual(["project:2", "block:1", "job:1"]);
    expect(ids(s.more)).toEqual(["education:1", "block:2"]);
  });

  it("makes the page's first call to action the hero's now card, out of the grids", () => {
    const s = m.studioSections(
      [item({ id: "job:1" }), cta("block:1")],
      [cta("block:2")],
    );
    expect(s.now?.id).toBe("block:1");
    expect(ids(s.highlights)).toEqual(["job:1"]);
  });

  it("looks in the highlights first, then the rest", () => {
    const s = m.studioSections([item({ id: "job:1" })], [cta("block:9")]);
    expect(s.now?.id).toBe("block:9");
    expect(s.more).toEqual([]);
  });

  it("has no now card and no closing line without a call to action", () => {
    const s = m.studioSections([item({ id: "job:1" })], []);
    expect(s.now).toBeNull();
    expect(s.closing).toBeNull();
  });

  it("closes on the same call to action when there's only one, marked as a repeat", () => {
    const s = m.studioSections([cta("block:1")], []);
    expect(s.now?.id).toBe("block:1");
    expect(s.closing).toEqual({ item: s.now, repeat: true });
    expect(s.highlights).toEqual([]);
  });

  it("closes on the last of several; the ones between stay where they stand", () => {
    const s = m.studioSections(
      [cta("block:1"), cta("block:2")],
      [cta("block:3")],
    );
    expect(s.now?.id).toBe("block:1");
    expect(s.closing).toMatchObject({ repeat: false });
    expect(s.closing?.item.id).toBe("block:3");
    expect(ids(s.highlights)).toEqual(["block:2"]);
    expect(s.more).toEqual([]);
  });

  it("doesn't put a call to action it can't follow in the hero", () => {
    const broken = block("block:1", "cta", {
      data: { label: "Click", url: "javascript:alert(1)" },
    });
    const unlabelled = block("block:2", "cta", {
      data: { label: " ", url: "https://example.com" },
    });
    const s = m.studioSections([broken, unlabelled], []);
    expect(s.now).toBeNull();
    expect(ids(s.highlights)).toEqual(["block:1", "block:2"]);
  });

  it("puts the first key-figures block under the hero; a second one stays in its grid", () => {
    const s = m.studioSections(
      [item({ id: "job:1" }), figures("block:4")],
      [figures("block:5")],
    );
    expect(s.figures?.id).toBe("block:4");
    expect(ids(s.highlights)).toEqual(["job:1"]);
    expect(ids(s.more)).toEqual(["block:5"]);
  });

  it("doesn't make tiles of a figures block without rows", () => {
    const s = m.studioSections([figures("block:4", 0)], []);
    expect(s.figures).toBeNull();
    expect(ids(s.highlights)).toEqual(["block:4"]);
  });
});

describe("the cards' small lines", () => {
  it("numbers the highlights with two digits", () => {
    expect(m.ordinal(1)).toBe("01");
    expect(m.ordinal(12)).toBe("12");
  });

  it("writes an entry's years the way the mock-up does", () => {
    const at = (over: Partial<PortfolioItem>) =>
      m.yearSpan(item({ id: "job:1", ...over }));
    expect(at({ started: "2021-03-01", ended: null })).toBe("2021 – today");
    expect(at({ started: "2019-01-01", ended: "2021-06-30" })).toBe(
      "2019 – 2021",
    );
    expect(at({ started: "2023-02-01", ended: "2023-09-01" })).toBe("2023");
    expect(at({ started: null, ended: "2020-01-01" })).toBe("");
  });

  it("gives a certification its issue year, not a span that runs until today", () => {
    expect(
      m.yearSpan(item({ id: "certification:1", started: "2023-05-04" })),
    ).toBe("2023");
  });

  it("names the type and the years above a card's title; a block has no type to name", () => {
    expect(
      m.metaLine(item({ id: "job:1", started: "2021-01-01", ended: null })),
    ).toBe("Job · 2021 – today");
    expect(m.metaLine(item({ id: "language:1" }))).toBe("Language");
    expect(m.metaLine(block("block:1", "text"))).toBe("");
  });

  it("sets a quote in typographic marks, unless the owner typed some", () => {
    expect(m.quoted("  Calm under pressure. ")).toBe("“Calm under pressure.”");
    expect(m.quoted("“Already quoted.”")).toBe("“Already quoted.”");
    expect(m.quoted('"Straight quotes."')).toBe('"Straight quotes."');
    expect(m.quoted("„Deutsch.“")).toBe("„Deutsch.“");
  });
});

describe("links", () => {
  it("lets through only what a visitor can safely follow", () => {
    expect(m.safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(m.safeHref("http://example.com/")).toBe("http://example.com/");
    expect(m.safeHref("mailto:me@example.com")).toBe("mailto:me@example.com");
    expect(m.safeHref("javascript:alert(1)")).toBeNull();
    expect(m.safeHref("data:text/html,hi")).toBeNull();
    expect(m.safeHref("/relative")).toBeNull();
    expect(m.safeHref("")).toBeNull();
    expect(m.safeHref(undefined)).toBeNull();
  });

  it("labels a link-list row by where it goes", () => {
    expect(m.hostLabel("https://www.github.com/someone/repo")).toBe(
      "github.com",
    );
    expect(m.hostLabel("http://blog.example.com/post")).toBe(
      "blog.example.com",
    );
    expect(m.hostLabel("mailto:me@example.com")).toBe("me@example.com");
    expect(m.hostLabel("nonsense")).toBe("");
  });

  it("writes the closing line's address out in full, without the noise", () => {
    expect(m.addressLabel("mailto:me@example.com?subject=Hello")).toBe(
      "me@example.com",
    );
    expect(m.addressLabel("https://www.example.com/")).toBe("example.com");
    expect(m.addressLabel("https://cal.example.com/me/30min/")).toBe(
      "cal.example.com/me/30min",
    );
    expect(m.addressLabel("nonsense")).toBe("");
  });
});

describe("scrollMotion", () => {
  afterEach(() => vi.unstubAllGlobals());

  const prefers = (reduce: boolean) =>
    vi.stubGlobal(
      "matchMedia",
      vi.fn((q: string) => ({
        matches: reduce && q === "(prefers-reduced-motion: reduce)",
      })),
    );

  it("jumps instead of gliding when the visitor asked for less motion", () => {
    prefers(true);
    expect(m.scrollMotion()).toBe("auto");
  });

  it("glides otherwise", () => {
    prefers(false);
    expect(m.scrollMotion()).toBe("smooth");
  });

  it("glides where there's no matchMedia to ask", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(m.scrollMotion()).toBe("smooth");
  });
});
