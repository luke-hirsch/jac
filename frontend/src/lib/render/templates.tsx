/**
 * react-pdf templates, driven by the LayoutSpec. `CvPages` wraps (react-pdf paginates
 * automatically — the fit loop measures the result); `LetterPage` approximates DIN 5008
 * (address field at the window-envelope position, right-aligned date, bold subject).
 * The "complete" document is letter first, then CV — the usual application order.
 *
 * The CV is deliberately single-column: ATS parsers read top-to-bottom and choke on
 * column interleaving, so machine readability beats the sidebar look. The spec's
 * `sidebar` sections still exist — they render after the main flow as compact joined
 * lines (one paragraph per section) instead of one block per entry.
 */
import {
  Document,
  Image,
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
  Font,
  type DocumentProps,
} from "@react-pdf/renderer";
import type { ReactElement } from "react";
import { SECTION_TITLES, type CvContent, type SectionKey } from "@/lib/cv-doc";
import type { LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { countPdfPages } from "./fit";
import { entryParts, skillGroups, entryDetail } from "./parts";
import type { LayoutSpec } from "./spec";

import type { DocMeta } from "./hidden";

export const mm = (n: number) => n * 2.83465;

/**
 * react-pdf hyphenates by default. That is wrong twice over here: it breaks German
 * compounds mid-word in the descriptions, and it was half the "weird line breaks" in the
 * date column. One global opt-out — the layout math below assumes words stay whole.
 */
Font.registerHyphenationCallback((word) => [word]);

/* ---------- invisible ink ---------- */

/**
 * 1pt text at opacity 0, absolutely positioned: zero layout impact (page counts and the
 * fit loop are untouched — render-hidden-pdf.test.ts guards the invariance), but the
 * glyphs land in the content stream where text extraction reads them. Bottom-anchored so
 * geometric extractors order it after the visible content.
 *
 * MUST be `fixed` + last-page-only via the render prop: a non-fixed bottom-anchored
 * absolute element joins react-pdf's pagination and gets a blank page of its own when
 * the flow content ends near the page bottom (the density guide's empty-trailing-page
 * bug — fitCv measures without ink, so the export grew a page the fit loop never saw).
 * `fixed` opts out of pagination entirely; the pageNumber === totalPages guard keeps
 * the payload single instead of repeating it on every page.
 */
function HiddenInk({ text }: { text?: string }) {
  if (!text) return null;
  // A single fixed Text, not a View: only Text's render prop is typed with
  // totalPages (react-pdf exposes it on fixed text for "Page X of Y" use).
  return (
    <Text
      fixed
      style={{
        position: "absolute",
        bottom: 6,
        left: 24,
        right: 24,
        opacity: 0,
        fontSize: 1,
      }}
      render={({ pageNumber, totalPages }) =>
        pageNumber === totalPages ? text : null
      }
    />
  );
}

function PageFooter({
  name,
  styles,
}: {
  name: string;
  styles: ReturnType<typeof cvStyles>;
}) {
  return (
    <Text
      fixed
      style={styles.pageFooter}
      render={({ pageNumber, totalPages }) =>
        totalPages > 1 ? `${name} — page ${pageNumber} of ${totalPages}` : null
      }
    />
  );
}
export type BodyBlock = { bullet: boolean; text: string };

/**
 * Career-DB descriptions are typed as markdown-lite. Only what actually appears in them is
 * honoured: leading "- " / "* " / "• " bullets, and **bold** __bold__ markers, which are
 * STRIPPED rather than rendered (react-pdf would need a nested Text per span; the emphasis
 * is not worth it on a 9pt CV line). Everything else passes through verbatim, one block
 * per non-empty line.
 */
export function bodyBlocks(text: string): BodyBlock[] {
  const strip = (s: string) =>
    s.replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1");
  return (text ?? "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = /^[-*•]\s+(.*)$/.exec(l);
      return m
        ? { bullet: true, text: strip(m[1]) }
        : { bullet: false, text: strip(l) };
    });
}
/* ---------- CV ---------- */

