import { describe, it, expect } from "vitest";
import {
  applyDrop,
  capContent,
  countPdfPages,
  dropOrder,
  effectiveCaps,
  fitCv,
  MAX_CAP_GROWTH,
  overCapIds,
} from "@/lib/render/fit";
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

  it("pinned entries drop last of all — even after favourites", () => {
    const c = content();
    c.skills[5] = { ...c.skills[5], pinned: true };
    // job:3 is a favourite, skill:6 is pinned: everything plain first, then the
    // favourite, then the pin.
    const order = dropOrder(c, (id) => id === "job:3");
    expect(order).toEqual(["skill:5", "skill:4", "job:2", "job:3", "skill:6"]);
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

describe("capContent (template entry budget)", () => {
  it("cuts each section to its cap, keeping the ranked head", () => {
    const out = capContent(content(), { skills: 2, jobs: 2 });
    expect(out.skills.map((e) => e.id)).toEqual(["skill:1", "skill:2"]);
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1", "job:2"]);
    expect(out.educations).toHaveLength(1); // uncapped section passes through
  });

  it("leaves content under the cap untouched", () => {
    const out = capContent(content(), { skills: 99 });
    expect(out.skills).toHaveLength(6);
  });
});

describe("overCapIds (editor warning set)", () => {
  it("flags entries past the cap, counting active entries only", () => {
    const c = content();
    c.skills[1] = { ...c.skills[1], deselected: true }; // skill:2 doesn't render
    const over = overCapIds(c, { skills: 3 });
    // Active order: 1,3,4,5,6 → 4th+ active are over.
    expect(over).toEqual(new Set(["skill:5", "skill:6"]));
  });

  it("never flags deselected entries and ignores uncapped sections", () => {
    const c = content();
    c.skills[5] = { ...c.skills[5], deselected: true };
    const over = overCapIds(c, { skills: 5 });
    expect(over.size).toBe(0);
    expect(overCapIds(content(), {}).size).toBe(0);
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

/**
 * `[fullstack]-cv-section-toggles`. SKIP-MARKED — not the active guide.
 * **Step 0: delete the `.skip` below.**
 *
 * Switching a section off frees *page space*, not a slot count — 4 certification slots
 * are worth about one and a half jobs, not four. So the caps are scaled by the freed
 * weight rather than traded one for one.
 */
describe.skip("effectiveCaps", () => {
  const CAPS = {
    jobs: 5,
    educations: 3,
    projects: 4,
    certifications: 4,
    skills: 18,
    languages: 6,
  };

  it("changes nothing when every section is on", () => {
    expect(effectiveCaps(CAPS, [])).toEqual(CAPS);
    expect(effectiveCaps(CAPS)).toEqual(CAPS);
  });

  it("drops the switched-off section from the result entirely", () => {
    expect(effectiveCaps(CAPS, ["certifications"])).not.toHaveProperty(
      "certifications",
    );
  });

  it("gives the freed budget to the sections that stay", () => {
    const out = effectiveCaps(CAPS, ["certifications"]);
    expect(out.jobs).toBeGreaterThan(CAPS.jobs);
    expect(out.skills).toBeGreaterThanOrEqual(CAPS.skills);
  });

  it("weighs sections by page space, not by slot count", () => {
    // skills: 18 slots × weight 1 = 18 lines freed.
    // certifications: 4 slots × weight 2 = 8 lines freed — fewer, despite 4 vs 18 being
    // the smaller number only in slots.
    const offSkills = effectiveCaps(CAPS, ["skills"]);
    const offCerts = effectiveCaps(CAPS, ["certifications"]);
    expect(offSkills.jobs).toBeGreaterThan(offCerts.jobs);
  });

  it("clamps growth so one toggle loosens the layout instead of abolishing it", () => {
    const out = effectiveCaps(CAPS, [
      "certifications",
      "skills",
      "languages",
      "projects",
      "educations",
    ]);
    expect(out.jobs).toBeLessThanOrEqual(CAPS.jobs * MAX_CAP_GROWTH);
  });

  it("never returns a cap below one", () => {
    const out = effectiveCaps({ jobs: 1, skills: 1 }, ["skills"]);
    expect(out.jobs).toBeGreaterThanOrEqual(1);
  });
});
