import { beforeAll, describe, expect, it } from "vitest";
import { renderToBuffer } from "@react-pdf/renderer";
import type { CvContent } from "@/lib/cv-doc";
import { emptyLetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { countPdfPages } from "@/lib/render/fit";
import { docMetadata, hiddenPayload, HIDDEN_DELIMITER } from "@/lib/render/hidden";
import { FALLBACK_SPEC } from "@/lib/render/spec";
import { CvDocument } from "@/lib/render/templates";
import { flat, infoField, pdfTextRuns } from "./_pdf-text";

/**
 * The hidden layer's physical properties, on a real render (guide
 * [frontend]-pdf-hidden-layer) — the suite's first node-side react-pdf run:
 *
 * 1. the invisible-ink payload is extractable from the (Flate-compressed) content
 *    streams — what pdftotext/ATS/LLM screeners will read;
 * 2. the page count is untouched even by a jumbo payload (the block is absolutely
 *    positioned — zero layout impact, so the fit loop's measurement stays honest);
 * 3. the metadata lands literally in the uncompressed info dictionary.
 *
 * If renderToBuffer or the stream parsing misbehaves under vitest, adapt and log the
 * deviation in the guide's Results.
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
    {
      id: 13,
      title: "Junior Dev",
      company: "Initech",
      started: "2019-01-01",
      ended: "2020-12-31",
      skills: [],
      description: "Learned the ropes.",
      favourite: false,
    },
  ],
  educations: [],
  certifications: [],
  projects: [],
  languages: [],
} as unknown as CvEntriesResponse;

const fitted: CvContent = {
  jobs: [{ id: "job:12", label: "Senior Dev at ACME", relevance_score: 0.9 }],
  skills: [
    { id: "skill:1", label: "Python (expert, technical)", relevance_score: null },
  ],
};
const full: CvContent = {
  jobs: [
    ...fitted.jobs,
    { id: "job:13", label: "Junior Dev at Initech", relevance_score: 0.4 },
  ],
  skills: fitted.skills,
};

const hidden = hiddenPayload("cv", {
  fitted,
  full,
  meta: emptyLetterMeta(),
  body: "",
  db,
});
const docMeta = docMetadata("cv", {
  name: "Ada Lovelace",
  subject: "Backend Engineer",
  content: fitted,
  db,
});

const render = (extra: Record<string, unknown>) =>
  renderToBuffer(
    CvDocument({
      spec: FALLBACK_SPEC,
      name: "Ada Lovelace",
      content: fitted,
      db,
      ...extra,
    } as Parameters<typeof CvDocument>[0]),
  );

// pdfTextRuns / infoField / flat live in ./_pdf-text — shared with the density tests.

let plain: Buffer;
let inked: Buffer;
let jumbo: Buffer;

beforeAll(async () => {
  plain = await render({});
  inked = await render({ hidden, docMeta });
  jumbo = await render({ hidden: "lorem ipsum ".repeat(2500) }); // ~30k chars of ink
}, 30_000);

describe("invisible ink in a rendered PDF", () => {
  it("lands in the content streams where text extraction reads it", () => {
    const text = flat(pdfTextRuns(inked));
    expect(text).toContain(flat(HIDDEN_DELIMITER));
    expect(text).toContain('"cut_for_space"'); // the machine-only overflow made it in
    expect(text).toContain(flat("Junior Dev at Initech")); // …with the cut entry
  });

  it("is absent from a render without the payload (extraction sanity check)", () => {
    const text = flat(pdfTextRuns(plain));
    expect(text).toContain(flat("Ada Lovelace")); // extraction itself works
    expect(text).not.toContain(flat(HIDDEN_DELIMITER));
  });

  it("never changes the page count, even at jumbo size", () => {
    const pages = countPdfPages(plain.toString("latin1"));
    expect(pages).toBeGreaterThanOrEqual(1);
    expect(countPdfPages(inked.toString("latin1"))).toBe(pages);
    expect(countPdfPages(jumbo.toString("latin1"))).toBe(pages);
  });
});

describe("empty-trailing-page regression (cv-density Results)", () => {
  // react-pdf gives a trailing absolute element a blank page of its own when the
  // wrapped flow content ends near the page bottom — Lukas's "one-page layout
  // appends one empty page". Sweep across the page-1→2 boundary (n≈20 for this
  // fixture): the ink must never change the count, exactly at the boundary too.
  const filler = (n: number) => {
    const jobs = Array.from({ length: n }, (_, i) => ({
      id: i + 1,
      title: `Role ${i + 1}`,
      company: `Company ${i + 1}`,
      started: "2020-01-01",
      ended: null,
      skills: [],
      description:
        "Built and maintained a mid-sized service landscape with reviews, on-call and mentoring duties across two teams.",
      favourite: false,
    }));
    return {
      fdb: { ...db, jobs } as unknown as CvEntriesResponse,
      content: {
        jobs: jobs.map((j) => ({
          id: `job:${j.id}`,
          label: `${j.title} at ${j.company}`,
          relevance_score: 0.5,
        })),
      } as CvContent,
    };
  };

  it("ink adds no page even when the flow ends near the page bottom", async () => {
    for (let n = 16; n <= 24; n += 2) {
      const { fdb, content } = filler(n);
      const base = { spec: FALLBACK_SPEC, name: "Ada", content, db: fdb };
      const plain = await renderToBuffer(
        CvDocument(base as Parameters<typeof CvDocument>[0]),
      );
      const inked = await renderToBuffer(
        CvDocument({
          ...base,
          hidden: "payload ".repeat(200),
        } as Parameters<typeof CvDocument>[0]),
      );
      expect(
        countPdfPages(inked.toString("latin1")),
        `n=${n} jobs`,
      ).toBe(countPdfPages(plain.toString("latin1")));
    }
  }, 60_000);
});

describe("info dictionary", () => {
  it("carries the metadata as plain ASCII literals", () => {
    const latin1 = inked.toString("latin1");
    expect(infoField(latin1, "Title")).toBe("Ada Lovelace - CV");
    expect(infoField(latin1, "Author")).toBe("Ada Lovelace");
    expect(infoField(latin1, "Subject")).toBe("Backend Engineer");
    expect(infoField(latin1, "Keywords")).toBe("Python");
    expect(infoField(latin1, "Creator")).toBe("jac");
  });
});
