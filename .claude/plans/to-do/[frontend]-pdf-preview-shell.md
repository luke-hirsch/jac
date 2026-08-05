# [frontend] PDF preview shell

> Roadmap: **UI polish phase, item 1** — "pdf preview: its just css stuff … there is a ton of
> whitespace inside the overlay. and the overlay is very slim."
> Branch: `frontend/pdf-preview-shell`

## Context / goal

The preview dialog renders the real export blob in an `<iframe>`, but the shell around it is wrong
in three ways:

1. **Dead height.** `DialogContent` is `grid gap-6 p-6` (`components/ui/dialog.tsx:60`). The route
   passes `className="h-[85vh] max-w-4xl"`, and the iframe asks for `h-full`. `h-full` on a _grid
   item_ resolves against its grid row, not against the dialog — so the iframe gets an
   intrinsic-ish height while the dialog keeps its 85vh, and the leftover becomes the whitespace
   band in `pdf_preview1.png`. Fix: flex column + `min-h-0` on the growing child, `p-0`.
2. **Wrong proportions.** `max-w-4xl` (56rem) with an 85vh height is a landscape box holding a
   portrait page. Fix: size the dialog _to the A4 aspect_ (`0.7071`), clamped to the viewport, and
   ask the built-in viewer to fit the page width (`#view=FitH`) so the page fills the frame.
3. **No feedback.** `onPreview()` (`export-card.tsx:245`) builds a `BuiltPdf` — page count, dropped
   entries, letter overflow — and throws all of it away. `notify()` is only called from
   `onDownloadPdf`, so the preview, which is exactly where you'd want to see "3 entries were
   dropped", says nothing.

