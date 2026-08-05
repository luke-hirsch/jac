import { describe, expect, it } from "vitest";
import { renderToBuffer } from "@react-pdf/renderer";
import type { CvContent } from "@/lib/cv-doc";
import { cvToMarkdown, joinedContent } from "@/lib/export";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import {
  MIN_DETAILED,
  datePoint,
  dateParts,
  entryDetail,
  entryParts,
  skillGroups,
} from "@/lib/render/parts";
import { FALLBACK_SPEC } from "@/lib/render/spec";
import { CvDocument, bodyBlocks } from "@/lib/render/templates";
import { countPdfPages } from "@/lib/render/fit";
import { flat, pdfTextRuns } from "./_pdf-text";

/**
 * `[frontend]-cv-typography`. SKIP-MARKED — this is not the active guide.
 * **Step 0 of the guide: delete every `.skip` in this file** and watch it go red.
 *
 * What it pins: the per-entry skill cloud leaves the visible page but must land in the
 * machine layer (`skill_names`) and in the markdown export — moved, not deleted. Plus the
 * date column that stops wrapping, markdown-ish bullets, and proficiency in the sidebar.
 */

const NBSP = " ";

const db = {
  skills: [
    {
      id: 1,
      name: "Kubernetes",
      proficiency: "expert",
      category: "technical",
      favourite: false,
    },
    {
      id: 2,
      name: "Teamwork",
      proficiency: "advanced",
      category: "soft",
      favourite: false,
    },
    {
      id: 3,
      name: "Logistics",
      proficiency: "intermediate",
      category: "domain",
      favourite: false,
    },
  ],
  jobs: [
    {
      id: 12,
      title: "Senior Dev",
      company: "ACME",
      started: "2021-03-01",
      ended: null,
      skills: [1],
      description: "Ran the platform.\n- migrated the **legacy** cluster\n- cut costs",
      favourite: false,
    },
  ],
  projects: [
    {
      id: 4,
      name: "Shopify Uploader",
      started: "2020-03-01",
      ended: "2023-01-31",
      url: "https://example.org/up",
      skills: [1],
      description: "Bulk imports.",
      favourite: false,
    },
  ],
  certifications: [
    {
      id: 7,
      name: "Shell Scripting",
      issuer: "Udemy",
      issued_on: "2020-03-01",
      skills: [],
      description: "",
      favourite: false,
    },
  ],
  educations: [],
  languages: [],
} as unknown as CvEntriesResponse;

const job = { id: "job:12", label: "Senior Dev at ACME", relevance_score: null };
const project = { id: "project:4", label: "Shopify Uploader", relevance_score: null };
const cert = { id: "certification:7", label: "Shell Scripting", relevance_score: null };

describe.skip("dateParts / datePoint — the left column stops wrapping", () => {
  it("keeps each side unbreakable and puts the dash on the second line", () => {
    const d = dateParts("2021-03-01", "2023-01-31");
    expect(d.from).toBe(`Mar${NBSP}2021`);
    expect(d.to).toBe(`– Jan${NBSP}2023`);
    expect(d.from).not.toContain(" "); // a plain space is a break opportunity
  });

  it("says present for an open-ended range", () => {
    expect(dateParts("2021-03-01", null).to).toBe("– present");
  });

  it("marks an unknown start rather than rendering an empty column", () => {
    expect(dateParts(null, "2023-01-31").from).toBe("?");
  });

  it("gives a point in time one line and no continuation", () => {
    const d = datePoint("2020-03-01");
    expect(d.from).toBe(`Mar${NBSP}2020`);
    expect(d.to).toBe("");
  });
});

describe.skip("bodyBlocks — markdown-ish descriptions", () => {
  it("flags the three bullet markers that appear in the career DB", () => {
    expect(bodyBlocks("- one\n* two\n• three")).toEqual([
      { bullet: true, text: "one" },
      { bullet: true, text: "two" },
      { bullet: true, text: "three" },
    ]);
  });

  it("strips bold markers instead of printing them", () => {
    expect(bodyBlocks("the **legacy** and __old__ stack")).toEqual([
      { bullet: false, text: "the legacy and old stack" },
    ]);
  });

  it("drops blank lines and keeps prose verbatim", () => {
    expect(bodyBlocks("intro\n\n- a point\n")).toEqual([
      { bullet: false, text: "intro" },
      { bullet: true, text: "a point" },
    ]);
  });

  it("survives an empty description", () => {
    expect(bodyBlocks("")).toEqual([]);
  });
});

describe.skip("entryParts — skills leave the visible meta line", () => {
  it("empties a job's meta and carries the resolved skills separately", () => {
    const p = entryParts(db, "jobs", job);
    expect(p.meta).toBe("");
    expect(p.skills).toBe("Kubernetes");
    expect(p.heading).toBe("Senior Dev — ACME");
  });

  it("keeps a project's url visible but not its skill cloud", () => {
    const p = entryParts(db, "projects", project);
    expect(p.meta).toBe("https://example.org/up");
    expect(p.skills).toBe("Kubernetes");
  });

  it("still exposes the joined range for the markdown export", () => {
    expect(entryParts(db, "jobs", job).date).toBe("Mar 2021 – present");
  });

  it("gives a certification a single-line date column", () => {
    const p = entryParts(db, "certifications", cert);
    expect(p.dateFrom).toBe(`Mar${NBSP}2020`);
    expect(p.dateTo).toBe("");
  });
});

