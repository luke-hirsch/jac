import { beforeAll, describe, expect, it } from "vitest";
import { dropOrder } from "@/lib/render/fit";
import type { CvContent } from "@/lib/cv-doc";

/**
 * `[frontend]-fit-preflight`: the fit grows as well as shrinks. SKIP-MARKED — not the
 * active guide. **Step 0: delete every `.skip` in this file.**
 *
 * `pagesFor` is faked as a line count so the suite stays deterministic and fast — the real
 * react-pdf measurement is exercised by the render suites; what's under test here is the
 * search, not the renderer.
 *
 * Lazy import (see appearance.test.ts) so the file stays collectible while the new exports
 * don't exist yet.
 */

type Step = { kind: "demote" | "drop"; id: string };
type Result = {
  content: CvContent;
  demotedIds: string[];
  droppedIds: string[];
  addedIds: string[];
  pages: number;
  fits: boolean;
};
type Mod = {
  GROW_HEADROOM: number;
  reductionOrder(
    content: CvContent,
    detailed: Record<string, number>,
    isFavourite?: (id: string) => boolean,
  ): Step[];
  applySteps(content: CvContent, steps: Step[]): CvContent;
  reduceCv(
    content: CvContent,
    detailed: Record<string, number>,
    maxPages: number,
    pagesFor: (c: CvContent) => Promise<number>,
    isFavourite?: (id: string) => boolean,
  ): Promise<Result>;
  beyondCap(c: CvContent, caps: Record<string, number>, headroom?: number): CvContent;
  addOrder(pool: CvContent, isFavourite?: (id: string) => boolean): string[];
  applyAdd(content: CvContent, full: CvContent, ids: string[]): CvContent;
  growCv(
    content: CvContent,
    full: CvContent,
    pool: CvContent,
    maxPages: number,
    pagesFor: (c: CvContent) => Promise<number>,
    isFavourite?: (id: string) => boolean,
  ): Promise<{ content: CvContent; addedIds: string[]; pages: number }>;
  fitContent(
    full: CvContent,
    caps: Record<string, number>,
    detailed: Record<string, number>,
    maxPages: number,
    pagesFor: (c: CvContent) => Promise<number>,
    isFavourite?: (id: string) => boolean,
  ): Promise<Result>;
  preflightKey(args: {
    specVersion: string;
    content: CvContent;
    sectionsOff: string[];
    letterBody: string;
    letterMeta: unknown;
  }): string;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/render/fit")) as unknown as Mod;
});

const entry = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  label: id,
  relevance_score: null,
  ...extra,
});

/** 8 jobs, 6 skills — more than any cap below, so there is always a pool. */
function full(): CvContent {
  return {
    jobs: Array.from({ length: 8 }, (_, i) => entry(`job:${i + 1}`)),
    skills: Array.from({ length: 6 }, (_, i) => entry(`skill:${i + 1}`)),
  };
}

const count = (c: CvContent) =>
  Object.values(c).reduce((n, list) => n + list.length, 0);

/** Every entry costs one line; `perPage` lines fit on a page. */
const pagesBy = (perPage: number) => async (c: CvContent) =>
  Math.max(1, Math.ceil(count(c) / perPage));

describe.skip("beyondCap", () => {
  it("takes only what the cap cut, in rank order", () => {
    const pool = m.beyondCap(full(), { jobs: 3, skills: 2 }, 10);
    expect(pool.jobs.map((e) => e.id)).toEqual([
      "job:4",
      "job:5",
      "job:6",
      "job:7",
      "job:8",
    ]);
    expect(pool.skills.map((e) => e.id)).toEqual([
      "skill:3",
      "skill:4",
      "skill:5",
      "skill:6",
    ]);
  });

  it("stops at the headroom — page space is not the only constraint", () => {
    // cap 3, headroom 1.5 → at most 5 entries total, so 2 in the pool.
    const pool = m.beyondCap(full(), { jobs: 3 }, 1.5);
    expect(pool.jobs.map((e) => e.id)).toEqual(["job:4", "job:5"]);
  });

  it("ships a headroom above 1, or the grow pass could never do anything", () => {
    expect(m.GROW_HEADROOM).toBeGreaterThan(1);
  });

  it("ignores sections the layout does not cap", () => {
    expect(m.beyondCap(full(), { jobs: 3 })).not.toHaveProperty("skills");
  });
});