Outcome: a preview overlay shaped like the page it shows, with the fit result on screen and a
Download button so preview → download is one flow, plus an iOS fallback (mobile Safari refuses to
render PDFs in an iframe — today that's a blank white box with no explanation).

The fit/notice logic moves into a pure module so the toasts and the preview footer read from **one**
source and can't drift apart.

## Affected files

| path                                                          | why                                                                                    |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `frontend/src/lib/render/preview.ts`                          | **new** — pure preview helpers: viewer URL params, notice list, inline-PDF capability. |
| `frontend/src/components/applications/pdf-preview-dialog.tsx` | **new** — the overlay itself, extracted from the export card.                          |
| `frontend/src/components/applications/export-card.tsx`        | use the dialog + notices; `notify()` becomes a thin toast mapper over `fitNotices`.    |
| `frontend/tests/lib/render-preview.test.ts`                   | **new** — acceptance tests (AI-written, red first).                                    |

## The code

### 1. `frontend/src/lib/render/preview.ts` (new)

```ts
/**
 * Pure helpers behind the PDF preview overlay. Kept out of the component so the export
 * card's toasts and the overlay's footer render the SAME notice list — they used to be two
 * places (the toasts in export-card, nothing at all in the preview), which is how the
 * preview ended up silent about dropped entries.
 */
import type { FitResult } from "./fit";

/** A4 short/long side. The overlay is sized to this so the page fills the frame with
 *  `#view=FitH` instead of floating in a landscape box. */
export const PAGE_ASPECT = 210 / 297;

export type Notice = { level: "warning" | "info"; text: string };

export type BuiltPdf = {
  blob: Blob;
  fit: FitResult | null; // null for letter-only
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
  } else if (built.fit && built.fit.droppedIds.length > 0) {
    const n = built.fit.droppedIds.length;
    out.push({
      level: "info",
      text:
        `${n} lowest-ranked entr${n === 1 ? "y was" : "ies were"} dropped to fit ` +
        `${pageBudget} page(s). Deselect or reorder to override.`,
    });
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
```

### 2. `frontend/src/components/applications/pdf-preview-dialog.tsx` (new)

```tsx
/**
 * The preview overlay. Two layout rules do the real work and are easy to undo by accident:
 *
 *  - `flex` + `min-h-0` on the viewer wrapper. DialogContent ships as `grid gap-6 p-6`, and a
 *    `h-full` iframe inside a grid row measures against the row, not the dialog — that was the
 *    whitespace band. tailwind-merge lets the classes below win (display/gap/padding conflicts).
 *  - the width is derived from the height at the A4 aspect, so the frame IS the page shape.
 */
import { Download, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ExportScope } from "@/lib/export";
import {
  fitNotices,
  previewSrc,
  supportsInlinePdf,
  totalPages,
  type BuiltPdf,
} from "@/lib/render/preview";

export function PdfPreviewDialog({
  open,
  url,
  built,
  scope,
  pageBudget,
  onClose,
  onDownload,
}: {
  open: boolean;
  url: string | null;
  built: BuiltPdf | null;
  scope: ExportScope;
  pageBudget: number;
  onClose: () => void;
  onDownload: () => void;
}) {
  const inline =
    typeof navigator === "undefined" || supportsInlinePdf(navigator.userAgent);
  const notices = built ? fitNotices(built, pageBudget) : [];
  const pages = built ? totalPages(built) : 0;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      {/* h → w at the page aspect; 5.5rem is the header+footer chrome. */}
      <DialogContent
        className="flex h-[92dvh] w-[min(96vw,calc((92dvh-5.5rem)*0.7071))] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none"
        showCloseButton={false}
      >
        <DialogHeader className="flex flex-row items-center justify-between gap-2 border-b px-4 py-2.5">
          <DialogTitle className="text-sm">
            PDF preview — {scope}
            {pages > 0 && (
              <span className="ml-2 font-sans text-xs font-normal tracking-normal normal-case text-muted-foreground">
                {pages} page{pages === 1 ? "" : "s"}
              </span>
            )}
          </DialogTitle>
          <div className="flex items-center gap-1">
            {url && (
              <Button variant="ghost" size="icon-sm" asChild>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  title="Open in a new tab"
                >
                  <ExternalLink />
                </a>
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onDownload}
              title="Download"
            >
              <Download />
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          </div>
        </DialogHeader>

        {/* min-h-0 is load-bearing: without it the flex child refuses to shrink and the
            iframe pushes the dialog's own scrollbar instead of filling the frame. */}
        <div className="min-h-0 flex-1 bg-muted">
          {url && inline ? (
            <iframe
              src={previewSrc(url)}
              title="PDF preview"
              className="h-full w-full border-0"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-muted-foreground">
              <p>This browser won't show PDFs inline.</p>
              {url && (
                <Button size="sm" asChild>
                  <a href={url} target="_blank" rel="noreferrer">
                    Open the PDF
                  </a>
                </Button>
              )}
            </div>
          )}
        </div>

        {notices.length > 0 && (
          <div className="space-y-1 border-t px-4 py-2">
            {notices.map((n) => (
              <p
                key={n.text}
                className={
                  n.level === "warning"
                    ? "text-xs text-destructive"
                    : "text-xs text-muted-foreground"
                }
              >
                {n.text}
              </p>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
```

### 3. `frontend/src/components/applications/export-card.tsx` (edits)

**a. imports** — drop the now-unused dialog imports, add the new ones. Replace the block at
lines 6–11 (`Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`) with nothing, and add
next to the other render imports:

```tsx
import { PdfPreviewDialog } from "@/components/applications/pdf-preview-dialog";
import { fitNotices, type BuiltPdf } from "@/lib/render/preview";
```

**b. the local `BuiltPdf` type** (lines 59–63) is deleted — it now lives in
`lib/render/preview.ts` and is imported. The shape is identical.

**c. `notify()`** (lines 288–301) becomes a mapper over the shared notice list, so the toasts and
the overlay footer can never disagree:

```tsx
function notify(built: BuiltPdf) {
  for (const n of fitNotices(built, spec.data?.cv.pages ?? 1)) {
    if (n.level === "warning") toast.warning(n.text);
    else toast.info(n.text);
  }
}
```

**d. `onPreview()`** (lines 245–252) — keep the built info for the overlay; the notices render in
the footer, so no toasts here (they'd double up the moment the user hits Download from inside):

```tsx
function onPreview() {
  if (blockedBy("pdf")) return;
  void withBusy(async () => {
    const built = await buildPdf();
    const blob = await withAttachments(built.blob);
    setPreview({ url: URL.createObjectURL(blob), info: built });
  });
}
```

(unchanged — listed so you can confirm it still compiles against the imported `BuiltPdf`.)

**e. a download that reuses the already-built blob** — new function, next to `onDownloadPdf`:

```tsx
// Download from inside the overlay: the blob is already built and merged, so this is a
// straight save — no second render pass.
function onDownloadPreview() {
  if (!preview) return;
  void fetch(preview.url)
    .then((r) => r.blob())
    .then((b) => downloadBlob(b, `${stem}.pdf`));
}
```

**f. the JSX** — replace the whole `<Dialog>…</Dialog>` block (lines 358–379) with:

```tsx
<PdfPreviewDialog
  open={preview != null}
  url={preview?.url ?? null}
  built={preview?.info ?? null}
  scope={scope}
  pageBudget={spec.data?.cv.pages ?? 1}
  onDownload={onDownloadPreview}
  onClose={() => {
    if (preview) URL.revokeObjectURL(preview.url);
    setPreview(null);
  }}
/>
```

## Tests

`frontend/tests/lib/render-preview.test.ts` — written to disk, **red** until `lib/render/preview.ts`
exists (the import fails). Covers:

- `fitNotices`: clean build → `[]`; overflow → one warning and _no_ drop notice (they're
  mutually exclusive, matching the current `else if`); 1 vs N dropped entries → singular/plural
  wording; letter > 1 page → its own warning; overflow + long letter → both, warning first.
- `previewSrc`: appends the viewer params; idempotent when a fragment is already present.
- `supportsInlinePdf`: false for iPhone/iPad UA strings, true for macOS Safari, Chrome, Firefox.
- `totalPages`: sums cv + letter, tolerates either side being null.
- `PAGE_ASPECT` is the A4 ratio (guards the `0.7071` in the className against drift).

```bash
cd frontend && npx vitest run tests/lib/render-preview.test.ts
```

Whole suite: `cd frontend && npm test`.

## Verification

1. `cd frontend && npx vitest run tests/lib/render-preview.test.ts` — red before, green after.
2. `cd frontend && npx tsc -b` — clean (the deleted `BuiltPdf` type and the dropped dialog imports
   are the likely breakage).
3. `npm run dev`, open an application with a generated CV **and** letter, scope = Complete,
   **Preview PDF**:
   - the overlay is portrait, roughly page-shaped, and the page fills it top to bottom — no
     whitespace band above or below;
   - the header shows `PDF preview — complete  ·  2 pages`;
   - if entries were dropped, the footer says so (this is the regression that guide fixes — it
     used to be silent).
4. Scope = **CV only** on an over-long CV: footer shows the drop notice, no letter warning.
   Scope = **Letter only**: no drop notice, letter warning if it spills to page 2.
5. Download from inside the overlay → the file matches the preview (same blob, attachments
   included).
6. Close the overlay, open it again 3–4 times, then check `chrome://blob-internals` (or just watch
   memory): each close revokes its URL, so nothing accumulates.
7. iPhone/responsive-mode simulation with an iOS UA: the fallback card with "Open the PDF"
   appears instead of a blank frame.

## Results

<!-- human: raw test output, observed issues, what works -->

Preview looks good for now, warnings are correctly diplayed, tests are either green or skipped, nothing red.
i will continue with next guide
