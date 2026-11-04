import { beforeAll, describe, expect, it } from "vitest";
import { renderToBuffer } from "@react-pdf/renderer";
import type { CvContent } from "@/lib/cv-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { skillGroups } from "@/lib/render/parts";
import { FALLBACK_SPEC } from "@/lib/render/spec";
import { CvDocument, cvStyles } from "@/lib/render/templates";
import { flat, pdfTextRuns } from "./_pdf-text";

/**
 * [frontend]-polished-render: the moderncv-classic look — a dated left column, ruled/colored
 * section titles, and skills grouped into \cvitem-style category rows. Starts RED: `skillGroups`
 * and the two-column `cvStyles` keys don't exist yet, and the current compact render joins skills
 * on one line without a "Technical" category label.
 */

const db = {
  skills: [
    { id: 1, name: "Python", proficiency: "expert", category: "technical", favourite: false },
    { id: 2, name: "Teamwork", proficiency: "advanced", category: "soft", favourite: false },
  ],
  jobs: [
    {
      id: 12,
      title: "Senior Dev",
      company: "ACME",
      started: "2021-01-01",
      ended: null,
      skills: [1],
      description: "Built the pipeline.",
      favourite: false,
    },
  ],
  educations: [],
  certifications: [],
  projects: [],
  languages: [{ id: 5, name: "German", fluency: "native", favourite: false }],
} as unknown as CvEntriesResponse;

const content: CvContent = {
  jobs: [{ id: "job:12", label: "Senior Dev at ACME", relevance_score: null }],
  skills: [
    { id: "skill:1", label: "Python", relevance_score: null },
    { id: "skill:2", label: "Teamwork", relevance_score: null },
  ],
  languages: [{ id: "language:5", label: "German", relevance_score: null }],
};

describe("cvStyles — moderncv two-column + rule", () => {
  const s = cvStyles(FALLBACK_SPEC);

  it("lays entries out in a row with a fixed date/label column", () => {
    expect(s.row.flexDirection).toBe("row");
    expect(s.hints.width).toBeGreaterThan(0);
  });

  it("rules the section titles in the accent colour", () => {
    expect(s.sectionTitle.borderBottomColor).toBe(FALLBACK_SPEC.colors.accent);
  });

  it("preserves the density decision (entry gap = base/3)", () => {
    expect(s.entry.marginBottom).toBeCloseTo(FALLBACK_SPEC.font.base_pt / 3, 1);
  });
});

describe("skillGroups", () => {
  it("groups by category with display labels, in category order", () => {
    // [frontend]-cv-typography: technical/domain names carry their proficiency now that
    // the per-entry skill cloud freed the room. Soft skills deliberately don't.
    expect(skillGroups(db, content.skills)).toEqual([
      { label: "Technical", names: "Python (expert)" },
      { label: "Soft", names: "Teamwork" },
    ]);
  });

  it("falls back to the stored label when the DB row is missing", () => {
    expect(
      skillGroups(undefined, [{ id: "skill:9", label: "Rust", relevance_score: null }]),
    ).toEqual([{ label: "Other", names: "Rust" }]);
  });
});

describe("CV render", () => {
  let text: string;
  beforeAll(async () => {
    const buf = await renderToBuffer(
      CvDocument({
        spec: FALLBACK_SPEC,
        name: "Ada Lovelace",
        content,
        db,
      } as Parameters<typeof CvDocument>[0]),
    );
    text = flat(pdfTextRuns(buf));
  }, 30_000);

  it("renders skills under a capitalized category label (was a lowercase joined line)", () => {
    expect(text).toContain(flat("Technical"));
    expect(text).toContain(flat("Python"));
  });
});
