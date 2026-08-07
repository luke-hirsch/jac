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

    // ⚠️ `fontSize` is not optional next to a unitless `lineHeight`. @react-pdf/stylesheet
    // resolves the multiplier against THIS object's fontSize or, failing that, its
    // DEFAULT_FONT_SIZE of 18 — so a bare `lineHeight: 1.4` silently means 25.2pt, not
    // 12.6pt. Inheritance does not help: the resolver only sees the one style object.
    summary: { fontSize: base, marginBottom: base * 0.4, lineHeight: 1.4 },
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
    // Wide enough for the whole range on ONE line at `small`: the widest real case,
    // "Mar 2023 – present", measures ~71pt, so 27mm minus the 4.5pt padding leaves
    // headroom. Undersizing this does not merely wrap, it wrecks the page — see the
    // note on `dateParts`.
    hints: {
      width: mm(27),
      color: spec.colors.muted,
      fontSize: small,
      paddingRight: base * 0.5,
    },
    // Same trap as `summary` above — declare the fontSize the multiplier applies to.
    //
    // `paddingTop` aligns the en dash in the date with the em dash in the heading beside
    // it — the eye reads those two rules as one horizontal line, so they, not the
    // baselines, are what "aligned" means here. Two corrections in one number:
    //
    //   base * 0.150   top-aligned → baselines flush. A smaller font's baseline sits
    //                  higher inside its line box, so the 7.5pt date rode 1.35pt high.
    //   base * 0.034   baselines flush → DASHES flush, back up again. Measured ink
    //                  centres above own baseline: the 9pt Bold em dash 2.340pt (0.260
    //                  em), the 7.5pt regular en dash 2.037pt (0.272 em) — the heavier
    //                  dash rides higher, so a flush baseline leaves it 0.3pt above.
    //   = base * 0.116
    //
    // Both terms are proportional to `base` (`small` is a fixed 0.833 × base), so this
    // scales with the layout rather than hard-coding 1.05pt.
    //
    // ⚠️ Not `alignItems` on `row`: `center` centres the gutter against the WHOLE entry
    // (heading + prose + bullets), dropping the date ~16pt down beside the bullets, and
    // `baseline` leaves 0.45pt because Yoga has no font baseline here and falls back to
    // the box height. Measured — see this guide's Results.
    hintLine: { fontSize: small, lineHeight: 1.2, paddingTop: base * 0.116 },
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
    // Side-by-side compact sections: equal columns, the gutter on every column but the
    // last. Keeps two short blocks (certifications, languages) from each eating a
    // full-width line and reading as more important than they are.
    sideRow: { flexDirection: "row" },
    sideCol: { flex: 1 },
    sideGutter: { paddingRight: base * 2 },
    stackedRow: { flexDirection: "row", marginBottom: base / 6 },
    // Narrower than the main flow's `hints` (27mm), because this sits inside a
    // half-width column — but wide enough for the longest real qualifier,
    // "conversational" (~52pt at 7.5pt), so it never wraps.
    stackedHints: {
      width: mm(20),
      color: spec.colors.muted,
      fontSize: small,
      paddingRight: base * 0.4,
    },
    // Same lineHeight trap as `summary`: declare the fontSize the multiplier resolves
    // against, or 1.3 silently means 1.3 × react-pdf's 18pt default.
    stackedLine: { fontSize: small, lineHeight: 1.3 },
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
  stacked,
  detailed,
  demoted,
}: {
  section: SectionKey;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  styles: ReturnType<typeof cvStyles>;
  compact?: boolean;
  /** Inside a side-by-side row: one entry per line instead of one joined run, so a
   *  half-width column reads as a list rather than as wrapped prose. */
  stacked?: boolean;
  /** The layout's `cv.detailed` budget. Passed instead of the whole spec so this
   *  function keeps the narrow signature it has today. */
  detailed?: Record<string, number>;
  demoted?: Set<string>;
}) {
  const entries = content[section] ?? [];
  if (entries.length === 0) return null;

  if (compact && stacked) {
    return (
      <View>
        <Text style={styles.sectionTitle}>{SECTION_TITLES[section]}</Text>
        {entries.map((e) => {
          const p = entryParts(db, section, e);
          // The qualifier column, same idiom as the main flow's dates and the skills
          // block's category labels: whatever the entry is *rated* by goes left, muted,
          // and the name reads down a clean edge. A certification is qualified by when
          // it was issued, a language by how well it is spoken — one of the two is
          // always empty, so this is one rule, not a section switch.
          const hint = p.dateFrom || p.meta;
          return (
            <View key={e.id} style={styles.stackedRow}>
              <Text style={styles.stackedHints}>{hint}</Text>
              <Text style={[styles.content, styles.stackedLine]}>
                {p.heading}
              </Text>
            </View>
          );
        })}
      </View>
    );
  }

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
              {/* ONE Text, not two stacked ones: each side is internally unbreakable,
                  so the single plain space between them is the only break opportunity
                  in the column — the range sets on one line, and if a narrow custom
                  layout can't hold it, it splits exactly where we want it to. */}
              {p.dateFrom ? (
                <Text style={styles.hintLine}>
                  {p.dateTo ? `${p.dateFrom} ${p.dateTo}` : p.dateFrom}
                </Text>
              ) : null}
            </View>
            <View style={styles.content}>
              {/* No favourite marker here. U+2605 is not in WinAnsi and the standard-14
                  Helvetica has no glyph for it, so it rendered as a zero-width blank and
                  left only its trailing space behind — every favourite heading sat 2.5pt
                  right of its own description while showing nothing for it. Favourites
                  are a ranking/fit signal anyway (they drop last, see fit.ts): internal,
                  not something a recruiter should read off the page. Markdown keeps the
                  star — it is UTF-8 and has no left edge to break. */}
              <Text style={styles.heading}>{p.heading}</Text>
              {/* A compact entry is the same dated row, minus everything that costs
                  lines. The description is still in the invisible-ink layer — the
                  hidden payload joins the career-DB row, so nothing is lost to a
                  parser, only to the page. */}
              {full && p.meta ? (
                <Text style={styles.meta}>{p.meta}</Text>
              ) : null}
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
      {spec.cv.sidebar.map((group) => {
        if (!Array.isArray(group)) {
          return (
            <CvSectionView
              key={group}
              section={group as SectionKey}
              content={content}
              db={db}
              styles={styles}
              compact
            />
          );
        }
        // An empty section must not hold a column open — with one survivor the row
        // collapses to the plain full-width rendering.
        const present = group.filter((s) => (content[s] ?? []).length > 0);
        if (present.length === 0) return null;
        if (present.length === 1) {
          return (
            <CvSectionView
              key={present[0]}
              section={present[0] as SectionKey}
              content={content}
              db={db}
              styles={styles}
              compact
            />
          );
        }
        return (
          <View key={group.join("+")} style={styles.sideRow}>
            {present.map((s, i) => (
              <View
                key={s}
                style={
                  i < present.length - 1
                    ? [styles.sideCol, styles.sideGutter]
                    : styles.sideCol
                }
              >
                <CvSectionView
                  section={s as SectionKey}
                  content={content}
                  db={db}
                  styles={styles}
                  compact
                  stacked
                />
              </View>
            ))}
          </View>
        );
      })}
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
