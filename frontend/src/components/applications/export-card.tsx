import { useState } from "react";
import { PdfPreviewDialog } from "@/components/applications/pdf-preview-dialog";
import { fitNotices, type BuiltPdf } from "@/lib/render/preview";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type ApplicationRow } from "@/lib/queries/applications";
import { useAttachments } from "@/lib/queries/attachments";
import { mergePdfs } from "@/lib/render/attachments";
import {
  buildPdf as buildApplicationPdf,
  exportMeta,
} from "@/lib/render/build";
import { activeContent } from "@/lib/cv-doc";
import { useCvEntries, useFullList, type LayoutRow } from "@/lib/queries/jac";
import { useProfile } from "@/lib/queries/profile";
import { capContent, effectiveCaps } from "@/lib/render/fit";
import { readPreflightCache } from "./use-preflight";
import { useRenderSpec } from "@/lib/render/spec";
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

import { PortfolioLinkSection } from "@/components/applications/portfolio-link-section";
import { type PortfolioLinkRow } from "@/lib/queries/portfolio";

export function ExportCard({
  app,
  dirty,
  onSave,
}: {
  app: ApplicationRow;
  dirty: boolean;
  onSave: () => Promise<ApplicationRow | null>;
}) {
  const layouts = useFullList<LayoutRow>("layouts");
  const layout = layouts.data?.find((l) => l.id === app.layout);
  const spec = useRenderSpec(layout);
  const careerDb = useCvEntries();
  const profile = useProfile();
  const library = useAttachments();
  const [scope, setScope] = useState<ExportScope>("complete");
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<PortfolioLinkRow | null>(null);
  const [includeQr, setIncludeQr] = useState(false);
  const [preview, setPreview] = useState<{
    url: string;
    info: BuiltPdf;
  } | null>(null);

  // Blank sender fields fall back to the user profile (same rule as the editor).
  const metaOf = (row: ApplicationRow) => exportMeta(row, profile.data);
  const stem = `application-${app.id}-${scope}`;

  /** The row an export reads: the server copy, saved first if the editor is ahead of it, so
   *  the file holds what's on screen. The save's answer, not the `app` prop: that belongs to
   *  the render that was current at the click. null = it failed (already toasted). */
  async function current(): Promise<ApplicationRow | null> {
    return dirty ? onSave() : app;
  }

  // The same build the connector's preview in the Claude app runs (lib/render/build.tsx).
  async function buildPdf(row: ApplicationRow): Promise<BuiltPdf> {
    if (!spec.data) throw new Error("layout spec not loaded");
    return buildApplicationPdf({
      row,
      spec: spec.data,
      db: careerDb.data,
      profile: profile.data,
      scope,
      portfolioUrl: includeQr && link ? link.url : undefined,
      cachedFit: (key) => readPreflightCache(key)?.result,
    });
  }

  // Client-side pdf-lib merge: resolve the application's selected attachment ids to file URLs
  // (in the stored order, stale ids skipped) and append them. Skipped for a letter-only export
  // — attachments follow the CV, not the letter.
  async function withAttachments(
    row: ApplicationRow,
    blob: Blob,
  ): Promise<Blob> {
    const byId = new Map((library.data ?? []).map((a) => [a.id, a] as const));
    const urls = (row.attachments ?? [])
      .map((id) => byId.get(id)?.file)
      .filter((u): u is string => !!u);
    return urls.length && scope !== "letter" ? mergePdfs(blob, urls) : blob;
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
  function blockedBy(format: ExportFormat, row: ApplicationRow): boolean {
    const reason = exportBlocker(scope, format, row.cover_letter);
    if (reason) toast.error(reason);
    return reason != null;
  }

  function onDownloadPdf() {
    void withBusy(async () => {
      const row = await current();
      if (!row || blockedBy("pdf", row)) return;
      const built = await buildPdf(row);
      downloadBlob(await withAttachments(row, built.blob), `${stem}.pdf`);
      notify(built);
    });
  }

  // Download from inside the overlay: the blob is already built and merged, so this is a
  // straight save — no second render pass.
  function onDownloadPreview() {
    if (!preview) return;
    void fetch(preview.url)
      .then((r) => r.blob())
      .then((b) => downloadBlob(b, `${stem}.pdf`));
  }

  function onPreview() {
    void withBusy(async () => {
      const row = await current();
      if (!row || blockedBy("pdf", row)) return;
      const built = await buildPdf(row);
      const blob = await withAttachments(row, built.blob);
      setPreview({ url: URL.createObjectURL(blob), info: built });
    });
  }

  function onDownloadMd() {
    void withBusy(async () => {
      const row = await current();
      if (!row || blockedBy("md", row)) return;
      const db = careerDb.data;
      const meta = metaOf(row);
      // Same template budget as the PDF (md is a sendable artefact); json stays a full dump.
      const off = row.sections_off ?? [];
      const active = spec.data
        ? capContent(
            activeContent(row.cv_content ?? {}, off),
            effectiveCaps(spec.data.cv.max_entries, off),
          )
        : activeContent(row.cv_content ?? {}, off);
      const cvMd = cvToMarkdown(meta.sender.name || "CV", active, db);
      const letterMd = letterToMarkdown(meta, row.cover_letter);
      const md =
        scope === "cv"
          ? cvMd
          : scope === "letter"
            ? letterMd
            : `${letterMd}\n---\n\n${cvMd}`;
      downloadText(md, `${stem}.md`, "text/markdown");
    });
  }

  function onDownloadJson() {
    void withBusy(async () => {
      const row = await current();
      if (!row) return;
      downloadText(
        exportJson(scope, {
          // A switched-off section is content the user removed from this application,
          // not a layout cut — it has no business in any export, dump or not.
          content: activeContent(row.cv_content ?? {}, row.sections_off ?? []),
          meta: metaOf(row),
          body: row.cover_letter,
          db: careerDb.data,
        }),
        `${stem}.json`,
        "application/json",
      );
    });
  }

  function notify(built: BuiltPdf) {
    for (const n of fitNotices(built, spec.data?.cv.pages ?? 1)) {
      if (n.level === "warning") toast.warning(n.text);
      else toast.info(n.text);
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
          Unsaved edits are saved first. The CV is auto-fitted to the layout's
          page budget by dropping the lowest-ranked entries; the letter is never
          cut, only flagged.
        </p>
        <PortfolioLinkSection
          app={app}
          link={link}
          onLink={setLink}
          includeQr={includeQr}
          onIncludeQr={setIncludeQr}
        />
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label className="text-xs">Scope</Label>
            <Select
              value={scope}
              onValueChange={(v) => setScope(v as ExportScope)}
            >
              <SelectTrigger className="w-40" aria-label="Scope">
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
          <Button
            size="sm"
            variant="outline"
            onClick={onDownloadMd}
            disabled={busy}
          >
            Markdown
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onDownloadJson}
            disabled={busy}
          >
            JSON
          </Button>
        </div>
      </CardContent>

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
    </Card>
  );
}
