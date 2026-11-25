import type { PortfolioItem } from "@/lib/queries/portfolio";

/** The Studio's "Experience": jobs and education as bars on a shared year axis. Pure, so the
 *  tests/ regime covers it; the drawing is components/portfolio/year-bars.tsx. Only what has a
 *  start date gets a bar: a bar without one would say something the data doesn't. */

export type YearBar = {
  item: PortfolioItem;
  kind: "work" | "study";
  /** Where the bar starts and how wide it is, in % of the axis. */
  left: number;
  width: number;
  /** 0 (the bar that ended first) … 1 (the most recent), evenly stepped by order, as in the
   *  skill network: careers bunch up at the recent end, and steps keep the colours apart. */
  recency: number;
  running: boolean;
};

export type YearAxis = {
  /** The first year on the axis, and the year after the last. */
  from: number;
  to: number;
  /** Year labels, at most MAX_TICKS, on round years. */
  ticks: { year: number; left: number }[];
  /** Newest first. */
  rows: YearBar[];
};

/** One bar says nothing its card doesn't. */
export const MIN_BARS = 2;
/** About 45 px a label on a phone's 320 px track. */
export const MAX_TICKS = 7;
/** A month-long job still shows. */
const MIN_WIDTH = 1.5;
const TICK_STEPS = [1, 2, 5, 10, 20];

const yearStart = (y: number) => Date.UTC(y, 0, 1);

/** The axis for a page's jobs and education, or null when fewer than MIN_BARS have a start. */
export function yearBars(
  items: PortfolioItem[],
  today: number = Date.now(),
): YearAxis | null {
  const seen = new Set<string>();
  const spans = items.flatMap((item) => {
    if (item.type !== "job" && item.type !== "education") return [];
    if (seen.has(item.id)) return [];
    const start = item.started ? Date.parse(item.started) : NaN;
    if (Number.isNaN(start)) return [];
    seen.add(item.id);
    const ended = item.ended ? Date.parse(item.ended) : NaN;
    const running = Number.isNaN(ended);
    return [
      { item, start, end: Math.max(start, running ? today : ended), running },
    ];
  });
  if (spans.length < MIN_BARS) return null;

  const from = new Date(
    Math.min(...spans.map((s) => s.start)),
  ).getUTCFullYear();
  const to =
    new Date(Math.max(...spans.map((s) => s.end))).getUTCFullYear() + 1;
  const span = yearStart(to) - yearStart(from);
  const at = (t: number) => ((t - yearStart(from)) / span) * 100;

  const ends = [...new Set(spans.map((s) => s.end))].sort((a, b) => a - b);
  const recencyOf = (end: number) =>
    ends.length > 1 ? ends.indexOf(end) / (ends.length - 1) : 1;

  const rows = spans
    .sort((a, b) => b.end - a.end || b.start - a.start)
    .map((s): YearBar => {
      const width = Math.max(MIN_WIDTH, at(s.end) - at(s.start));
      return {
        item: s.item,
        kind: s.item.type === "education" ? "study" : "work",
        left: Math.min(at(s.start), 100 - width),
        width,
        recency: recencyOf(s.end),
        running: s.running,
      };
    });

  const years = Array.from({ length: to - from }, (_, i) => from + i);
  const step =
    TICK_STEPS.find(
      (n) => years.filter((y) => y % n === 0).length <= MAX_TICKS,
    ) ?? TICK_STEPS[TICK_STEPS.length - 1];
  const ticks = years
    .filter((y) => y % step === 0)
    .map((year) => ({ year, left: at(yearStart(year)) }));

  return { from, to, ticks, rows };
}
