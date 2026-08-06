import { describe, expect, it } from "vitest";
import type { CvContent } from "@/lib/cv-doc";
import {
  addOrder,
  applyAdd,
  applySteps,
  beyondCap,
  dropOrder,
  fitContent,
  GROW_HEADROOM,
  growCv,
  preflightKey,
  reduceCv,
  reductionOrder,
} from "@/lib/render/fit";
import { FALLBACK_SPEC } from "@/lib/render/spec";

/**
 * `[frontend]-fit-preflight`: the fit reduces *and* grows, and its middle gear is a
 * demotion (drop the description, keep the position) rather than a deletion.
 *
 * `pagesFor` is faked as a line count so the suite stays deterministic and fast — the real
 * react-pdf measurement is exercised by the render suites; what is under test here is the
 * search, not the renderer. Note the signature: `(content, demoted)`. Demotions travel
 * beside the content, never inside it — the same second channel `CvPages` already takes
 * (`demoted`), so what the search measures is byte-for-byte what the export renders.
 */

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

/** Every entry costs one line; `perPage` lines fit on a page. Detail-blind — used where
 *  the layout has no `detailed` budget, so demotion cannot happen anyway. */
const pagesBy = (perPage: number) => async (c: CvContent) =>
  Math.max(1, Math.ceil(count(c) / perPage));

describe("beyondCap", () => {
  it("takes only what the cap cut, in rank order", () => {
    const pool = beyondCap(full(), { jobs: 3, skills: 2 }, 10);
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
    // cap 3, headroom 1.5 → at most ceil(4.5) = 5 entries, so 2 in the pool.
    const pool = beyondCap(full(), { jobs: 3 }, 1.5);
    expect(pool.jobs.map((e) => e.id)).toEqual(["job:4", "job:5"]);
  });

  it("ships a headroom above 1, or the grow pass could never do anything", () => {
    expect(GROW_HEADROOM).toBeGreaterThan(1);
  });

  it("ignores sections the layout does not cap", () => {
    expect(beyondCap(full(), { jobs: 3 })).not.toHaveProperty("skills");
  });
});

describe("addOrder", () => {
  it("mirrors dropOrder: what sheds last is taken back first", () => {
    const pool = { jobs: [entry("job:1"), entry("job:2"), entry("job:3")] };
    // dropOrder protects each section's min-keep floor, so it offers fewer ids than
    // addOrder does — but over the ids they share, one order is the other reversed.
    const dropped = dropOrder(pool);
    const added = addOrder(pool);
    expect(added.filter((id) => dropped.includes(id))).toEqual(
      [...dropped].reverse(),
    );
  });

  it("takes pins first, then favourites, then the best of the rest", () => {
    const pool = {
      jobs: [entry("job:1"), entry("job:2"), entry("job:3", { pinned: true })],
    };
    const order = addOrder(pool, (id) => id === "job:2");
    expect(order).toEqual(["job:3", "job:2", "job:1"]);
  });

  it("returns nothing for an empty pool", () => {
    expect(addOrder({})).toEqual([]);
  });
});

describe("applyAdd", () => {
  it("re-inserts in the full content's rank order, not at the end", () => {
    const f = full();
    const kept: CvContent = { jobs: [f.jobs[0], f.jobs[1]], skills: [] };
    const out = applyAdd(kept, f, ["job:5"]);
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1", "job:2", "job:5"]);
  });

  it("leaves untouched sections and the input alone", () => {
    const f = full();
    const kept: CvContent = { jobs: [f.jobs[0]], skills: [f.skills[0]] };
    const out = applyAdd(kept, f, ["job:2"]);
    expect(out.skills.map((e) => e.id)).toEqual(["skill:1"]);
    expect(kept.jobs).toHaveLength(1); // immutable
  });
});

