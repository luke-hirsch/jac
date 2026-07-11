import { inflateSync } from "node:zlib";
import { beforeAll, describe, expect, it } from "vitest";
import { renderToBuffer } from "@react-pdf/renderer";
import type { CvContent } from "@/lib/cv-doc";
import { emptyLetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { countPdfPages } from "@/lib/render/fit";
import { docMetadata, hiddenPayload, HIDDEN_DELIMITER } from "@/lib/render/hidden";
import { FALLBACK_SPEC } from "@/lib/render/spec";
import { CvDocument } from "@/lib/render/templates";

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

/**
 * Every Flate stream inflated (raw fallback for uncompressed ones), then all `(...)`
 * string literals concatenated: kerning may split one text run into many literals with
 * positioning numbers between them, so only the concatenation is comparable — and
 * word spacing may swallow blanks entirely, hence the `flat` comparisons below.
 */
function pdfTextRuns(buf: Buffer): string {
  const latin1 = buf.toString("latin1");
  const chunks: string[] = [];
  const re = /stream\r?\n/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(latin1))) {
    const start = m.index + m[0].length;
    const end = latin1.indexOf("endstream", start);
    if (end < 0) break;
    let data = buf.subarray(start, end);
    while (
      data.length &&
      (data[data.length - 1] === 0x0a || data[data.length - 1] === 0x0d)
    ) {
      data = data.subarray(0, data.length - 1);
    }
    try {
      chunks.push(inflateSync(data).toString("latin1"));
    } catch {
      chunks.push(latin1.slice(start, end)); // not Flate — take it raw
    }
    re.lastIndex = end;
  }
  const literals = chunks.join("\n").match(/\((?:\\.|[^\\()])*\)/g) ?? [];
  return literals.map((s) => s.slice(1, -1).replace(/\\(.)/g, "$1")).join("");
}

const flat = (s: string) => s.replace(/\s+/g, "");

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

describe("info dictionary", () => {
  it("carries the metadata as plain ASCII literals", () => {
    const latin1 = inked.toString("latin1");
    expect(latin1).toContain("/Title (Ada Lovelace - CV)");
    expect(latin1).toContain("/Author (Ada Lovelace)");
    expect(latin1).toContain("/Subject (Backend Engineer)");
    expect(latin1).toContain("/Keywords (Python)");
    expect(latin1).toContain("/Creator (jac)");
  });
});
