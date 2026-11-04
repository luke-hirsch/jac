import { describe, expect, it } from "vitest";
import {
  PAGE_ASPECT,
  fitNotices,
  previewSrc,
  supportsInlinePdf,
  totalPages,
  type BuiltPdf,
} from "@/lib/render/preview";
import type { PreflightResult } from "@/lib/render/fit";

/**
 * `[frontend]-pdf-preview-shell`: the pure half of the preview overlay.
 *
 * The point of this module is that the export card's toasts and the overlay's footer read
 * the same notice list; the wording assertions below are the existing toast strings, so a
 * silent reword in one place fails here instead of drifting apart unnoticed.
 *
 * `[frontend]-fit-preflight` widens what a fit can report: it now also *shortens* entries
 * (the demote rung) and *adds* entries back to spend leftover page space. Both are things
 * the reader must be told about — a description silently missing from the PDF is exactly
 * the kind of surprise this module exists to prevent.
 */

const blob = new Blob(["%PDF-1.3"], { type: "application/pdf" });

function fit(over: Partial<PreflightResult> = {}): PreflightResult {
  return {
    content: {},
    demotedIds: [],
    droppedIds: [],
    addedIds: [],
    cutIds: [],
    pages: 1,
    fits: true,
    ...over,
  };
}

function built(over: Partial<BuiltPdf> = {}): BuiltPdf {
  return { blob, fit: fit(), letterPages: null, ...over };
}

describe("fitNotices", () => {
  it("says nothing when the export is exactly what the editor shows", () => {
    expect(fitNotices(built(), 1)).toEqual([]);
  });

  it("warns when even the minimum content overflows the layout", () => {
    const n = fitNotices(built({ fit: fit({ fits: false, pages: 2 }) }), 1);
    expect(n).toEqual([
      {
        level: "warning",
        text: "The CV overflows the layout even at minimum content.",
      },
    ]);
  });

  it("reports a single dropped entry in the singular", () => {
    const n = fitNotices(built({ fit: fit({ droppedIds: ["job:3"] }) }), 1);
    expect(n).toHaveLength(1);
    expect(n[0].level).toBe("info");
    expect(n[0].text).toBe(
      "1 lowest-ranked entry was dropped to fit 1 page(s). " +
        "Deselect or reorder to override.",
    );
  });

  it("reports several dropped entries in the plural, against the page budget", () => {
    const n = fitNotices(
      built({ fit: fit({ droppedIds: ["job:3", "skill:9", "project:1"] }) }),
      2,
    );
    expect(n[0].text).toBe(
      "3 lowest-ranked entries were dropped to fit 2 page(s). " +
        "Deselect or reorder to override.",
    );
  });

  it("does not add a drop notice on top of an overflow warning", () => {
    // Mutually exclusive by construction: an unfittable CV dropped *everything*, so
    // quoting a drop count on top of it is noise.
    const n = fitNotices(
      built({ fit: fit({ fits: false, droppedIds: ["job:1", "job:2"] }) }),
      1,
    );
    expect(n).toHaveLength(1);
    expect(n[0].level).toBe("warning");
  });

  it("reports shortened entries — a description silently gone is a surprise", () => {
    const n = fitNotices(built({ fit: fit({ demotedIds: ["job:3"] }) }), 1);
    expect(n).toHaveLength(1);
    expect(n[0].level).toBe("info");
    expect(n[0].text).toBe(
      "1 entry was shortened to its heading to fit 1 page(s).",
    );
  });

  it("reports shortened and dropped entries side by side, shortest cut first", () => {
    const n = fitNotices(
      built({
        fit: fit({ demotedIds: ["job:3", "job:2"], droppedIds: ["job:4"] }),
      }),
      1,
    );
    expect(n.map((x) => x.text)).toEqual([
      "1 lowest-ranked entry was dropped to fit 1 page(s). " +
        "Deselect or reorder to override.",
      "2 entries were shortened to their heading to fit 1 page(s).",
    ]);
  });

  it("reports the entries the fit added to spend the leftover space", () => {
    const n = fitNotices(built({ fit: fit({ addedIds: ["job:4", "job:5"] }) }), 1);
    expect(n).toHaveLength(1);
    expect(n[0].level).toBe("info");
    expect(n[0].text).toBe("2 extra entries were added to fill 1 page(s).");
  });

  it("stays quiet about the template cap — that is a standing budget, not news", () => {
    // `cutIds` is the editor's business (a per-entry badge on the exact rows it
    // concerns). In a send-time toast it would fire on nearly every export, and the
    // machine layer already lists those entries as cut_for_space.
    expect(fitNotices(built({ fit: fit({ cutIds: ["job:8", "job:9"] }) }), 1)).toEqual(
      [],
    );
  });

  it("says nothing about additions on an overflowing CV — they never happen there", () => {
    const n = fitNotices(
      built({ fit: fit({ fits: false, demotedIds: ["job:2"] }) }),
      1,
    );
    expect(n).toHaveLength(1);
    expect(n[0].level).toBe("warning");
  });

  it("warns about a cover letter that spills onto a second page", () => {
    const n = fitNotices(built({ fit: null, letterPages: 2 }), 1);
    expect(n).toEqual([
      {
        level: "warning",
        text: "The cover letter exceeds one page — shorten the body.",
      },
    ]);
  });

  it("stays quiet about a one-page letter", () => {
    expect(fitNotices(built({ fit: null, letterPages: 1 }), 1)).toEqual([]);
  });

  it("reports CV and letter problems together, CV first", () => {
    const n = fitNotices(
      built({ fit: fit({ fits: false }), letterPages: 3 }),
      1,
    );
    expect(n.map((x) => x.level)).toEqual(["warning", "warning"]);
    expect(n[0].text).toContain("CV overflows");
    expect(n[1].text).toContain("cover letter exceeds");
  });
});