/**
 * Compact-9pt density ([frontend]-cv-density): tight *within* a section (entry gap
 * base/3, meta + sidebar lines at 0.833 × base ≈ 7.5pt on the 9pt default), while the
 * inter-section whitespace stays — the bigger sectionTitle.marginTop offsets the
 * shrunken entry gap. Everything is a multiplier of base_pt so custom layouts scale.
 * Exported for the density tests — the numbers are a design decision, not an accident.
 */
export function cvStyles(spec: LayoutSpec) {
  const base = spec.font.base_pt;
  const small = base * 0.833;
  return StyleSheet.create({
    page: {
      paddingVertical: spec.page.margin[0],
      paddingHorizontal: spec.page.margin[1],
      fontFamily: spec.font.family,
      fontSize: base,
      color: spec.colors.text,
    },
    name: {
      fontSize: base * 2,
      marginBottom: base * 0.4,
      color: spec.colors.accent,
    },
    subtitle: {
      fontSize: base * 1.1,
      color: spec.colors.muted,
      marginBottom: base * 0.4,
    },
    contact: {
      color: spec.colors.muted,
      fontSize: base * 0.9,
      marginBottom: base * 0.4,
    },

    summary: { marginBottom: base * 0.4, lineHeight: 1.4 },
    sectionTitle: {
      fontSize: base * 1.2,
      color: spec.colors.accent,
      marginTop: base * 1.4,
      marginBottom: base * 0.4,
      borderBottomWidth: 0.5,
      borderBottomColor: spec.colors.accent,
      paddingBottom: base * 0.15,
    },
    // Two-column entry: fixed date/label column + flexible content column.
    row: { flexDirection: "row", marginBottom: base / 3 },
    hints: {
      width: mm(23),
      color: spec.colors.muted,
      fontSize: small,
      paddingRight: base * 0.5,
    },
    hintLine: { lineHeight: 1.2 },
    content: { flex: 1 },
    entry: { marginBottom: base / 3 }, // kept — density decision
    heading: { fontFamily: `${spec.font.family}-Bold` },
    meta: { color: spec.colors.muted, fontSize: small },
    body: { marginTop: base * 0.15 },
    bulletRow: { flexDirection: "row", marginTop: base * 0.15 },
    bulletDot: { width: base * 0.7 },
    bulletText: { flex: 1 },
    // Absolute + fixed = zero layout impact (same argument as HiddenInk); sits above the
    // ink block at bottom: 6.
    pageFooter: {
      position: "absolute",
      bottom: mm(8),
      left: spec.page.margin[1],
      right: spec.page.margin[1],
      fontSize: small * 0.9,
      color: spec.colors.muted,
      textAlign: "center",
    },
    compact: { fontSize: small, marginBottom: base / 3 },
    // Portfolio QR: absolute (zero layout impact — the fit loop and page counts are
    // invariant, same argument as HiddenInk) inside the page margins, top-right.
    qr: {
      position: "absolute",
      top: spec.page.margin[0],
      right: spec.page.margin[1],
      alignItems: "center",
    },
    qrImage: { width: mm(18), height: mm(18) },
    qrCaption: {
      fontSize: small * 0.9,
      color: spec.colors.muted,
      marginTop: 2,
    },
  });
}
function CvSectionView({
  section,
  content,
  db,
  styles,
  compact,
  detailed,
  demoted,
}: {
  section: SectionKey;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  styles: ReturnType<typeof cvStyles>;
  compact?: boolean;
  /** The layout's `cv.detailed` budget. Passed instead of the whole spec so this
   *  function keeps the narrow signature it has today. */
  detailed?: Record<string, number>;
  demoted?: Set<string>;
}) {
  const entries = content[section] ?? [];
  if (entries.length === 0) return null;

  if (compact) {
    const rows =
      section === "skills"
        ? skillGroups(db, entries)
        : [
            {
              label: "",
              names: entries
                .map((e) => {
                  const p = entryParts(db, section, e);
                  return p.meta ? `${p.heading} (${p.meta})` : p.heading;
                })
                .join(", "),
            },
          ];
    return (
      <View>
        <Text style={styles.sectionTitle}>{SECTION_TITLES[section]}</Text>
        {rows.map((r, i) => (
          <View key={r.label || i} style={styles.row}>
            {r.label ? <Text style={styles.hints}>{r.label}</Text> : null}
            <Text style={[styles.content, styles.compact]}>{r.names}</Text>
          </View>
        ))}
      </View>
    );
  }

  return (
    <View>
      <Text style={styles.sectionTitle} minPresenceAhead={mm(14)}>
        {SECTION_TITLES[section]}
      </Text>
      {entries.map((e, i) => {
        const p = entryParts(db, section, e);
        const full =
          entryDetail(e, i, section, detailed ?? {}, demoted) === "full";
        return (
          <View key={e.id} style={styles.row} wrap={false}>
            <View style={styles.hints}>
              {p.dateFrom ? (
                <Text style={styles.hintLine}>{p.dateFrom}</Text>
              ) : null}
              {p.dateTo ? <Text style={styles.hintLine}>{p.dateTo}</Text> : null}
            </View>
            <View style={styles.content}>
              <Text style={styles.heading}>
                {p.favourite ? "★ " : ""}
                {p.heading}
              </Text>
              {/* A compact entry is the same dated row, minus everything that costs
                  lines. The description is still in the invisible-ink layer — the
                  hidden payload joins the career-DB row, so nothing is lost to a
                  parser, only to the page. */}
              {full && p.meta ? <Text style={styles.meta}>{p.meta}</Text> : null}
              {full
                ? bodyBlocks(p.body).map((b, j) =>
                    b.bullet ? (
                      <View key={j} style={styles.bulletRow}>
                        <Text style={styles.bulletDot}>•</Text>
                        <Text style={styles.bulletText}>{b.text}</Text>
                      </View>
                    ) : (
                      <Text key={j} style={styles.body}>
                        {b.text}
                      </Text>
                    ),
                  )
                : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}

export function CvPages({
  spec,
  name,
  content,
  db,
  contact,
  summary,
  subtitle,
  hidden,
  portfolio,
  demoted,
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  contact?: string;
  summary?: string;
  subtitle?: string;
  hidden?: string;
  portfolio?: { qr: string };
  demoted?: Set<string>;
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      {portfolio ? (
        <View style={styles.qr}>
          <Image src={portfolio.qr} style={styles.qrImage} />
          <Text style={styles.qrCaption}>online portfolio</Text>
        </View>
      ) : null}
      <Text style={styles.name}>{name}</Text>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      {summary ? <Text style={styles.summary}>{summary}</Text> : null}
      {contact ? <Text style={styles.contact}>{contact}</Text> : null}
      {spec.cv.sections.map((s) => (
        <CvSectionView
          key={s}
          section={s as SectionKey}
          content={content}
          db={db}
          styles={styles}
          detailed={spec.cv.detailed}
          demoted={demoted}
        />
      ))}
      {spec.cv.sidebar.map((s) => (
        <CvSectionView
          key={s}
          section={s as SectionKey}
          content={content}
          db={db}
          styles={styles}
          compact
        />
      ))}
      <PageFooter name={name} styles={styles} />
      <HiddenInk text={hidden} />
    </Page>
  );
}
/* ---------- letter (DIN 5008-ish) ---------- */

function letterStyles(spec: LayoutSpec) {
  const base = Math.max(spec.font.base_pt, 11); // letters read better a notch larger
  return StyleSheet.create({
    page: {
      // DIN 5008 address zone ends at 85mm; start the body right below it rather
      // than at the 98mm reference-line position we don't use (kills a ~13mm gap).
      paddingTop: mm(85),
      paddingBottom: mm(25),
      paddingLeft: mm(25),
      paddingRight: mm(20),
      fontFamily: spec.font.family,
      fontSize: base,
      color: spec.colors.text,
      lineHeight: 1.4,
    },
    // Address field where a window envelope shows it (DIN 5008 form B: 45mm from top).
    addressField: {
      position: "absolute",
      top: mm(45),
      left: mm(25),
      width: mm(85),
    },
    returnLine: {
      fontSize: base * 0.65,
      color: spec.colors.muted,
      marginBottom: 4,
    },
    date: {
      position: "absolute",
      top: mm(45),
      right: mm(20),
      fontSize: base * 0.9,
    },
    subject: { fontFamily: `${spec.font.family}-Bold`, marginBottom: base },
    para: { marginBottom: base },
    signatureImg: {
      width: mm(40),
      marginTop: base * 0.5,
      marginBottom: base * 0.2,
    },
    signature: { marginTop: base * 2 },
    footer: {
      position: "absolute",
      bottom: mm(12),
      left: mm(25),
      right: mm(20),
      fontSize: base * 0.7,
      color: spec.colors.muted,
      textAlign: "center",
    },
  });
}

export function LetterPage({
  spec,
  meta,
  body,
  signatureUrl,
  hidden,
}: {
  spec: LayoutSpec;
  meta: LetterMeta;
  body: string;
  signatureUrl?: string;
  hidden?: string;
}) {
  const styles = letterStyles(spec);
  const snd = meta.sender;
  const rcp = meta.recipient;
  const returnLine = [
    snd.name,
    snd.street,
    [snd.zip, snd.city].filter(Boolean).join(" "),
  ]
    .filter(Boolean)
    .join(" · ");
  const recipientLines = [
    rcp.company,
    rcp.contact_name,
    rcp.street,
    rcp.address_line2,
    [rcp.zip, rcp.city].filter(Boolean).join(" "),
    rcp.country,
  ].filter(Boolean);
  const dateLine = [snd.city, meta.date].filter(Boolean).join(", ");
  const contactLine = [snd.email, snd.phone, snd.website]
    .filter(Boolean)
    .join(" · ");

  return (
    <Page size={spec.page.size} style={styles.page}>
      <View style={styles.addressField} fixed>
        {returnLine ? (
          <Text style={styles.returnLine}>{returnLine}</Text>
        ) : null}
        {recipientLines.map((l) => (
          <Text key={l}>{l}</Text>
        ))}
      </View>
      <Text style={styles.date} fixed>
        {dateLine}
      </Text>

      <Text style={styles.subject}>{meta.subject}</Text>
      <Text style={styles.para}>{meta.salutation}</Text>
      {body.split(/\n{2,}/).map((p, i) => (
        <Text key={i} style={styles.para}>
          {p}
        </Text>
      ))}
      <Text style={styles.para}>{meta.closing}</Text>
      {signatureUrl ? (
        <Image src={signatureUrl} style={styles.signatureImg} />
      ) : null}
      <Text style={styles.signature}>{snd.name}</Text>
      {contactLine ? (
        <Text style={styles.footer} fixed>
          {contactLine}
        </Text>
      ) : null}
      <HiddenInk text={hidden} />
    </Page>
  );
}

/* ---------- documents ---------- */

export type CvDocProps = Parameters<typeof CvPages>[0];
export type LetterDocProps = Parameters<typeof LetterPage>[0];

export const CvDocument = ({
  docMeta,
  ...p
}: CvDocProps & { docMeta?: DocMeta }) => (
  <Document {...docMeta}>
    <CvPages {...p} />
  </Document>
);

export const LetterDocument = ({
  docMeta,
  ...p
}: LetterDocProps & { docMeta?: DocMeta }) => (
  <Document {...docMeta}>
    <LetterPage {...p} />
  </Document>
);

export const ApplicationDocument = ({
  cv,
  letter,
  docMeta,
}: {
  cv: CvDocProps;
  letter: LetterDocProps;
  docMeta?: DocMeta;
}) => (
  <Document {...docMeta}>
    <LetterPage {...letter} />
    <CvPages {...cv} />
  </Document>
);

/* ---------- impure render helpers ---------- */

export async function renderPdfBlob(
  doc: ReactElement<DocumentProps>,
): Promise<Blob> {
  return pdf(doc).toBlob();
}

export async function pdfPages(
  doc: ReactElement<DocumentProps>,
): Promise<number> {
  const blob = await renderPdfBlob(doc);
  const bytes = await blob.arrayBuffer();
  return countPdfPages(new TextDecoder("latin1").decode(bytes));
}
