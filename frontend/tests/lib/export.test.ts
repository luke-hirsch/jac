import { describe, it, expect } from "vitest";
import { entryParts, isFavouriteLookup, skillNames } from "@/lib/render/parts";
import {
  cvToMarkdown,
  exportBlocker,
  exportJson,
  letterToMarkdown,
  type ExportFormat,
  type ExportScope,
} from "@/lib/export";
import { PERSONAL_STUB } from "@/lib/letter-doc";
import type { CvContent } from "@/lib/cv-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";

/**
 * Export builders (guide [frontend]-render-export). entryParts is the shared entry shape
 * behind the PDF templates and the markdown export; the markdown mirrors the backend
 * renderers (jac/render.py, jac/cover_letter.py render_markdown) so every format agrees.
 * exportBlocker is the send-time stub safeguard: a stubbed letter never leaves as pdf/md.
 */

const db = {
  skills: [
    {
      id: 1,
      name: "Python",
      proficiency: "expert",
      category: "technical",
      favourite: false,
    },
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
      favourite: true,
    },
  ],
  educations: [],
  certifications: [],
  projects: [],
  languages: [],
} as unknown as CvEntriesResponse;

const content: CvContent = {
  jobs: [{ id: "job:12", label: "stored label", relevance_score: 0.9 }],
  skills: [{ id: "skill:1", label: "Python (expert, technical)", relevance_score: null }],
};

const meta = {
  language: "de",
  subject: "Bewerbung als Dev",
  salutation: "Sehr geehrte Frau Doe,",
  date: "2026-07-09",
  closing: "Mit freundlichen Grüßen,",
  sender: {
    name: "Ada Lovelace",
    street: "Musterstr. 1",
    zip: "10115",
    city: "Berlin",
    email: "me@x.com",
    phone: "+49 123",
  },
  recipient: { company: "ACME", contact_name: "Jane Doe" },
};

describe("skillNames / entryParts", () => {
  it("resolves skill pks to names", () => {
    expect(skillNames(db, [1])).toBe("Python");
    expect(skillNames(db, [])).toBe("");
    expect(skillNames(undefined, [1])).toBe("");
  });

  it("splits the date into its own column, leaving skills in meta", () => {
    const p = entryParts(db, "jobs", content.jobs[0]);
    expect(p.heading).toBe("Senior Dev — ACME");
    expect(p.date).toBe("2021-01-01–present");
    expect(p.meta).toBe("Python"); // no date in meta any more
    expect(p.body).toBe("Built the pipeline.");
    expect(p.favourite).toBe(true);
  });

  it("falls back to the stored label for a row missing from the DB", () => {
    const p = entryParts(db, "jobs", {
      id: "job:99",
      label: "a deleted job",
      relevance_score: null,
    });
    expect(p).toEqual({
      heading: "a deleted job",
      date: "",
      meta: "",
      body: "",
      favourite: false,
    });
  });
});

describe("isFavouriteLookup", () => {
  it("flags favourite rows across sections by entry id", () => {
    const fav = isFavouriteLookup(db);
    expect(fav("job:12")).toBe(true);
    expect(fav("skill:1")).toBe(false);
  });

  it("is all-false without a loaded DB", () => {
    expect(isFavouriteLookup(undefined)("job:12")).toBe(false);
  });
});

describe("cvToMarkdown", () => {
  it("mirrors the backend section order and marks favourites", () => {
    const md = cvToMarkdown("Ada Lovelace", content, db);
    expect(md.startsWith("# Ada Lovelace\n")).toBe(true);
    expect(md).toContain("## Experience");
    expect(md).toContain("### ★ Senior Dev — ACME");
    expect(md).toContain("Built the pipeline.");
    expect(md).toContain("## Skills");
    expect(md.indexOf("## Experience")).toBeLessThan(md.indexOf("## Skills"));
    expect(md).not.toContain("## Education"); // empty sections omitted
    expect(md.endsWith("\n")).toBe(true);
  });
});

describe("letterToMarkdown", () => {
  it("assembles the DIN-style block order, skipping empty lines", () => {
    expect(letterToMarkdown(meta, "Body.")).toBe(
      "Ada Lovelace\n" +
        "Musterstr. 1\n" +
        "10115 Berlin\n" +
        "me@x.com · +49 123\n" +
        "\n" +
        "ACME\n" +
        "Jane Doe\n" +
        "\n" +
        "2026-07-09\n" +
        "\n" +
        "**Bewerbung als Dev**\n" +
        "\n" +
        "Sehr geehrte Frau Doe,\n" +
        "\n" +
        "Body.\n" +
        "\n" +
        "Mit freundlichen Grüßen,\n" +
        "\n" +
        "Ada Lovelace\n",
    );
  });
});

describe("exportJson", () => {
  const args = { content, meta, body: "Body.", db };

  it("scopes to cv / letter / complete", () => {
    expect(Object.keys(JSON.parse(exportJson("cv", args)))).toEqual(["cv"]);
    expect(Object.keys(JSON.parse(exportJson("letter", args)))).toEqual(["letter"]);
    expect(Object.keys(JSON.parse(exportJson("complete", args)))).toEqual([
      "cv",
      "letter",
    ]);
  });

  it("joins the career-DB row into each cv entry", () => {
    const parsed = JSON.parse(exportJson("cv", args));
    const job = parsed.cv.jobs[0];
    expect(job.id).toBe("job:12");
    expect(job.entry.company).toBe("ACME");
  });

  it("carries the letter meta + body", () => {
    const parsed = JSON.parse(exportJson("letter", args));
    expect(parsed.letter).toEqual({ meta, body: "Body." });
  });
});

describe("exportBlocker (send-time stub safeguard)", () => {
  const stubbed = `Intro.\n\n${PERSONAL_STUB}\n\nOutro.`;
  const scopes: ExportScope[] = ["complete", "cv", "letter"];
  const formats: ExportFormat[] = ["pdf", "md", "json"];

  it("blocks letter-bearing pdf/md exports while the stub is in the body", () => {
    for (const scope of ["complete", "letter"] as ExportScope[]) {
      for (const format of ["pdf", "md"] as ExportFormat[]) {
        expect(exportBlocker(scope, format, stubbed)).toMatch(/stub/);
      }
    }
  });

  it("exempts cv-only exports and json data dumps", () => {
    for (const format of formats) {
      expect(exportBlocker("cv", format, stubbed)).toBeNull();
    }
    for (const scope of scopes) {
      expect(exportBlocker(scope, "json", stubbed)).toBeNull();
    }
  });

  it("never blocks a clean body", () => {
    for (const scope of scopes) {
      for (const format of formats) {
        expect(exportBlocker(scope, format, "A finished letter.")).toBeNull();
      }
    }
  });
});