describe("totalPages", () => {
  it("sums the CV and letter halves of a complete document", () => {
    expect(totalPages(built({ fit: fit({ pages: 2 }), letterPages: 1 }))).toBe(3);
  });

  it("tolerates a missing half (cv-only / letter-only scopes)", () => {
    expect(totalPages(built({ fit: fit({ pages: 2 }), letterPages: null }))).toBe(2);
    expect(totalPages(built({ fit: null, letterPages: 1 }))).toBe(1);
  });
});

describe("previewSrc", () => {
  it("asks the built-in viewer to fit the width, with no chrome", () => {
    const src = previewSrc("blob:http://app.localhost/abc-123");
    expect(src).toContain("#view=FitH");
    expect(src).toContain("toolbar=0");
    expect(src).toContain("navpanes=0");
    expect(src.startsWith("blob:http://app.localhost/abc-123#")).toBe(true);
  });

  it("is idempotent — re-rendering never stacks fragments", () => {
    const once = previewSrc("blob:http://app.localhost/abc-123");
    expect(previewSrc(once)).toBe(once);
    expect(once.match(/#/g)).toHaveLength(1);
  });
});

describe("supportsInlinePdf", () => {
  const IOS =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " +
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
  const IPAD =
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " +
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
  const MAC_SAFARI =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 " +
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15";
  const CHROME =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
  const FIREFOX =
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0";

  it("refuses the iframe on iOS, where it silently paints nothing", () => {
    expect(supportsInlinePdf(IOS)).toBe(false);
    expect(supportsInlinePdf(IPAD)).toBe(false);
  });

  it("uses the inline viewer everywhere else", () => {
    expect(supportsInlinePdf(MAC_SAFARI)).toBe(true);
    expect(supportsInlinePdf(CHROME)).toBe(true);
    expect(supportsInlinePdf(FIREFOX)).toBe(true);
  });
});

describe("PAGE_ASPECT", () => {
  it("is the A4 short/long ratio the overlay's width calc hardcodes as 0.7071", () => {
    expect(PAGE_ASPECT).toBeCloseTo(0.7071, 4);
  });
});