describe.skip("addOrder", () => {
  it("is the exact mirror of dropOrder", () => {
    const pool = { jobs: [entry("job:1"), entry("job:2"), entry("job:3")] };
    // dropOrder sheds the deepest first; addOrder takes the shallowest first.
    const dropped = dropOrder(pool);
    const added = m.addOrder(pool);
    expect(added[0]).toBe(dropped[dropped.length - 1]);
  });

  it("takes pins first, then favourites, then the best of the rest", () => {
    const pool = {
      jobs: [entry("job:1"), entry("job:2"), entry("job:3", { pinned: true })],
    };
    const order = m.addOrder(pool, (id) => id === "job:2");
    expect(order).toEqual(["job:3", "job:2", "job:1"]);
  });

  it("returns nothing for an empty pool", () => {
    expect(m.addOrder({})).toEqual([]);
  });
});

describe.skip("applyAdd", () => {
  it("re-inserts in the full content's rank order, not at the end", () => {
    const f = full();
    const kept: CvContent = { jobs: [f.jobs[0], f.jobs[1]], skills: [] };
    const out = m.applyAdd(kept, f, ["job:5"]);
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1", "job:2", "job:5"]);
  });

  it("leaves untouched sections and the input alone", () => {
    const f = full();
    const kept: CvContent = { jobs: [f.jobs[0]], skills: [f.skills[0]] };
    const out = m.applyAdd(kept, f, ["job:2"]);
    expect(out.skills.map((e) => e.id)).toEqual(["skill:1"]);
    expect(kept.jobs).toHaveLength(1); // immutable
  });
});

describe.skip("growCv", () => {
  it("does nothing with an empty pool", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const out = await m.growCv(kept, f, {}, 1, pagesBy(10));
    expect(out.addedIds).toEqual([]);
    expect(count(out.content)).toBe(2);
  });

  it("takes the whole pool when the whole pool fits", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const pool: CvContent = { jobs: f.jobs.slice(2, 5) };
    const out = await m.growCv(kept, f, pool, 1, pagesBy(100));
    expect(out.addedIds).toHaveLength(3);
    expect(count(out.content)).toBe(5);
  });

  it("stops exactly at the page boundary — one more would spill", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const pool: CvContent = { jobs: f.jobs.slice(2, 8) };
    // 5 entries per page, 1 page: 2 kept + at most 3 added.
    const out = await m.growCv(kept, f, pool, 1, pagesBy(5));
    expect(count(out.content)).toBe(5);
    expect(out.addedIds).toHaveLength(3);
    expect(out.pages).toBe(1);
  });

  it("reports the page count of the content it returns", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const pool: CvContent = { jobs: f.jobs.slice(2, 6) };
    const pagesFor = pagesBy(3);
    const out = await m.growCv(kept, f, pool, 2, pagesFor);
    expect(out.pages).toBe(await pagesFor(out.content));
  });
});

