/**
 * One PDF build for every place that makes one: the export card (download and preview) and the
 * connector's preview in the Claude app (`src/connector`). It was the export card's closure; it
 * lives here so the two can't disagree about the fit, the hidden layer or the documents.
 *
 * Everything comes in as data: the saved row, the spec with the accent already on it, the
 * career, the profile. Attachments are the caller's: the export card fetches them with the
 * session and merges them after this; the connector has no session and says how many there are.
 */
import { activeContent } from "@/lib/cv-doc";
import type { ExportScope } from "@/lib/export";
import {
  contactLine,
  fillBlanks,
  normalizeLetterMeta,
  senderFromProfile,
  stripSoftStub,
  type LetterMeta,
} from "@/lib/letter-doc";
import { qrDataUrl } from "@/lib/portfolio/qr";
import type { ApplicationRow } from "@/lib/queries/applications";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { ProfileRow } from "@/lib/queries/profile";
import {
  effectiveCaps,
  fitContent,
  preflightKey,
  type PreflightResult,
} from "./fit";
import { docMetadata, hiddenPayload } from "./hidden";
import { isFavouriteLookup } from "./parts";
import type { BuiltPdf } from "./preview";
import type { LayoutSpec } from "./spec";
import {
  ApplicationDocument,
  CvDocument,
  LetterDocument,
  pdfPages,
  renderPdfBlob,
} from "./templates";

/** What the build reads of the profile. The connector's preview gets exactly these fields
 *  (backend/jac/mcp.py `PREVIEW_PROFILE`), without the signature, which stays in jac. */
export type RenderProfile = Pick<
  ProfileRow,
  | "name"
  | "email"
  | "phone"
  | "street"
  | "address_line2"
  | "zip"
  | "city"
  | "country"
  | "website"
  | "linkedin_url"
  | "github_url"
  | "bio"
  | "show_socials"
  | "accent_color"
> & { signature?: string };

/** The row's letter meta, with blank sender fields filled from the profile (the editor's rule),
 *  so an export never goes out with an empty sender block. */
export function exportMeta(
  row: Pick<ApplicationRow, "letter_meta">,
  profile: RenderProfile | undefined,
): LetterMeta {
  const stored = normalizeLetterMeta(row.letter_meta);
  return profile
    ? {
        ...stored,
        sender: fillBlanks(stored.sender, senderFromProfile(profile)),
      }
    : stored;
}

export type BuildArgs = {
  row: ApplicationRow;
  spec: LayoutSpec; // the accent applied (useRenderSpec, or applyAccent by hand)
  db: CvEntriesResponse | undefined;
  profile: RenderProfile | undefined;
  scope: ExportScope;
  /** The portfolio link behind the QR, when this export carries one. */
  portfolioUrl?: string;
  /** The editor's measurement for this exact state, if it has one (the export card's). */
  cachedFit?: (key: string) => PreflightResult | null | undefined;
};

export async function buildPdf({
  row,
  spec: s,
  db,
  profile,
  scope,
  portfolioUrl,
  cachedFit,
}: BuildArgs): Promise<BuiltPdf> {
  const meta = exportMeta(row, profile);
  const name = meta.sender.name || "CV";
  const socials = profile?.show_socials ?? false;
  const contact = contactLine(meta.sender, { socials, portfolioUrl });
  // Per-user signature image in the letter closing (the owner-only URL); the connector's
  // preview has none.
  const signatureUrl = profile?.signature || undefined;
  // Raster QR for react-pdf's <Image> (absolute block, layout-invariant); included in
  // the fit-measuring render below too, for measure/export parity.
  const portfolio = portfolioUrl
    ? { qr: await qrDataUrl(portfolioUrl) }
    : undefined;
  const summary = profile?.bio ?? "";
  // Template entry budget first (hard editorial cap), page fit second. `full` (pre-cap)
  // sticks around: everything it has that the fitted content lacks — cap cuts and page
  // drops alike — goes into the hidden layer as cut_for_space.
  const off = row.sections_off ?? [];
  const full = activeContent(row.cv_content ?? {}, off);
  const cached = cachedFit?.(
    preflightKey({
      spec: s,
      db,
      content: full,
      sectionsOff: off,
      cvHeader: { name, contact, summary },
      letterBody: row.cover_letter,
      letterMeta: meta,
    }),
  );
  const fit =
    scope === "letter"
      ? null
      : (cached ??
        (await fitContent(
          full,
          effectiveCaps(s.cv.max_entries, off),
          s.cv.detailed,
          s.cv.pages,
          (c, demoted) =>
            pdfPages(
              <CvDocument
                spec={s}
                name={name}
                content={c}
                db={db}
                demoted={demoted}
                contact={contact}
                summary={summary}
                portfolio={portfolio}
              />,
            ),
          isFavouriteLookup(db),
        )));
  const demoted = new Set(fit?.demotedIds ?? []);

  const letterPages =
    scope === "cv"
      ? null
      : await pdfPages(
          <LetterDocument
            spec={s}
            meta={meta}
            body={stripSoftStub(row.cover_letter)}
          />,
        );

  // The machine-readable layer: built from the fit *result* (which is why the fit's
  // measuring renders above go without it — an absolute block has zero layout impact).
  const hidden = hiddenPayload(scope, {
    fitted: fit?.content ?? {},
    full,
    meta,
    body: row.cover_letter,
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
        demoted={demoted}
      />
    ) : scope === "letter" ? (
      <LetterDocument
        docMeta={docMeta}
        spec={s}
        meta={meta}
        body={stripSoftStub(row.cover_letter)}
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
          demoted,
        }}
        letter={{
          spec: s,
          meta,
          body: stripSoftStub(row.cover_letter),
          signatureUrl,
        }}
      />
    );
  return { blob: await renderPdfBlob(doc), fit, letterPages };
}
