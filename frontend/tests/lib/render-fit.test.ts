import { describe, it, expect } from "vitest";
import { applyDrop, countPdfPages, dropOrder, fitCv } from "@/lib/render/fit";
import type { CvContent } from "@/lib/cv-doc";

/**
 * Fit-to-layout logic (guide [frontend]-render-export). The drop order is scale-free —
 * position fraction within a section, not raw relevance scores (incomparable across rungs
 * and sections) — respects per-section min-keep floors, and drops favourites last. fitCv
 * binary-searches the smallest drop that fits, with the renderer injected as `pagesFor`.
 */

function entry(id: string) {
  return { id, label: id, relevance_score: null };
}

// 3 jobs (floor 1), 6 skills (floor 3), 1 education (floor 1 → untouchable).
function content(): CvContent {
  return {
    jobs: [entry("job:1"), entry("job:2"), entry("job:3")],
    skills: [
      entry("skill:1"),
      entry("skill:2"),
      entry("skill:3"),
      entry("skill:4"),
      entry("skill:5"),
      entry("skill:6"),
    ],
    educations: [entry("education:1")],
  };
}

const totalEntries = (c: CvContent) =>
  Object.values(c).reduce((n, list) => n + list.length, 0);

describe("dropOrder", () => {
  it("drops tails first (by position fraction), bigger section on ties, floors protected", () => {
    // frac: skill:6=1.0, job:3=1.0 (skill wins the tie via size 6>3), skill:5=.83,
    // then .67 tie between skill:4 and job:2 (size again).
    expect(dropOrder(content())).toEqual([
      "skill:6",
      "job:3",
      "skill:5",
      "skill:4",
      "job:2",
    ]);
  });

  it("never offers floor entries (skills keep 3, other sections keep 1)", () => {
    const order = dropOrder(content());
    for (const id of ["job:1", "skill:1", "skill:2", "skill:3", "education:1"]) {
      expect(order).not.toContain(id);
    }
  });

  it("favourites drop only after every non-favourite", () => {
    const order = dropOrder(content(), (id) => id === "skill:6");
    expect(order).toEqual(["job:3", "skill:5", "skill:4", "job:2", "skill:6"]);
  });
});

describe("applyDrop", () => {
  it("removes exactly the given ids", () => {
    const out = applyDrop(content(), ["skill:6", "job:3"]);
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1", "job:2"]);
    expect(out.skills).toHaveLength(5);
    expect(out.educations).toHaveLength(1);
  });
});

describe("countPdfPages", () => {
  it("counts /Type /Page dictionaries, excluding the /Pages tree node", () => {
    const pdfText =
      "1 0 obj << /Type /Pages /Kids [2 0 R 3 0 R] >> " +
      "2 0 obj << /Type /Page /Parent 1 0 R >> " +
      "3 0 obj << /Type/Page /Parent 1 0 R >>"; // no space is valid PDF too
    expect(countPdfPages(pdfText)).toBe(2);
  });

  it("is 0 when nothing matches", () => {
    expect(countPdfPages("<< /Type /Pages >>")).toBe(0);
  });
});

describe("fitCv", () => {
  // Fake renderer: 4 entries per page.
  const pagesFor = (c: CvContent) => Promise.resolve(Math.ceil(totalEntries(c) / 4));

  it("returns the content untouched when it already fits", async () => {
    const c = content(); // 10 entries → 3 pages
    const fit = await fitCv(c, 3, pagesFor);
    expect(fit.content).toBe(c);
    expect(fit.droppedIds).toEqual([]);
    expect(fit.pages).toBe(3);
    expect(fit.fits).toBe(true);
  });

  it("drops the minimal ranked tail to fit", async () => {
    // 10 entries → 3 pages; 2 pages allow 8 entries → exactly 2 drops, in drop order.
    const fit = await fitCv(content(), 2, pagesFor);
    expect(fit.droppedIds).toEqual(["skill:6", "job:3"]);
    expect(totalEntries(fit.content)).toBe(8);
    expect(fit.pages).toBe(2);
    expect(fit.fits).toBe(true);
  });

  it("reports fits=false when even the min-keep floor overflows", async () => {
    const fit = await fitCv(content(), 1, pagesFor);
    // Floor = 1 job + 3 skills + 1 education = 5 entries → 2 pages > 1.
    expect(fit.fits).toBe(false);
    expect(fit.droppedIds).toHaveLength(5);
    expect(totalEntries(fit.content)).toBe(5);
    expect(fit.pages).toBe(2);
  });
});