describe("growCv", () => {
  it("does nothing with an empty pool", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const out = await growCv(kept, f, {}, 1, pagesBy(10));
    expect(out.addedIds).toEqual([]);
    expect(count(out.content)).toBe(2);
  });

  it("takes the whole pool when the whole pool fits", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const pool: CvContent = { jobs: f.jobs.slice(2, 5) };
    const out = await growCv(kept, f, pool, 1, pagesBy(100));
    expect(out.addedIds).toHaveLength(3);
    expect(count(out.content)).toBe(5);
  });

  it("stops exactly at the page boundary — one more would spill", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const pool: CvContent = { jobs: f.jobs.slice(2, 8) };
    // 5 entries per page, 1 page: 2 kept + at most 3 added.
    const out = await growCv(kept, f, pool, 1, pagesBy(5));
    expect(count(out.content)).toBe(5);
    expect(out.addedIds).toHaveLength(3);
    expect(out.pages).toBe(1);
  });

  it("reports the page count of the content it returns", async () => {
    const f = full();
    const kept: CvContent = { jobs: f.jobs.slice(0, 2) };
    const pool: CvContent = { jobs: f.jobs.slice(2, 6) };
    const pagesFor = pagesBy(3);
    const out = await growCv(kept, f, pool, 2, pagesFor);
    expect(out.pages).toBe(await pagesFor(out.content));
  });

  it("measures without demotions — the grow pass only runs when there are none", async () => {
    const f = full();
    const seen: Set<string>[] = [];
    await growCv(
      { jobs: f.jobs.slice(0, 2) },
      f,
      { jobs: f.jobs.slice(2, 4) },
      1,
      async (c, demoted) => {
        seen.push(demoted);
        return Math.max(1, Math.ceil(count(c) / 10));
      },
    );
    expect(seen.length).toBeGreaterThan(0);
    for (const s of seen) expect(s.size).toBe(0);
  });
});

describe("fitContent — cap, then down OR up, never both", () => {
  it("drops and never grows when the capped content overflows", async () => {
    const out = await fitContent(full(), { jobs: 8, skills: 6 }, {}, 1, pagesBy(5));
    expect(out.droppedIds.length).toBeGreaterThan(0);
    expect(out.addedIds).toEqual([]);
    expect(out.pages).toBeLessThanOrEqual(1);
  });

  it("grows and never drops when the capped content leaves room", async () => {
    const out = await fitContent(full(), { jobs: 2, skills: 1 }, {}, 1, pagesBy(10));
    expect(out.droppedIds).toEqual([]);
    expect(out.demotedIds).toEqual([]);
    expect(out.addedIds.length).toBeGreaterThan(0);
    expect(count(out.content)).toBeGreaterThan(3);
  });

  it("does neither when the cap already fills the page exactly", async () => {
    // caps total 5 entries, 5 per page → nothing to drop, no room to add.
    const out = await fitContent(full(), { jobs: 3, skills: 2 }, {}, 1, pagesBy(5));
    expect(out.droppedIds).toEqual([]);
    expect(out.addedIds).toEqual([]);
    expect(count(out.content)).toBe(5);
  });

  it("propagates an unfittable CV instead of pretending it grew", async () => {
    const out = await fitContent(full(), { jobs: 8, skills: 6 }, {}, 1, async () => 3);
    expect(out.fits).toBe(false);
    expect(out.addedIds).toEqual([]);
  });

  it("respects the grow headroom rather than filling the page with everything", async () => {
    // cap 2 with headroom 1.5 → ceil(3) = 3 slots, so exactly one job may come back,
    // even though the page here would hold all eight.
    const out = await fitContent(full(), { jobs: 2 }, {}, 2, pagesBy(100));
    expect(out.addedIds).toEqual(["job:3"]);
    expect(out.content.jobs).toHaveLength(3);
    // The uncapped section is not a grow candidate at all — capContent already let it
    // through whole, so there is nothing "beyond the cap" to take back.
    expect(out.content.skills).toHaveLength(6);
  });
});

