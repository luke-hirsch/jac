import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type ApplicationRow } from "@/lib/queries/applications";
import { activeContent } from "@/lib/cv-doc";
import {
  contactLine,
  fillBlanks,
  normalizeLetterMeta,
  senderFromProfile,
} from "@/lib/letter-doc";
import { useCvEntries, useFullList, type LayoutRow } from "@/lib/queries/jac";
import { useProfile } from "@/lib/queries/profile";
import { capContent, fitCv, type FitResult } from "@/lib/render/fit";
import { isFavouriteLookup } from "@/lib/render/parts";
import { useLayoutSpec } from "@/lib/render/spec";
import {
  ApplicationDocument,
  CvDocument,
  LetterDocument,
  pdfPages,
  renderPdfBlob,
} from "@/lib/render/templates";
import {
  cvToMarkdown,
  downloadBlob,
  downloadText,
  exportBlocker,
  exportJson,
  letterToMarkdown,
  type ExportFormat,
  type ExportScope,
} from "@/lib/export";

type BuiltPdf = {
  blob: Blob;
  fit: FitResult | null; // null for letter-only
  letterPages: number | null;
};

export function ExportCard({ app }: { app: ApplicationRow }) {
  const layouts = useFullList<LayoutRow>("layouts");
  const layout = layouts.data?.find((l) => l.id === app.layout);
  const spec = useLayoutSpec(layout);
  const careerDb = useCvEntries();
  const profile = useProfile();
  const [scope, setScope] = useState<ExportScope>("complete");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{
    url: string;
    info: BuiltPdf;
  } | null>(null);

  // Blank sender fields fall back to the user profile (same rule as the editor),
  // so an export never goes out with an empty sender block.
  const stored = normalizeLetterMeta(app.letter_meta);
  const meta = profile.data
    ? {
        ...stored,
        sender: fillBlanks(stored.sender, senderFromProfile(profile.data)),
      }
    : stored;
  const name = meta.sender.name || "CV";
  const stem = `application-${app.id}-${scope}`;

  async function buildPdf(): Promise<BuiltPdf> {
    if (!spec.data) throw new Error("layout spec not loaded");
    const s = spec.data;
    const db = careerDb.data;
    const socials = profile.data?.show_socials ?? false;
    const contact = contactLine(meta.sender, { socials });
    const summary = profile.data?.bio ?? "";
    // Template entry budget first (hard editorial cap), page fit second.
    const active = capContent(
      activeContent(app.cv_content ?? {}),
      s.cv.max_entries,
    );

    const fit =
      scope === "letter"
        ? null
        : await fitCv(
            active,
            s.cv.pages,
            (c) =>
              pdfPages(
                <CvDocument
                  spec={s}
                  name={name}
                  content={c}
                  db={db}
                  contact={contact}
                  summary={summary}
                />,
              ),
            isFavouriteLookup(db),
          );
    const letterPages =
      scope === "cv"
        ? null
        : await pdfPages(
            <LetterDocument spec={s} meta={meta} body={app.cover_letter} />,
          );

    const doc =
      scope === "cv" ? (
        <CvDocument
          spec={s}
          name={name}
          content={fit!.content}
          db={db}
          contact={contact}
          summary={summary}
        />
      ) : scope === "letter" ? (
        <LetterDocument spec={s} meta={meta} body={app.cover_letter} />
      ) : (
        <ApplicationDocument
          cv={{ spec: s, name, content: fit!.content, db }}
          letter={{ spec: s, meta, body: app.cover_letter }}
        />
      );
    return { blob: await renderPdfBlob(doc), fit, letterPages };
  }

  async function withBusy<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    try {
      return await fn();
    } catch {
      toast.error("Export failed");
    } finally {
      setBusy(false);
    }
  }

  // Send-time stub gate: refuses (with the reason) before any rendering happens.
  function blockedBy(format: ExportFormat): boolean {
    const reason = exportBlocker(scope, format, app.cover_letter);
    if (reason) toast.error(reason);
    return reason != null;
  }

  function onDownloadPdf() {
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const built = await buildPdf();
      downloadBlob(built.blob, `${stem}.pdf`);
      notify(built);
    });
  }

  function onPreview() {
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const built = await buildPdf();
      setPreview({ url: URL.createObjectURL(built.blob), info: built });
    });
  }

  function onDownloadMd() {
    if (blockedBy("md")) return;
    const db = careerDb.data;
    // Same template budget as the PDF (md is a sendable artefact); json stays a full dump.
    const active = spec.data
      ? capContent(
          activeContent(app.cv_content ?? {}),
          spec.data.cv.max_entries,
        )
      : activeContent(app.cv_content ?? {});
    const cvMd = cvToMarkdown(name, active, db);
    const letterMd = letterToMarkdown(meta, app.cover_letter);
    const md =
      scope === "cv"
        ? cvMd
        : scope === "letter"
          ? letterMd
          : `${letterMd}\n---\n\n${cvMd}`;
    downloadText(md, `${stem}.md`, "text/markdown");
  }

  function onDownloadJson() {
    downloadText(
      exportJson(scope, {
        content: activeContent(app.cv_content ?? {}),
        meta,
        body: app.cover_letter,
        db: careerDb.data,
      }),
      `${stem}.json`,
      "application/json",
    );
  }

  function notify(built: BuiltPdf) {
    if (built.fit && !built.fit.fits) {
      toast.warning("The CV overflows the layout even at minimum content.");
    } else if (built.fit && built.fit.droppedIds.length > 0) {
      toast.info(
        `${built.fit.droppedIds.length} lowest-ranked entr${
          built.fit.droppedIds.length === 1 ? "y was" : "ies were"
        } dropped to fit ${spec.data?.cv.pages} page(s). Deselect or reorder to override.`,
      );
    }
    if (built.letterPages != null && built.letterPages > 1) {
      toast.warning("The cover letter exceeds one page — shorten the body.");
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Export</CardTitle>
        <Badge variant="outline">{layout?.name ?? "layout…"}</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Exports the saved application content — save your edits first. The CV
          is auto-fitted to the layout's page budget by dropping the
          lowest-ranked entries; the letter is never cut, only flagged.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label className="text-xs">Scope</Label>
            <Select
              value={scope}
              onValueChange={(v) => setScope(v as ExportScope)}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="complete">Complete</SelectItem>
                <SelectItem value="cv">CV only</SelectItem>
                <SelectItem value="letter">Letter only</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button size="sm" onClick={onPreview} disabled={busy || !spec.data}>
            {busy ? "Rendering…" : "Preview PDF"}
          </Button>
          <Button
            size="sm"
            onClick={onDownloadPdf}
            disabled={busy || !spec.data}
          >
            Download PDF
          </Button>
          <Button size="sm" variant="outline" onClick={onDownloadMd}>
            Markdown
          </Button>
          <Button size="sm" variant="outline" onClick={onDownloadJson}>
            JSON
          </Button>
        </div>
      </CardContent>

      <Dialog
        open={preview != null}
        onOpenChange={(open) => {
          if (!open && preview) {
            URL.revokeObjectURL(preview.url);
            setPreview(null);
          }
        }}
      >
        <DialogContent className="h-[85vh] max-w-4xl">
          <DialogHeader>
            <DialogTitle>PDF preview — {scope}</DialogTitle>
          </DialogHeader>
          {preview && (
            <iframe
              src={preview.url}
              title="PDF preview"
              className="h-full w-full"
            />
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
