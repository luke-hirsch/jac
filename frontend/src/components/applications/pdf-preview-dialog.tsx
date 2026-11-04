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
