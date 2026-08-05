import { useState } from "react";
import { PdfPreviewDialog } from "@/components/applications/pdf-preview-dialog";
import { fitNotices, type BuiltPdf } from "@/lib/render/preview";
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
import { useAttachments } from "@/lib/queries/attachments";
import { mergePdfs } from "@/lib/render/attachments";
import { activeContent } from "@/lib/cv-doc";
import {
  contactLine,
  fillBlanks,
  normalizeLetterMeta,
  senderFromProfile,
  stripSoftStub,
} from "@/lib/letter-doc";
import { useCvEntries, useFullList, type LayoutRow } from "@/lib/queries/jac";
import { useProfile } from "@/lib/queries/profile";
import { capContent, fitCv } from "@/lib/render/fit";
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

import { docMetadata, hiddenPayload } from "@/lib/render/hidden";
import { PortfolioLinkSection } from "@/components/applications/portfolio-link-section";
import { qrDataUrl } from "@/lib/portfolio/qr";
import { type PortfolioLinkRow } from "@/lib/queries/portfolio";

export function ExportCard({ app }: { app: ApplicationRow }) {
  const layouts = useFullList<LayoutRow>("layouts");
  const layout = layouts.data?.find((l) => l.id === app.layout);
  const spec = useLayoutSpec(layout);
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
  // Per-user uploaded signature image, rendered in the letter closing when present.
  const signatureUrl = profile.data?.signature || undefined;

  async function buildPdf(): Promise<BuiltPdf> {
    if (!spec.data) throw new Error("layout spec not loaded");
    const s = spec.data;
    const db = careerDb.data;
    const socials = profile.data?.show_socials ?? false;
    const portfolioUrl = includeQr && link ? link.url : undefined;
    const contact = contactLine(meta.sender, { socials, portfolioUrl });
    // Raster QR for react-pdf's <Image> (absolute block, layout-invariant); included in
    // the fit-measuring render below too, for measure/export parity.
    const portfolio = portfolioUrl
      ? { qr: await qrDataUrl(portfolioUrl) }
      : undefined;
    const summary = profile.data?.bio ?? "";
    // Template entry budget first (hard editorial cap), page fit second. `full` (pre-cap)
    // sticks around: everything it has that the fitted content lacks — cap cuts and page
    // drops alike — goes into the hidden layer as cut_for_space.
    const full = activeContent(app.cv_content ?? {});
    const active = capContent(full, s.cv.max_entries);

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
                  portfolio={portfolio}
                />,
              ),
            isFavouriteLookup(db),
          );
    const letterPages =
      scope === "cv"
        ? null
        : await pdfPages(
            <LetterDocument
              spec={s}
              meta={meta}
              body={stripSoftStub(app.cover_letter)}
            />,
          );

    // The machine-readable layer: built from the fit *result* (which is why the fit's
    // measuring renders above go without it — an absolute block has zero layout impact).
    const hidden = hiddenPayload(scope, {
      fitted: fit?.content ?? {},
      full,
      meta,
      body: app.cover_letter,
      db,
    });
    const docMeta = docMetadata(scope, {
      name,
      subject: meta.subject,
      content: fit?.content ?? {},
      db,
    });

    const doc =
      scope === "cv" ? (
        <CvDocument
          docMeta={docMeta}
          spec={s}
          name={name}
          content={fit!.content}
          db={db}
          contact={contact}
          summary={summary}
          hidden={hidden}
          portfolio={portfolio}
        />
      ) : scope === "letter" ? (
        <LetterDocument
          docMeta={docMeta}
          spec={s}
          meta={meta}
          body={stripSoftStub(app.cover_letter)}
          signatureUrl={signatureUrl}
          hidden={hidden}
        />
      ) : (
        <ApplicationDocument
          docMeta={docMeta}
          cv={{
            spec: s,
            name,
            content: fit!.content,
            db,
            contact,
            summary,
            hidden,
            portfolio,
          }}
          letter={{
            spec: s,
            meta,
            body: stripSoftStub(app.cover_letter),
            signatureUrl,
          }}
        />
      );
    return { blob: await renderPdfBlob(doc), fit, letterPages };
  }

  // Client-side pdf-lib merge: resolve the application's selected attachment ids to file URLs
  // (in the stored order, stale ids skipped) and append them. Skipped for a letter-only export
  // — attachments follow the CV, not the letter.
  async function withAttachments(blob: Blob): Promise<Blob> {
    const byId = new Map((library.data ?? []).map((a) => [a.id, a] as const));
    const urls = (app.attachments ?? [])
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
  function blockedBy(format: ExportFormat): boolean {
    const reason = exportBlocker(scope, format, app.cover_letter);
    if (reason) toast.error(reason);
    return reason != null;
  }

  function onDownloadPdf() {
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const built = await buildPdf();
      downloadBlob(await withAttachments(built.blob), `${stem}.pdf`);
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
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const built = await buildPdf();
      const blob = await withAttachments(built.blob);
      setPreview({ url: URL.createObjectURL(blob), info: built });
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
          Exports the saved application content — save your edits first. The CV
          is auto-fitted to the layout's page budget by dropping the
          lowest-ranked entries; the letter is never cut, only flagged.
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
