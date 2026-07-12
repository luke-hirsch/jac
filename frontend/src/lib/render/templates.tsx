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
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
  type DocumentProps,
} from "@react-pdf/renderer";
import type { ReactElement } from "react";
import { SECTION_TITLES, type CvContent, type SectionKey } from "@/lib/cv-doc";
import type { LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { countPdfPages } from "./fit";
import { entryParts } from "./parts";
import type { LayoutSpec } from "./spec";

import type { DocMeta } from "./hidden";

export const mm = (n: number) => n * 2.83465;

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
    // 0.4 × base under the name — a full line's gap made the header feel
    // disconnected from the bio (density Results follow-up).
    name: {
      fontSize: base * 2,
      marginBottom: base * 0.4,
      color: spec.colors.accent,
    },
    contact: {
      color: spec.colors.muted,
      fontSize: base * 0.9,
      marginBottom: base,
    },
    summary: { marginBottom: base * 0.4, lineHeight: 1.4 },
    sectionTitle: {
      fontSize: base * 1.2,
      color: spec.colors.accent,
      marginTop: base * 1.4,
      marginBottom: base * 0.4,
    },
    entry: { marginBottom: base / 3 },
    heading: { fontFamily: `${spec.font.family}-Bold` },
    meta: { color: spec.colors.muted, fontSize: small },
    body: { marginTop: base * 0.15 },
    // Sidebar sections as one joined line per section — smaller still, so skills and
    // languages carry more information per line.
    compact: { fontSize: small, marginBottom: base / 3 },
  });
}

function CvSectionView({
  section,
  content,
  db,
  styles,
  compact,
}: {
  section: SectionKey;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  styles: ReturnType<typeof cvStyles>;
  compact?: boolean;
}) {
  const entries = content[section] ?? [];
  if (entries.length === 0) return null;
  if (compact) {
    // One joined paragraph: "Python (expert · technical), German (native), …" —
    // compact on paper, and a linear text run for machine parsers.
    const line = entries
      .map((e) => {
        const p = entryParts(db, section, e);
        const head = `${p.favourite ? "★ " : ""}${p.heading}`;
        return p.meta ? `${head} (${p.meta})` : head;
      })
      .join(", ");
    return (
      <View>
        <Text style={styles.sectionTitle}>{SECTION_TITLES[section]}</Text>
        <Text style={styles.compact}>{line}</Text>
      </View>
    );
  }
  return (
    <View>
      <Text style={styles.sectionTitle}>{SECTION_TITLES[section]}</Text>
      {entries.map((e) => {
        const p = entryParts(db, section, e);
        return (
          <View key={e.id} style={styles.entry} wrap={false}>
            <Text style={styles.heading}>
              {p.favourite ? "★ " : ""}
              {p.heading}
            </Text>
            {p.meta ? <Text style={styles.meta}>{p.meta}</Text> : null}
            {p.body ? <Text style={styles.body}>{p.body}</Text> : null}
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
  hidden,
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  contact?: string;
  summary?: string;
  hidden?: string;
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      {/* Header order: name → bio → contact (Lukas's call, 2026-07-11). */}
      <Text style={styles.name}>{name}</Text>
      {summary ? <Text style={styles.summary}>{summary}</Text> : null}
      {contact ? <Text style={styles.contact}>{contact}</Text> : null}
      {spec.cv.sections.map((s) => (
        <CvSectionView
          key={s}
          section={s as SectionKey}
          content={content}
          db={db}
          styles={styles}
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
      <HiddenInk text={hidden} />
    </Page>
  );
}

/* ---------- letter (DIN 5008-ish) ---------- */

function letterStyles(spec: LayoutSpec) {
  const base = Math.max(spec.font.base_pt, 11); // letters read better a notch larger
  return StyleSheet.create({
    page: {
      paddingTop: mm(98),
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
  hidden,
}: {
  spec: LayoutSpec;
  meta: LetterMeta;
  body: string;
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
