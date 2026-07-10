/**
 * react-pdf templates, driven by the LayoutSpec. `CvPages` wraps (react-pdf paginates
 * automatically — the fit loop measures the result); `LetterPage` approximates DIN 5008
 * (address field at the window-envelope position, right-aligned date, bold subject).
 * The "complete" document is letter first, then CV — the usual application order.
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

export const mm = (n: number) => n * 2.83465;

/* ---------- CV ---------- */

function cvStyles(spec: LayoutSpec) {
  const base = spec.font.base_pt;
  return StyleSheet.create({
    page: {
      paddingVertical: spec.page.margin[0],
      paddingHorizontal: spec.page.margin[1],
      fontFamily: spec.font.family,
      fontSize: base,
      color: spec.colors.text,
    },
    name: { fontSize: base * 2, marginBottom: base, color: spec.colors.accent },
    columns: { flexDirection: "row", gap: base * 1.5 },
    main: { flex: 2 },
    sidebar: { flex: 1 },
    sectionTitle: {
      fontSize: base * 1.2,
      color: spec.colors.accent,
      marginTop: base,
      marginBottom: base * 0.4,
    },
    entry: { marginBottom: base * 0.6 },
    heading: { fontFamily: `${spec.font.family}-Bold` },
    meta: { color: spec.colors.muted, fontSize: base * 0.85 },
    body: { marginTop: base * 0.2 },
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
            {!compact && p.body ? (
              <Text style={styles.body}>{p.body}</Text>
            ) : null}
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
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      <Text style={styles.name}>{name}</Text>
      <View style={styles.columns}>
        <View style={styles.main}>
          {spec.cv.sections.map((s) => (
            <CvSectionView
              key={s}
              section={s as SectionKey}
              content={content}
              db={db}
              styles={styles}
            />
          ))}
        </View>
        <View style={styles.sidebar}>
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
        </View>
      </View>
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
}: {
  spec: LayoutSpec;
  meta: LetterMeta;
  body: string;
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
    </Page>
  );
}

/* ---------- documents ---------- */

export type CvDocProps = Parameters<typeof CvPages>[0];
export type LetterDocProps = Parameters<typeof LetterPage>[0];

export const CvDocument = (p: CvDocProps) => (
  <Document>
    <CvPages {...p} />
  </Document>
);

export const LetterDocument = (p: LetterDocProps) => (
  <Document>
    <LetterPage {...p} />
  </Document>
);

export const ApplicationDocument = ({
  cv,
  letter,
}: {
  cv: CvDocProps;
  letter: LetterDocProps;
}) => (
  <Document>
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
