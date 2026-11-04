/**
 * Pure helpers behind the PDF preview overlay. Kept out of the component so the export
 * card's toasts and the overlay's footer render the SAME notice list — they used to be two
 * places (the toasts in export-card, nothing at all in the preview), which is how the
 * preview ended up silent about dropped entries.
 */
import type { PreflightResult } from "./fit";

/** A4 short/long side. The overlay is sized to this so the page fills the frame with
 *  `#view=FitH` instead of floating in a landscape box. */
export const PAGE_ASPECT = 210 / 297;

export type Notice = { level: "warning" | "info"; text: string };

export type BuiltPdf = {
  blob: Blob;
  fit: PreflightResult | null; // null for letter-only
  letterPages: number | null; // null for cv-only
};

/**
 * What the render actually did to the content, worst first. Empty = the export is exactly
 * what the editor shows. Wording is the pre-existing toast wording — do not "improve" it
 * here without changing it everywhere, it is asserted in the tests.
 */
export function fitNotices(built: BuiltPdf, pageBudget: number): Notice[] {
  const out: Notice[] = [];
  if (built.fit && !built.fit.fits) {
    out.push({
      level: "warning",
      text: "The CV overflows the layout even at minimum content.",
    });
  } else if (built.fit) {
    const dropped = built.fit.droppedIds.length;
    if (dropped > 0) {
      out.push({
        level: "info",
        text:
          `${dropped} lowest-ranked entr${dropped === 1 ? "y was" : "ies were"} dropped to fit ` +
          `${pageBudget} page(s). Deselect or reorder to override.`,
      });
    }
    const shortened = built.fit.demotedIds.length;
    if (shortened > 0) {
      out.push({
        level: "info",
        text:
          `${shortened} ${shortened === 1 ? "entry was" : "entries were"} shortened to ` +
          `${shortened === 1 ? "its" : "their"} heading to fit ${pageBudget} page(s).`,
      });
    }
    const added = built.fit.addedIds.length;
    if (added > 0) {
      out.push({
        level: "info",
        text:
          `${added} extra entr${added === 1 ? "y was" : "ies were"} added to fill ` +
          `${pageBudget} page(s).`,
      });
    }
  }
  if (built.letterPages != null && built.letterPages > 1) {
    out.push({
      level: "warning",
      text: "The cover letter exceeds one page — shorten the body.",
    });
  }
  return out;
}

/** Total pages in the built document, for the overlay's status line. */
export function totalPages(built: BuiltPdf): number {
  return (built.fit?.pages ?? 0) + (built.letterPages ?? 0);
}

/**
 * Blob URL + PDF open parameters: fit the page width (the overlay is already at the page
 * aspect, so FitH fills it exactly), no toolbar, no sidebar. Idempotent — a URL that
 * already carries a fragment is returned untouched, so re-renders never stack `#`s.
 */
export function previewSrc(url: string): string {
  return url.includes("#") ? url : `${url}#view=FitH&toolbar=0&navpanes=0`;
}

/**
 * iOS (every browser on it, they are all WebKit) does not render PDFs in an iframe — it
 * paints a blank box with no error. Anything else gets the inline viewer; iOS gets the
 * "open in a new tab" fallback instead of a mystery white rectangle.
 */
export function supportsInlinePdf(ua: string): boolean {
  return !/iP(hone|od|ad)/i.test(ua) && !/Mac.*Mobile/i.test(ua);
}
