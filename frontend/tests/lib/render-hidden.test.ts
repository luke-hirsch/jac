import { describe, it, expect } from "vitest";
import {
  docMetadata,
  hiddenPayload,
  HIDDEN_DELIMITER,
  MACHINE_GREETING,
} from "@/lib/render/hidden";
import { joinedContent, type ExportScope } from "@/lib/export";
import type { CvContent } from "@/lib/cv-doc";
import type { LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";

/**
 * The PDF's machine-readable layer (guide [frontend]-pdf-hidden-layer), pure builders.
 * hiddenPayload = greeting + delimiter + minified JSON: the fitted CV, the entries the
 * cap/page-fit cut (cut_for_space = full minus fitted), the letter on letter-bearing
 * scopes — all \uXXXX-escaped to ASCII so the WinAnsi standard fonts can always encode
 * it. docMetadata = the info dictionary (ASCII title, keywords = on-page skills).
 * joinedContent is the shape shared with exportJson (refactor guard lives here).
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
    {
      id: 2,
      name: "TypeScript",
      proficiency: "advanced",
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
      favourite: false,
    },
    {
      id: 13,
      title: "Junior Dev",
      company: "Züri ★ Labs", // non-WinAnsi glyphs — must come out ASCII-escaped
      started: "2019-01-01",
      ended: "2020-12-31",
      skills: [2],
      description: "Learned the ropes.",
      favourite: false,
    },
  ],
  educations: [],
  certifications: [],
  projects: [],
  languages: [],
} as unknown as CvEntriesResponse;

// full = active content before cap/fit; fitted = what the pages show (job:13 was cut).
const full: CvContent = {
  jobs: [
    { id: "job:12", label: "Senior Dev at ACME", relevance_score: 0.9 },
    { id: "job:13", label: "Junior Dev at Züri ★ Labs", relevance_score: 0.4 },
  ],
  skills: [
    { id: "skill:1", label: "Python (expert, technical)", relevance_score: null },
    { id: "skill:2", label: "TypeScript (advanced, technical)", relevance_score: null },
  ],
};
const fitted: CvContent = { jobs: [full.jobs[0]], skills: full.skills };

const meta: LetterMeta = {
  language: "de",
  subject: "Bewerbung als Dev",
  salutation: "Sehr geehrte Frau Doe,",
  date: "2026-07-11",
  closing: "Mit freundlichen Grüßen,",
  sender: { name: "Ada Lovelace", city: "Berlin" },
  recipient: { company: "ACME" },
};

const args = { fitted, full, meta, body: "Body.", db };

/** The JSON after the delimiter line — the part a machine is meant to parse. */
function payloadJson(text: string) {
  const marker = `${HIDDEN_DELIMITER}\n`;
  const idx = text.indexOf(marker);
  expect(idx).toBeGreaterThan(-1);
  return JSON.parse(text.slice(idx + marker.length));
}

describe("hiddenPayload", () => {
  it("opens with the greeting, then the delimiter, then valid minified JSON", () => {
    const text = hiddenPayload("complete", args);
    expect(text.startsWith(`${MACHINE_GREETING}\n${HIDDEN_DELIMITER}\n`)).toBe(
      true,
    );
    const json = text.slice(
      text.indexOf(HIDDEN_DELIMITER) + HIDDEN_DELIMITER.length + 1,
    );
    expect(json).not.toContain("\n"); // minified — "strip newlines" reconstructs it
    expect(() => JSON.parse(json)).not.toThrow();
  });

  it("scopes like exportJson: cv / letter / complete", () => {
    expect(Object.keys(payloadJson(hiddenPayload("cv", args)))).toEqual([
      "cv",
      "cut_for_space",
    ]);
    expect(Object.keys(payloadJson(hiddenPayload("letter", args)))).toEqual([
      "letter",
    ]);
    expect(Object.keys(payloadJson(hiddenPayload("complete", args)))).toEqual([
      "cv",
      "cut_for_space",
      "letter",
    ]);
  });

  it("cut_for_space = full minus fitted, joined against the career DB", () => {
    const parsed = payloadJson(hiddenPayload("cv", args));
    expect(parsed.cv.jobs.map((e: { id: string }) => e.id)).toEqual(["job:12"]);
    expect(
      parsed.cut_for_space.jobs.map((e: { id: string }) => e.id),
    ).toEqual(["job:13"]);
    expect(parsed.cut_for_space.jobs[0].entry.company).toBe("Züri ★ Labs");
    expect(parsed.cut_for_space.skills).toEqual([]); // nothing cut there
  });

  it("omits cut_for_space entirely when nothing was cut", () => {
    const parsed = payloadJson(
      hiddenPayload("complete", { ...args, fitted: full }),
    );
    expect(parsed).not.toHaveProperty("cut_for_space");
  });

  it("carries the letter meta + body verbatim on letter-bearing scopes", () => {
    const parsed = payloadJson(hiddenPayload("letter", args));
    expect(parsed.letter).toEqual({ meta, body: "Body." });
    expect(parsed).not.toHaveProperty("cv");
  });

  it("is pure ASCII end to end, and the escapes round-trip", () => {
    const text = hiddenPayload("complete", args);
    // Umlauts and ★ went in (labels, rows, closing) — only \n + printable ASCII come out.
    expect(/^[\n\x20-\x7e]*$/.test(text)).toBe(true);
    const parsed = payloadJson(text);
    expect(parsed.cut_for_space.jobs[0].entry.company).toBe("Züri ★ Labs");
    expect(parsed.letter.meta.closing).toBe("Mit freundlichen Grüßen,");
  });
});

describe("docMetadata", () => {
  const margs = {
    name: "Ada Lovelace",
    subject: "Bewerbung als Dev",
    content: fitted,
    db,
  };

  it("titles per scope with a plain ASCII hyphen", () => {
    const titles: Record<ExportScope, string> = {
      complete: "Ada Lovelace - Application",
      cv: "Ada Lovelace - CV",
      letter: "Ada Lovelace - Cover letter",
    };
    for (const scope of Object.keys(titles) as ExportScope[]) {
      expect(docMetadata(scope, margs).title).toBe(titles[scope]);
    }
  });

  it("carries author + subject, keywords = the skills actually on the page", () => {
    const m = docMetadata("cv", margs);
    expect(m.author).toBe("Ada Lovelace");
    expect(m.subject).toBe("Bewerbung als Dev");
    expect(m.keywords).toBe("Python, TypeScript");
    expect(m.creator).toBe("jac");
  });

  it("has no keywords without on-page skills (letter scope passes {})", () => {
    expect(docMetadata("letter", { ...margs, content: {} }).keywords).toBe("");
  });
});

describe("joinedContent (exportJson refactor guard)", () => {
  it("emits every section, joining rows and null-ing deleted ones", () => {
    const out = joinedContent(
      {
        jobs: [
          full.jobs[0],
          { id: "job:99", label: "a deleted job", relevance_score: null },
        ],
      },
      db,
    ) as Record<string, { id: string; entry: { company?: string } | null }[]>;
    expect(Object.keys(out)).toEqual([
      "jobs",
      "educations",
      "projects",
      "skills",
      "certifications",
      "languages",
    ]);
    expect(out.jobs[0].entry?.company).toBe("ACME");
    expect(out.jobs[1].entry).toBeNull();
    expect(out.skills).toEqual([]);
  });
});
