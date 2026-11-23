import { describe, expect, it } from "vitest";
import type { CvContent } from "@/lib/cv-doc";
import { emptyLetterMeta } from "@/lib/letter-doc";
import type { ApplicationRow } from "@/lib/queries/applications";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { ProfileRow } from "@/lib/queries/profile";
import { buildPdf, exportMeta } from "@/lib/render/build";
import { countPdfPages, type PreflightResult } from "@/lib/render/fit";
import { FALLBACK_SPEC } from "@/lib/render/spec";

/**
 * The one PDF build (lib/render/build.tsx) the export card and the connector's preview in the
 * Claude app both run. Node-side react-pdf renders, as in render-hidden-pdf.test.ts.
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
      favourite: false,
    },
  ],
  educations: [],
  certifications: [],
  projects: [],
  languages: [],
} as unknown as CvEntriesResponse;

const content: CvContent = {
  jobs: [{ id: "job:12", label: "Senior Dev at ACME", relevance_score: 0.9 }],
  skills: [
    {
      id: "skill:1",
      label: "Python (expert, technical)",
      relevance_score: null,
    },
  ],
};

const profile = {
  name: "Ada Lovelace",
  email: "ada@example.org",
  phone: "",
  street: "Analytical Row 1",
  address_line2: "",
  zip: "10115",
  city: "Berlin",
  country: "",
  website: "",
  linkedin_url: "",
  github_url: "",
  show_socials: false,
  bio: "Engineer.",
  signature: "",
  accent_color: "",
} as unknown as ProfileRow;

const body =
  "Ich bewerbe mich auf die Stelle. ".repeat(12) +
  "\n\n" +
  "Mehr dazu im Gespräch. ".repeat(8);

const row = {
  id: 7,
  cv_content: content,
  sections_off: [],
  cover_letter: body,
  letter_meta: {
    ...emptyLetterMeta("de"),
    subject: "Bewerbung als Backend-Entwickler:in",
    salutation: "Sehr geehrte Damen und Herren,",
    closing: "Mit freundlichen Grüßen",
    sender: { ...emptyLetterMeta("de").sender, phone: "030 123" },
  },
  attachments: [],
} as unknown as ApplicationRow;

async function pagesOf(blob: Blob) {
  return countPdfPages(
    new TextDecoder("latin1").decode(await blob.arrayBuffer()),
  );
}

const args = { row, spec: FALLBACK_SPEC, db, profile };

describe("the shared PDF build", () => {
  it("builds the letter and the CV for a complete export", async () => {
    const built = await buildPdf({ ...args, scope: "complete" });
    expect(built.letterPages).toBe(1);
    expect(built.fit?.fits).toBe(true);
    expect(await pagesOf(built.blob)).toBe(
      built.fit!.pages + built.letterPages!,
    );
  }, 30_000);

  it("builds the CV alone, with no letter measured", async () => {
    const built = await buildPdf({ ...args, scope: "cv" });
    expect(built.letterPages).toBeNull();
    expect(await pagesOf(built.blob)).toBe(built.fit!.pages);
  }, 30_000);

  it("builds the letter alone, with no fit", async () => {
    const built = await buildPdf({ ...args, scope: "letter" });
    expect(built.fit).toBeNull();
    expect(await pagesOf(built.blob)).toBe(1);
  }, 30_000);

  it("takes the editor's measurement when there is one, instead of measuring again", async () => {
    const measured = {
      content,
      pages: 1,
      fits: true,
      droppedIds: [],
      demotedIds: [],
      addedIds: [],
      cutIds: [],
    } as unknown as PreflightResult;
    const keys: string[] = [];
    const built = await buildPdf({
      ...args,
      scope: "cv",
      cachedFit: (key) => {
        keys.push(key);
        return measured;
      },
    });
    expect(built.fit).toBe(measured);
    expect(keys).toHaveLength(1);
  }, 30_000);
});

describe("the export's letter meta", () => {
  it("fills blank sender fields from the profile and keeps what the row says", () => {
    const meta = exportMeta(row, profile);
    expect(meta.sender.name).toBe("Ada Lovelace");
    expect(meta.sender.city).toBe("Berlin");
    expect(meta.sender.phone).toBe("030 123");
  });

  it("is the row's own without a profile", () => {
    expect(exportMeta(row, undefined).sender.name).toBeFalsy();
  });
});
