import { beforeAll, describe, expect, it } from "vitest";
import type { PortfolioItem } from "@/lib/queries/portfolio";

/**
 * Guide 29, the Studio's "Experience": the page's jobs and education as bars on one year axis.
 * Only entries with a start date get a bar, newest first; the ticks stay few enough for a phone.
 *
 * Lazy import, as in appearance.test.ts: collectible while `@/lib/portfolio/timeline` doesn't
 * exist yet. `Mod` is the guide's contract.
 */

type Bar = {
  item: PortfolioItem;
  kind: "work" | "study";
  left: number;
  width: number;
  recency: number;
  running: boolean;
};
type Axis = {
  from: number;
  to: number;
  ticks: { year: number; left: number }[];
  rows: Bar[];
};
type Mod = {
  MIN_BARS: number;
  MAX_TICKS: number;
  yearBars(items: PortfolioItem[], today?: number): Axis | null;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/portfolio/timeline")) as unknown as Mod;
});

const TODAY = Date.parse("2026-11-24");

const entry = (
  id: string,
  started: string | null,
  ended: string | null = null,
): PortfolioItem => ({
  id,
  type: id.split(":")[0] as PortfolioItem["type"],
  title: id,
  domains: [],
  started,
  ended,
});
const row = (axis: Axis, id: string) => {
  const r = axis.rows.find((x) => x.item.id === id);
  if (!r) throw new Error(`no bar for ${id}`);
  return r;
};

describe("yearBars: what gets a bar", () => {
  it("needs two dated entries: one bar says nothing its card doesn't", () => {
    expect(m.MIN_BARS).toBe(2);
    expect(m.yearBars([entry("job:1", "2020-01-01")], TODAY)).toBeNull();
    expect(m.yearBars([], TODAY)).toBeNull();
  });

  it("draws jobs and education only, and only with a start date", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2020-01-01"),
        entry("education:1", "2014-10-01", "2018-09-30"),
        entry("job:2", null, "2019-01-01"),
        entry("project:1", "2021-01-01"),
        entry("certification:1", "2022-01-01"),
      ],
      TODAY,
    )!;
    expect(axis.rows.map((r) => r.item.id).sort()).toEqual([
      "education:1",
      "job:1",
    ]);
  });

  it("counts an entry once, even when the page lists it twice", () => {
    const job = entry("job:1", "2020-01-01");
    const axis = m.yearBars(
      [job, entry("job:2", "2018-01-01", "2019-12-31"), job],
      TODAY,
    )!;
    expect(axis.rows).toHaveLength(2);
  });

  it("calls a job work and education study", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2020-01-01"),
        entry("education:1", "2015-10-01", "2019-09-30"),
      ],
      TODAY,
    )!;
    expect(row(axis, "job:1").kind).toBe("work");
    expect(row(axis, "education:1").kind).toBe("study");
  });
});

describe("yearBars: the geometry", () => {
  it("runs the axis from the first start's year to the year after the last end", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2020-01-01", "2021-12-31"),
        entry("education:1", "2018-01-01", "2020-01-01"),
      ],
      TODAY,
    )!;
    expect(axis.from).toBe(2018);
    expect(axis.to).toBe(2022);
  });

  it("places each bar by its dates, in percent of the axis", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2020-01-01", "2021-12-31"),
        entry("education:1", "2018-01-01", "2020-01-01"),
      ],
      TODAY,
    )!;
    // 2018–2022 is four years: 2020 sits half-way, two years are half of it.
    expect(row(axis, "job:1").left).toBeCloseTo(50, 0);
    expect(row(axis, "job:1").width).toBeCloseTo(50, 0);
    expect(row(axis, "education:1").left).toBeCloseTo(0, 5);
    expect(row(axis, "education:1").width).toBeCloseTo(50, 0);
  });

  it("runs a current job until today", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2024-01-01"),
        entry("job:2", "2020-01-01", "2023-12-31"),
      ],
      TODAY,
    )!;
    const now = row(axis, "job:1");
    expect(now.running).toBe(true);
    expect(row(axis, "job:2").running).toBe(false);
    expect(axis.to).toBe(2027);
    // Today is late November 2026: the bar ends a few percent short of the axis' end.
    expect(now.left + now.width).toBeGreaterThan(95);
    expect(now.left + now.width).toBeLessThan(100);
  });

  it("keeps every bar visible and inside the axis", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2010-01-01", "2026-11-24"),
        entry("job:2", "2026-11-01", "2026-11-10"), // ten days in sixteen years
        entry("job:3", "2015-06-01", "2015-01-01"), // ended before it started
      ],
      TODAY,
    )!;
    for (const r of axis.rows) {
      expect(r.width).toBeGreaterThan(1);
      expect(r.left).toBeGreaterThanOrEqual(0);
      expect(r.left + r.width).toBeLessThanOrEqual(100 + 1e-9);
    }
  });
});

describe("yearBars: order and colour", () => {
  const axis = () =>
    m.yearBars(
      [
        entry("education:1", "2012-10-01", "2016-09-30"),
        entry("job:1", "2021-03-01"),
        entry("job:2", "2016-10-01", "2021-02-28"),
      ],
      TODAY,
    )!;

  it("lists the newest first", () => {
    expect(axis().rows.map((r) => r.item.id)).toEqual([
      "job:1",
      "job:2",
      "education:1",
    ]);
  });

  it("steps recency evenly by order: the earliest end 0, the running one 1", () => {
    const a = axis();
    expect(row(a, "education:1").recency).toBe(0);
    expect(row(a, "job:2").recency).toBe(0.5);
    expect(row(a, "job:1").recency).toBe(1);
  });
});

describe("yearBars: the ticks", () => {
  it("labels every year on a short axis", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2022-01-01", "2023-06-30"),
        entry("job:2", "2020-02-01", "2021-12-31"),
      ],
      TODAY,
    )!;
    expect(axis.ticks.map((t) => t.year)).toEqual([2020, 2021, 2022, 2023]);
    expect(axis.ticks[0].left).toBeCloseTo(0, 5);
  });

  it("thins a long career to a few round years, in order and inside the axis", () => {
    const axis = m.yearBars(
      [
        entry("job:1", "2019-01-01"),
        entry("education:1", "2001-10-01", "2006-09-30"),
      ],
      TODAY,
    )!;
    const years = axis.ticks.map((t) => t.year);
    expect(years.length).toBeLessThanOrEqual(m.MAX_TICKS);
    expect(years.length).toBeGreaterThanOrEqual(3);
    expect(years).toEqual([...years].sort((a, b) => a - b));
    const step = years[1] - years[0];
    expect([2, 5, 10]).toContain(step);
    for (const y of years) expect(y % step).toBe(0);
    for (const t of axis.ticks) {
      expect(t.left).toBeGreaterThanOrEqual(0);
      expect(t.left).toBeLessThan(100);
    }
  });
});