describe.skip("fitContent — cap, then down OR up, never both", () => {
  it("drops and never grows when the capped content overflows", async () => {
    const out = await m.fitContent(full(), { jobs: 8, skills: 6 }, {}, 1, pagesBy(5));
    expect(out.droppedIds.length).toBeGreaterThan(0);
    expect(out.addedIds).toEqual([]);
    expect(out.pages).toBeLessThanOrEqual(1);
  });

  it("grows and never drops when the capped content leaves room", async () => {
    const out = await m.fitContent(full(), { jobs: 2, skills: 1 }, {}, 1, pagesBy(10));
    expect(out.droppedIds).toEqual([]);
    expect(out.addedIds.length).toBeGreaterThan(0);
    expect(count(out.content)).toBeGreaterThan(3);
  });

  it("does neither when the cap already fills the page exactly", async () => {
    // caps total 5 entries, 5 per page → nothing to drop, no room to add.
    const out = await m.fitContent(full(), { jobs: 3, skills: 2 }, {}, 1, pagesBy(5));
    expect(out.droppedIds).toEqual([]);
    expect(out.addedIds).toEqual([]);
    expect(count(out.content)).toBe(5);
  });

  it("propagates an unfittable CV instead of pretending it grew", async () => {
    const out = await m.fitContent(full(), { jobs: 8, skills: 6 }, {}, 1, async () => 3);
    expect(out.fits).toBe(false);
    expect(out.addedIds).toEqual([]);
  });

  it("respects the grow headroom rather than filling the page with skills", async () => {
    const out = await m.fitContent(full(), { jobs: 1, skills: 1 }, {}, 2, pagesBy(100));
    // caps 1+1, headroom 1.5 → ceil(1.5)=1 slot each, so no pool at all.
    expect(out.addedIds).toEqual([]);
  });
});

describe.skip("preflightKey", () => {
  const base = {
    specVersion: "1:1:9:#1a5fb4",
    content: full(),
    sectionsOff: [] as string[],
    letterBody: "Dear team",
    letterMeta: { subject: "x" },
  };

  it("is stable for identical inputs", () => {
    expect(m.preflightKey(base)).toBe(m.preflightKey({ ...base, content: full() }));
  });

  it("changes when anything the render depends on changes", () => {
    const k = m.preflightKey(base);
    expect(m.preflightKey({ ...base, specVersion: "1:2:9:#1a5fb4" })).not.toBe(k);
    expect(m.preflightKey({ ...base, sectionsOff: ["skills"] })).not.toBe(k);
    expect(m.preflightKey({ ...base, letterBody: "Dear team!" })).not.toBe(k);
    expect(m.preflightKey({ ...base, letterMeta: { subject: "y" } })).not.toBe(k);
    expect(
      m.preflightKey({ ...base, content: { jobs: [entry("job:1")] } }),
    ).not.toBe(k);
  });
});

/**
 * The reduction ladder's middle gear: demote a job to its title before deleting it.
 *
 * The pager below mirrors the render rule (`entryDetail`): an explicit `detail` wins,
 * otherwise the first `detailed[section]` entries are full. A full entry costs `fullCost`
 * lines, a compact one costs 1 — without that asymmetry a demotion would be free and the
 * search would be untestable.
 */
const pagesByDetail =
  (perPage: number, detailed: Record<string, number>, fullCost = 3) =>
  async (c: CvContent) => {
    let lines = 0;
    for (const [section, list] of Object.entries(c)) {
      const budget = detailed[section] ?? 0;
      list.forEach((e, i) => {
        const isFull = e.detail ? e.detail === "full" : i < budget;
        lines += isFull ? fullCost : 1;
      });
    }
    return Math.max(1, Math.ceil(lines / perPage));
  };

/** 4 jobs, top 3 detailed → 3+3+3+1 = 10 lines at the baseline. */
const jobs4 = (): CvContent => ({
  jobs: Array.from({ length: 4 }, (_, i) => entry(`job:${i + 1}`)),
});
const DETAILED = { jobs: 3 };