describe("preflightKey", () => {
  const base = {
    spec: FALLBACK_SPEC,
    content: full(),
    sectionsOff: [] as string[],
    cvHeader: { name: "Lukas", contact: "a@b.c", summary: "Backend dev" },
    letterBody: "Dear team",
    letterMeta: { subject: "x" },
  };

  it("is stable for identical inputs", () => {
    expect(preflightKey(base)).toBe(preflightKey({ ...base, content: full() }));
  });

  it("changes when anything the render depends on changes", () => {
    const k = preflightKey(base);
    expect(
      preflightKey({
        ...base,
        spec: { ...FALLBACK_SPEC, cv: { ...FALLBACK_SPEC.cv, pages: 2 } },
      }),
    ).not.toBe(k);
    expect(preflightKey({ ...base, sectionsOff: ["skills"] })).not.toBe(k);
    expect(preflightKey({ ...base, letterBody: "Dear team!" })).not.toBe(k);
    expect(preflightKey({ ...base, letterMeta: { subject: "y" } })).not.toBe(k);
    expect(preflightKey({ ...base, content: { jobs: [entry("job:1")] } })).not.toBe(k);
  });

  it("includes the CV header — contact and summary are page content, not chrome", () => {
    const k = preflightKey(base);
    expect(
      preflightKey({ ...base, cvHeader: { ...base.cvHeader, contact: "x@y.z" } }),
    ).not.toBe(k);
    expect(
      preflightKey({ ...base, cvHeader: { ...base.cvHeader, summary: "" } }),
    ).not.toBe(k);
  });
});

/**
 * The reduction ladder's middle gear: demote a job to its title before deleting it.
 *
 * The pager below mirrors the render rule (`entryDetail`) exactly: an explicit
 * `entry.detail` wins, then the fit's `demoted` set, then rank against the `detailed`
 * budget. A full entry costs `fullCost` lines, a compact one costs 1 — without that
 * asymmetry a demotion would be free and the search untestable.
 */
