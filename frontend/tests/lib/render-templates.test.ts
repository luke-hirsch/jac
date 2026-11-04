import { beforeAll, describe, expect, it } from "vitest";
import { renderToBuffer } from "@react-pdf/renderer";
import type { CvContent } from "@/lib/cv-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { FALLBACK_SPEC, type LayoutSpec } from "@/lib/render/spec";
import { CvDocument, cvStyles, pdfPages } from "@/lib/render/templates";
import { qrDataUrl } from "@/lib/portfolio/qr";
import { flat, pdfPositionedRuns, pdfTextRuns, runAt } from "./_pdf-text";

/**
 * `[frontend]-cv-density`: the Compact-9pt decision, pinned in the one artifact that
 * renders it — cvStyles — plus the header order swap (name → bio → contact) verified on
 * a real render. Numbers are multipliers of base_pt so custom layouts scale with their
 * own base.
 */

const spec9: LayoutSpec = {
  ...FALLBACK_SPEC,
  font: { ...FALLBACK_SPEC.font, base_pt: 9 },
};

describe("cvStyles density (Compact — 9pt)", () => {
  const s = cvStyles(spec9);

  it("entries sit 3pt apart within a section (was 6pt at base 10)", () => {
    expect(s.entry.marginBottom).toBeCloseTo(3, 1);
  });

  it("meta line (dates · skills-in-a-job) reads at ~7.5pt", () => {
    expect(s.meta.fontSize).toBeCloseTo(7.5, 1);
  });

  it("sidebar joined lines get their own compact style at ~7.5pt", () => {
    expect(s.compact.fontSize).toBeCloseTo(7.5, 1);
    expect(s.compact.marginBottom).toBeCloseTo(3, 1);
  });

  it("inter-section whitespace is preserved: bigger marginTop offsets the tighter entries", () => {
    // old gap ≈ entry 6 + marginTop 10 = 16pt; new ≈ 3 + 12.6 = 15.6pt.
    expect(s.sectionTitle.marginTop).toBeCloseTo(12.6, 1);
  });

  it("the name hugs the bio: 0.4 × base, not a full line (density Results follow-up)", () => {
    // A full 9pt below the 18pt name made the header feel disconnected from the bio.
    expect(s.name.marginBottom).toBeCloseTo(3.6, 1);
  });

  it("scales with the spec's base_pt instead of hardcoding points", () => {
    const s11 = cvStyles({
      ...FALLBACK_SPEC,
      font: { ...FALLBACK_SPEC.font, base_pt: 11 },
    });
    expect(s11.meta.fontSize).toBeGreaterThan(s.meta.fontSize);
    expect(s11.entry.marginBottom).toBeCloseTo(11 / 3, 1);
  });
});

describe("FALLBACK_SPEC mirrors the compact default layout", () => {
  it("base 9pt with the bumped sidebar budgets", () => {
    expect(FALLBACK_SPEC.font.base_pt).toBe(9);
    // [frontend]-cv-typography: the space the per-entry skill cloud gave back is spent
    // on budget — and certifications moved from the main flow into the sidebar.
    expect(FALLBACK_SPEC.cv.max_entries.skills).toBe(18);
    expect(FALLBACK_SPEC.cv.max_entries.languages).toBe(6);
    expect(FALLBACK_SPEC.cv.sections).not.toContain("certifications");
    expect(FALLBACK_SPEC.cv.sidebar.flat()).toContain("certifications");
  });
});

/**
 * `[frontend]-fit-preflight` Results round 1: certifications and languages are two short
 * blocks. Full width each, they read as more important than they are and waste the page;
 * side by side they stay visible and stop shouting. A nested array in `spec.cv.sidebar`
 * is the layout saying so.
 */