describe.skip("skillGroups — proficiency in the space the cloud freed", () => {
  it("qualifies technical and domain skills", () => {
    expect(
      skillGroups(db, [
        { id: "skill:1", label: "Kubernetes", relevance_score: null },
        { id: "skill:3", label: "Logistics", relevance_score: null },
      ]),
    ).toEqual([
      { label: "Technical", names: "Kubernetes (expert)" },
      { label: "Domain", names: "Logistics (intermediate)" },
    ]);
  });

  it("leaves soft skills unqualified", () => {
    expect(
      skillGroups(db, [{ id: "skill:2", label: "Teamwork", relevance_score: null }]),
    ).toEqual([{ label: "Soft", names: "Teamwork" }]);
  });

  it("still falls back to the stored label without a DB row", () => {
    expect(
      skillGroups(undefined, [{ id: "skill:9", label: "Rust", relevance_score: null }]),
    ).toEqual([{ label: "Other", names: "Rust" }]);
  });
});

describe.skip("the skills are moved, not deleted", () => {
  it("joinedContent resolves them into skill_names for the hidden layer", () => {
    const out = joinedContent({ jobs: [job] }, db) as Record<
      string,
      { skill_names?: string }[]
    >;
    expect(out.jobs[0].skill_names).toBe("Kubernetes");
  });

  it("omits the key entirely for an entry that has no skills", () => {
    const out = joinedContent({ certifications: [cert] }, db) as Record<
      string,
      { skill_names?: string }[]
    >;
    expect(out.certifications[0]).not.toHaveProperty("skill_names");
  });

  it("markdown keeps them — it has no hidden layer to hide them in", () => {
    expect(cvToMarkdown("Ada", { jobs: [job] }, db)).toContain("Kubernetes");
  });
});

describe.skip("CV render", () => {
  async function render(content: CvContent) {
    const buf = await renderToBuffer(
      CvDocument({
        spec: FALLBACK_SPEC,
        name: "Ada Lovelace",
        content,
        db,
      } as Parameters<typeof CvDocument>[0]),
    );
    return { text: flat(pdfTextRuns(buf)), buf };
  }

  it(
    "prints the heading and description but not the job's skill cloud",
    async () => {
      const { text } = await render({ jobs: [job] });
      expect(text).toContain(flat("Senior Dev"));
      expect(text).toContain(flat("Ran the platform."));
      expect(text).not.toContain(flat("Kubernetes"));
    },
    30_000,
  );

  it(
    "renders bullets as bullets, with the bold markers gone",
    async () => {
      const { text } = await render({ jobs: [job] });
      expect(text).toContain("•");
      expect(text).toContain(flat("migrated the legacy cluster"));
      expect(text).not.toContain("**");
    },
    30_000,
  );

  it(
    "suppresses the page footer on a single-page CV",
    async () => {
      const { text, buf } = await render({ jobs: [job] });
      expect(countPdfPages(buf.toString("latin1"))).toBe(1);
      expect(text).not.toContain(flat("page 1 of 1"));
    },
    30_000,
  );
});

/**
 * Two levels of entry detail: the top N of a section carry their description, the rest are
 * one-liners. This is how a CV gets written by hand — two jobs described, the rest listed —
 * and it gives the page fit a middle gear between "keep in full" and "delete".
 */
describe.skip("entryDetail", () => {
  const detailed = { jobs: 2 };
  const plain = (id: string) => ({ id, label: id, relevance_score: null });

  it("gives the layout's budget to the top of the section, by rank", () => {
    expect(entryDetail(plain("job:1"), 0, "jobs", detailed)).toBe("full");
    expect(entryDetail(plain("job:2"), 1, "jobs", detailed)).toBe("full");
    expect(entryDetail(plain("job:3"), 2, "jobs", detailed)).toBe("compact");
  });

  it("makes a section with no budget compact throughout", () => {
    expect(entryDetail(plain("skill:1"), 0, "skills", detailed)).toBe("compact");
  });

  it("lets the page fit demote an entry rank would have kept full", () => {
    expect(
      entryDetail(plain("job:2"), 1, "jobs", detailed, new Set(["job:2"])),
    ).toBe("compact");
  });

  it("lets the user override the fit, in both directions", () => {
    // A top-ranked entry the user wants as a one-liner…
    expect(
      entryDetail({ ...plain("job:1"), detail: "compact" }, 0, "jobs", detailed),
    ).toBe("compact");
    // …and a deep one they want described anyway, even after a fit demotion.
    expect(
      entryDetail(
        { ...plain("job:9"), detail: "full" },
        8,
        "jobs",
        detailed,
        new Set(["job:9"]),
      ),
    ).toBe("full");
  });

  it("keeps at least one detailed entry per section", () => {
    expect(MIN_DETAILED).toBeGreaterThanOrEqual(1);
  });
});

describe.skip("CV render — detail levels", () => {
  it(
    "prints every job's heading but only the budgeted descriptions",
    async () => {
      const second = {
        ...db.jobs[0],
        id: 13,
        title: "Junior Dev",
        company: "Initech",
        description: "Sorted the mail.",
      };
      const twoJobs = { ...db, jobs: [db.jobs[0], second] } as CvEntriesResponse;
      const buf = await renderToBuffer(
        CvDocument({
          spec: {
            ...FALLBACK_SPEC,
            cv: { ...FALLBACK_SPEC.cv, detailed: { jobs: 1 } },
          },
          name: "Ada Lovelace",
          content: {
            jobs: [job, { id: "job:13", label: "Junior Dev", relevance_score: null }],
          },
          db: twoJobs,
        } as Parameters<typeof CvDocument>[0]),
      );
      const text = flat(pdfTextRuns(buf));
      // Both positions visible — no gap in the timeline…
      expect(text).toContain(flat("Senior Dev"));
      expect(text).toContain(flat("Junior Dev"));
      // …but only the first one is described.
      expect(text).toContain(flat("Ran the platform."));
      expect(text).not.toContain(flat("Sorted the mail."));
    },
    30_000,
  );
});