const pagesByDetail =
  (perPage: number, detailed: Record<string, number>, fullCost = 3) =>
  async (c: CvContent, demoted: Set<string> = new Set()) => {
    let lines = 0;
    for (const [section, list] of Object.entries(c)) {
      const budget = detailed[section] ?? 0;
      list.forEach((e, i) => {
        const isFull = e.detail
          ? e.detail === "full"
          : !demoted.has(e.id) && i < budget;
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

describe("reductionOrder", () => {
  it("offers every demotion before any drop", () => {
    const steps = reductionOrder(jobs4(), DETAILED);
    const firstDrop = steps.findIndex((s) => s.kind === "drop");
    const lastDemote = steps.map((s) => s.kind).lastIndexOf("demote");
    expect(lastDemote).toBeLessThan(firstDrop);
  });

  it("demotes deepest-first and never touches the top entry", () => {
    const demotes = reductionOrder(jobs4(), DETAILED)
      .filter((s) => s.kind === "demote")
      .map((s) => s.id);
    expect(demotes).toEqual(["job:3", "job:2"]);
    expect(demotes).not.toContain("job:1"); // MIN_DETAILED
  });

  it("offers no demotion for entries that are already one-liners", () => {
    // job:4 sits beyond the `detailed` budget, so it renders compact already.
    const demotes = reductionOrder(jobs4(), DETAILED).filter(
      (s) => s.kind === "demote",
    );
    expect(demotes.map((s) => s.id)).not.toContain("job:4");
  });

  it("leaves an entry the user gave an explicit detail alone, either way", () => {
    const content: CvContent = {
      jobs: [
        entry("job:1"),
        entry("job:2", { detail: "compact" }), // nothing left to take
        entry("job:3", { detail: "full" }), // the user insists — the fit may not
        entry("job:4"),
      ],
    };
    const demotes = reductionOrder(content, { jobs: 4 }).filter(
      (s) => s.kind === "demote",
    );
    expect(demotes.map((s) => s.id)).toEqual(["job:4"]);
  });

  it("contributes only drops for a section with no detail budget", () => {
    const steps = reductionOrder({ skills: full().skills }, DETAILED);
    expect(steps.every((s) => s.kind === "drop")).toBe(true);
  });
});

describe("applySteps", () => {
  it("removes dropped entries and reports demotions beside the content", () => {
    const out = applySteps(jobs4(), [
      { kind: "demote", id: "job:2" },
      { kind: "drop", id: "job:4" },
    ]);
    expect(out.content.jobs.map((e) => e.id)).toEqual([
      "job:1",
      "job:2",
      "job:3",
    ]);
    expect(out.demoted).toEqual(new Set(["job:2"]));
    // The content itself stays editorially clean: `detail` is the user's field, and a
    // machine demotion must never be mistaken for one (it would then survive into the
    // markdown export, which honours `entry.detail` only).
    expect(out.content.jobs[1].detail).toBeUndefined();
  });

  it("does not report an entry it also dropped", () => {
    const out = applySteps(jobs4(), [
      { kind: "demote", id: "job:3" },
      { kind: "drop", id: "job:3" },
    ]);
    expect(out.demoted.size).toBe(0);
    expect(out.content.jobs.map((e) => e.id)).toEqual([
      "job:1",
      "job:2",
      "job:4",
    ]);
  });

  it("does not mutate the input", () => {
    const input = jobs4();
    applySteps(input, [
      { kind: "demote", id: "job:2" },
      { kind: "drop", id: "job:4" },
    ]);
    expect(input.jobs).toHaveLength(4);
    expect(input.jobs[1].detail).toBeUndefined();
  });

  it("ignores a step naming an id that is not there", () => {
    const out = applySteps(jobs4(), [{ kind: "drop", id: "job:99" }]);
    expect(out.content.jobs).toHaveLength(4);
  });
});

describe("reduceCv — demote before drop", () => {
  it("does nothing when it already fits", async () => {
    const out = await reduceCv(jobs4(), DETAILED, 1, pagesByDetail(10, DETAILED));
    expect(out.demotedIds).toEqual([]);
    expect(out.droppedIds).toEqual([]);
    expect(out.fits).toBe(true);
  });

  it("shortens a job rather than deleting one when that is enough", async () => {
    // 10 lines, 8 per page: demoting job:3 (-2) fits. Nothing should be dropped.
    const out = await reduceCv(jobs4(), DETAILED, 1, pagesByDetail(8, DETAILED));
    expect(out.droppedIds).toEqual([]);
    expect(out.demotedIds).toEqual(["job:3"]);
    expect(out.content.jobs).toHaveLength(4); // every position still on the CV
    expect(out.pages).toBe(1);
  });

  it("falls through to dropping once every demotion is spent", async () => {
    const out = await reduceCv(jobs4(), DETAILED, 1, pagesByDetail(5, DETAILED));
    expect(out.demotedIds).toEqual(["job:3", "job:2"]);
    expect(out.droppedIds).toEqual(["job:4"]);
    expect(out.fits).toBe(true);
  });

  it("reports a dropped entry once, as a drop, even if it was demoted on the way", async () => {
    // Nothing fits: every step is applied, so job:3 and job:2 are demoted *and* then
    // dropped. They are gone — reporting them as "shortened" would be a lie.
    const out = await reduceCv(jobs4(), DETAILED, 1, async () => 4);
    expect(out.fits).toBe(false);
    expect(out.demotedIds).toEqual([]);
    expect(out.droppedIds).toEqual(["job:4", "job:3", "job:2"]);
  });

  it("never demotes what the user pinned to full, and pays with a drop instead", async () => {
    const content: CvContent = {
      jobs: [
        entry("job:1"),
        entry("job:2", { detail: "full" }),
        entry("job:3", { detail: "full" }),
        entry("job:4"),
      ],
    };
    // 10 lines, 9 per page: one line has to go. The only demotable rows carry an
    // explicit `full`, so the ladder skips straight to the drop rung.
    const out = await reduceCv(content, DETAILED, 1, pagesByDetail(9, DETAILED));
    expect(out.demotedIds).toEqual([]);
    expect(out.droppedIds).toEqual(["job:4"]);
  });
});
