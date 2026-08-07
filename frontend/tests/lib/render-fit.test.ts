import { describe, it, expect } from "vitest";
import {
  applyDrop,
  capContent,
  countPdfPages,
  dropOrder,
  effectiveCaps,
  MAX_CAP_GROWTH,
} from "@/lib/render/fit";
import type { CvContent } from "@/lib/cv-doc";

/**
 * Fit-to-layout primitives (guide [frontend]-render-export). The drop order is scale-free —
 * position fraction within a section, not raw relevance scores (incomparable across rungs
 * and sections) — respects per-section min-keep floors, and drops favourites last.
 *
 * The searches that consume these (`reduceCv`, `growCv`, `fitContent`) live in
 * `render-preflight.test.ts`, together with the demote-before-drop ladder that replaced
 * the old drop-only `fitCv`.
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

  /**
   * `[frontend]-fit-preflight` Results round 1. A pin is the user saying "this one is on
   * the CV"; the editorial cap does not get to overrule it silently. The page fit still
   * can — and drops pins last (`dropOrder`) — but that at least gets reported.
   */
  it("keeps a pinned entry wherever it sits, cap or no cap", () => {
    const c = content();
    c.skills[5] = { ...c.skills[5], pinned: true };
    const out = capContent(c, { skills: 2 });
    expect(out.skills.map((e) => e.id)).toEqual([
      "skill:1",
      "skill:2",
      "skill:6",
    ]);
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

/**
 * `[fullstack]-cv-section-toggles` — the ACTIVE guide.
 *
 * Switching a section off frees *page space*, not a slot count — 4 certification slots
 * are worth about one and a half jobs, not four. So the caps are scaled by the freed
 * weight rather than traded one for one.
 */
describe("effectiveCaps", () => {
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
    //
    // Read on `projects`, not `jobs`: at these budgets both toggles round the 5 job
    // slots to 6, so `jobs` cannot tell the two apart — 4 project slots can (4 → 5 for
    // the bigger release, 4 → 4 for the smaller). Rounding hides small differences;
    // that is a property of integer caps, not a reason to weaken the claim.
    const offSkills = effectiveCaps(CAPS, ["skills"]);
    const offCerts = effectiveCaps(CAPS, ["certifications"]);
    expect(offSkills.projects).toBeGreaterThan(offCerts.projects);
    expect(offSkills.jobs).toBeGreaterThanOrEqual(offCerts.jobs);
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