describe("side-by-side sidebar sections", () => {
  const db = {
    skills: [],
    jobs: [],
    educations: [],
    projects: [],
    certifications: [
      {
        id: 1,
        name: "Shell Scripting",
        issuer: "Udemy",
        issued_on: "2020-01-01",
        skills: [],
        description: "",
      },
      {
        id: 2,
        name: "Python Bootcamp",
        issuer: "Udemy",
        issued_on: "2019-01-01",
        skills: [],
        description: "",
      },
    ],
    languages: [
      { id: 1, name: "German", fluency: "native" },
      { id: 2, name: "English", fluency: "fluent" },
      { id: 3, name: "French", fluency: "conversational" },
    ],
  } as unknown as CvEntriesResponse;

  const entry = (id: string) => ({ id, label: id, relevance_score: null });
  const both: CvContent = {
    certifications: [entry("certification:1"), entry("certification:2")],
    languages: [entry("language:1"), entry("language:2")],
  };

  const doc = (content: CvContent, sidebar: LayoutSpec["cv"]["sidebar"]) =>
    CvDocument({
      spec: { ...spec9, cv: { ...spec9.cv, sections: [], sidebar } },
      name: "Ada Lovelace",
      content,
      db,
    } as Parameters<typeof CvDocument>[0]);

  it("puts the two headings on one line, in two columns", async () => {
    const buf = await renderToBuffer(
      doc(both, [["certifications", "languages"]]),
    );
    const runs = pdfPositionedRuns(buf);
    const certs = runAt(runs, "Certifications");
    const langs = runAt(runs, "Languages");
    expect(langs.y).toBeCloseTo(certs.y, 0); // same line
    expect(langs.x).toBeGreaterThan(certs.x + 100); // its own column
  }, 30_000);

  it("stacks a column's entries one per line instead of joining them", async () => {
    const buf = await renderToBuffer(
      doc(both, [["certifications", "languages"]]),
    );
    const runs = pdfPositionedRuns(buf);
    // Joined, both languages would paint at one y from one x.
    expect(runAt(runs, "English").y).toBeLessThan(runAt(runs, "German").y);
    expect(runAt(runs, "English").x).toBeCloseTo(runAt(runs, "German").x, 0);
  }, 30_000);

  it("qualifies each entry in its own column: issued date, spoken level", async () => {
    const buf = await renderToBuffer(
      doc(both, [["certifications", "languages"]]),
    );
    const runs = pdfPositionedRuns(buf);
    for (const [hint, name] of [
      ["Jan 2020", "Shell Scripting"],
      ["native", "German"],
      ["fluent", "English"],
    ]) {
      const h = runAt(runs, hint);
      const n = runAt(runs, name);
      expect(h.y).toBeCloseTo(n.y, 0); // same row
      expect(h.x).toBeLessThan(n.x); // qualifier left of the name
    }
    // Both columns' names start at their own column's content edge, so each column
    // reads down a clean left edge.
    expect(runAt(runs, "German").x).toBeCloseTo(runAt(runs, "English").x, 0);
  }, 30_000);

  it("keeps the longest real qualifier on one line", async () => {
    // "conversational" is the widest Language.Fluency choice — if the hint column is
    // sized to "native" it wraps, and every row below it goes crooked.
    const wide: CvContent = {
      certifications: both.certifications,
      languages: [{ id: "language:3", label: "", relevance_score: null }],
    };
    const buf = await renderToBuffer(
      doc(wide, [["certifications", "languages"]]),
    );
    const runs = pdfPositionedRuns(buf);
    const hint = runAt(runs, "conversational");
    expect(hint.y).toBeCloseTo(runAt(runs, "French").y, 0);
  }, 30_000);

  it("collapses to full width when the group has a single survivor", async () => {
    // The certifications were deselected: languages must not sit in half a page with a
    // blank column beside it.
    const only: CvContent = { languages: both.languages };
    const grouped = await renderToBuffer(
      doc(only, [["certifications", "languages"]]),
    );
    const plain = await renderToBuffer(doc(only, ["languages"]));
    expect(runAt(pdfPositionedRuns(grouped), "German")).toEqual(
      runAt(pdfPositionedRuns(plain), "German"),
    );
  }, 30_000);
});

describe("CV header order", () => {
  const db = {
    skills: [],
    jobs: [
      {
        id: 12,
        title: "Senior Dev",
        company: "ACME",
        started: "2021-01-01",
        ended: null,
        skills: [],
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
    jobs: [
      { id: "job:12", label: "Senior Dev at ACME", relevance_score: null },
    ],
  };

  let text: string;
  beforeAll(async () => {
    const buf = await renderToBuffer(
      CvDocument({
        spec: spec9,
        name: "Ada Lovelace",
        content,
        db,
        contact: "ada@example.com · +49 123",
        summary: "Bio: I turn coffee into software.",
      } as Parameters<typeof CvDocument>[0]),
    );
    text = flat(pdfTextRuns(buf));
  }, 30_000);

  it("renders name, then bio, then contact (bio/contact swapped)", () => {
    const name = text.indexOf(flat("Ada Lovelace"));
    const bio = text.indexOf(flat("Bio: I turn coffee into software."));
    const contact = text.indexOf(flat("ada@example.com"));
    expect(name).toBeGreaterThanOrEqual(0);
    expect(bio).toBeGreaterThan(name);
    expect(contact).toBeGreaterThan(bio);
  });
});

describe("portfolio QR ([fullstack]-portfolio-cv-qr)", () => {
  const db = {
    skills: [],
    jobs: [],
    educations: [],
    certifications: [],
    projects: [],
    languages: [],
  } as unknown as CvEntriesResponse;
  const content: CvContent = {};
  const url = "https://lukehirsch.com/portfolio/acme-x7f3";

  const base = () =>
    ({
      spec: spec9,
      name: "Ada Lovelace",
      content,
      db,
      contact: `ada@example.com · ${url}`,
      summary: "Bio.",
    }) as Parameters<typeof CvDocument>[0];

  // NOTE: the text-layer belt (the URL rides in the contact line) is asserted at the
  // unit level in letter-doc.test.ts — pdfTextRuns can't cleanly extract page text once
  // an image XObject (the QR) shares the stream pool, so it's not re-asserted here.

  it("renders a CvDocument carrying the QR without throwing", async () => {
    const qr = await qrDataUrl(url);
    const buf = await renderToBuffer(
      CvDocument({ ...base(), portfolio: { qr } }),
    );
    expect(buf.length).toBeGreaterThan(0);
  }, 30_000);

  it("is layout-invariant: page count matches with and without the QR block", async () => {
    const qr = await qrDataUrl(url);
    const without = await pdfPages(CvDocument(base()));
    const withQr = await pdfPages(CvDocument({ ...base(), portfolio: { qr } }));
    expect(withQr).toBe(without);
  }, 30_000);
});