describe.skip("reductionOrder", () => {
  it("offers every demotion before any drop", () => {
    const steps = m.reductionOrder(jobs4(), DETAILED);
    const firstDrop = steps.findIndex((s) => s.kind === "drop");
    const lastDemote = steps.map((s) => s.kind).lastIndexOf("demote");
    expect(lastDemote).toBeLessThan(firstDrop);
  });

  it("demotes deepest-first and never touches the top entry", () => {
    const demotes = m.reductionOrder(jobs4(), DETAILED)
      .filter((s) => s.kind === "demote")
      .map((s) => s.id);
    expect(demotes).toEqual(["job:3", "job:2"]);
    expect(demotes).not.toContain("job:1"); // MIN_DETAILED
  });

  it("offers no demotion for entries that are already one-liners", () => {
    // job:4 sits beyond the `detailed` budget, so it renders compact already.
    const demotes = m.reductionOrder(jobs4(), DETAILED).filter((s) => s.kind === "demote");
    expect(demotes.map((s) => s.id)).not.toContain("job:4");
  });

  it("skips an entry the user already set to compact", () => {
    const content: CvContent = {
      jobs: [entry("job:1"), entry("job:2", { detail: "compact" }), entry("job:3")],
    };
    const demotes = m.reductionOrder(content, DETAILED).filter((s) => s.kind === "demote");
    expect(demotes.map((s) => s.id)).toEqual(["job:3"]);
  });

  it("contributes only drops for a section with no detail budget", () => {
    const steps = m.reductionOrder({ skills: full().skills }, DETAILED);
    expect(steps.every((s) => s.kind === "drop")).toBe(true);
  });
});

describe.skip("applySteps", () => {
  it("marks demoted entries compact in place and removes dropped ones", () => {
    const out = m.applySteps(jobs4(), [
      { kind: "demote", id: "job:2" },
      { kind: "drop", id: "job:4" },
    ]);
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1", "job:2", "job:3"]);
    expect(out.jobs[1].detail).toBe("compact");
    expect(out.jobs[0].detail).toBeUndefined(); // untouched, rank still decides
  });

  it("keeps a demotion explicit so a later drop cannot promote it back", () => {
    // Dropping job:4 shortens the list to 3, which the `detailed: 3` budget would
    // otherwise render entirely in full — the stored flag is what prevents that.
    const out = m.applySteps(jobs4(), [
      { kind: "demote", id: "job:3" },
      { kind: "drop", id: "job:4" },
    ]);
    expect(out.jobs[2].detail).toBe("compact");
  });

  it("does not mutate the input", () => {
    const input = jobs4();
    m.applySteps(input, [{ kind: "demote", id: "job:2" }]);
    expect(input.jobs[1].detail).toBeUndefined();
  });

  it("ignores a step naming an id that is not there", () => {
    const out = m.applySteps(jobs4(), [{ kind: "drop", id: "job:99" }]);
    expect(out.jobs).toHaveLength(4);
  });
});

describe.skip("reduceCv — demote before drop", () => {
  it("does nothing when it already fits", async () => {
    const out = await m.reduceCv(jobs4(), DETAILED, 1, pagesByDetail(10, DETAILED));
    expect(out.demotedIds).toEqual([]);
    expect(out.droppedIds).toEqual([]);
    expect(out.fits).toBe(true);
  });

  it("shortens a job rather than deleting one when that is enough", async () => {
    // 10 lines, 8 per page: demoting job:3 (-2) fits. Nothing should be dropped.
    const out = await m.reduceCv(jobs4(), DETAILED, 1, pagesByDetail(8, DETAILED));
    expect(out.droppedIds).toEqual([]);
    expect(out.demotedIds).toEqual(["job:3"]);
    expect(out.content.jobs).toHaveLength(4); // every position still on the CV
    expect(out.pages).toBe(1);
  });

  it("falls through to dropping once every demotion is spent", async () => {
    const out = await m.reduceCv(jobs4(), DETAILED, 1, pagesByDetail(5, DETAILED));
    expect(out.demotedIds).toEqual(["job:3", "job:2"]);
    expect(out.droppedIds).toEqual(["job:4"]);
    expect(out.fits).toBe(true);
  });

  it("still reports an unfittable CV honestly", async () => {
    const out = await m.reduceCv(jobs4(), DETAILED, 1, async () => 4);
    expect(out.fits).toBe(false);
  });
});
