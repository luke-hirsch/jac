# [frontend] render-export — react-pdf templates, fit-to-layout, md/json/pdf export

> Guide 4 of 4 for the "frontend polish" phase. Branch: `frontend/render-export`.
> **Depends on guides 1–3 being merged** (layout specs + `letter_meta` + `cv-doc.ts` +
> `letter-doc.ts`). Merge `main` into this branch before starting. This completes the target
> flow: … → manual customisation → **download final PDF rendered by the frontend**.

## Context / goal

Render and export happen entirely in the browser with **`@react-pdf/renderer`** (decided
earlier — memory `cv-render-export-decision`: template components → vector PDF, deterministic
page counts, `pdf().toBlob()` for download).

- **Layouts**: the application's `ApplicationLayout` carries a JSON spec (`default` = 1-page CV,
  `two-page` = 2-page CV; seeded in guide 1). The cover letter is always budgeted **one** page →
  max three pages per complete application.
- **Fit**: the backend selection is re-filtered to the page budget by *measuring*: render,
  count pages, drop the lowest-ranked tail entries, repeat (binary search). Deselected entries
  (guide 2) are excluded before fitting; auto-drops are computed at export time and **never
  persisted** — deselect/reorder is how the user overrides them.
- **Export**: scope × format — {complete, cv, letter} × {pdf, md, json}. Markdown mirrors the
  backend's `CvRender.export_md` / `CoverLetter.render_markdown` so all renderers agree.
- The letter is never auto-truncated — an overflowing letter gets a warning, not silent cuts.

## Affected files

| file | why |
| --- | --- |
| `frontend/package.json` | `npm install @react-pdf/renderer` |
| `frontend/src/lib/render/spec.ts` | **new** — `LayoutSpec` type, parser/normalizer, template fetch hook |
| `frontend/src/lib/render/fit.ts` | **new** — pure fit logic: drop order, page counting, binary-search fit |
| `frontend/src/lib/render/parts.ts` | **new** — entry → {heading, meta, body} shared by PDF + md renderers |
| `frontend/src/lib/render/templates.tsx` | **new** — `CvDocument` / `LetterDocument` / `ApplicationDocument` + render helpers |
| `frontend/src/lib/export.ts` | **new** — md/json builders + download helpers |
| `frontend/src/routes/_authenticated/applications/$applicationId.tsx` | new `ExportCard` |

## The code

### 0. Dependency

```bash
cd frontend && npm install @react-pdf/renderer
```

### 1. `frontend/src/lib/render/spec.ts`

```ts
/**
 * The declarative layout spec stored as `ApplicationLayout.template` (a media file; source of
 * truth: backend/jac/resources/*.json, seeded by seed_default_domains). Parsing is defensive —
 * a user-uploaded spec may be partial or use the legacy singular "education" section name.
 */
import { useQuery } from "@tanstack/react-query";
import type { LayoutRow } from "@/lib/queries/jac";

export type LayoutSpec = {
  version: number;
  page: { size: "A4" | "LETTER"; margin: [number, number] }; // [vertical, horizontal] pt
  font: { family: string; base_pt: number };
  colors: { accent: string; text: string; muted: string };
  cv: { pages: number; sections: string[]; sidebar: string[] };
  cover_letter: { din5008: boolean };
};

export const FALLBACK_SPEC: LayoutSpec = {
  version: 1,
  page: { size: "A4", margin: [56, 48] },
  font: { family: "Helvetica", base_pt: 10 },
  colors: { accent: "#1a5fb4", text: "#1c1c1c", muted: "#6b6b6b" },
  cv: {
    pages: 1,
    sections: ["jobs", "educations", "projects", "certifications"],
    sidebar: ["skills", "languages"],
  },
  cover_letter: { din5008: true },
};

/** Legacy spec section names → cv_content keys. */
const LEGACY_SECTIONS: Record<string, string> = { education: "educations" };

export function parseLayoutSpec(raw: unknown): LayoutSpec {
  const r = (raw ?? {}) as {
    version?: number;
    page?: Partial<LayoutSpec["page"]>;
    font?: Partial<LayoutSpec["font"]>;
    colors?: Partial<LayoutSpec["colors"]>;
    cv?: Partial<LayoutSpec["cv"]>;
    cover_letter?: Partial<LayoutSpec["cover_letter"]>;
  };
  const f = FALLBACK_SPEC;
  const sections = (names: string[] | undefined, fallback: string[]) =>
    (names ?? fallback).map((n) => LEGACY_SECTIONS[n] ?? n);
  return {
    version: r.version ?? f.version,
    page: {
      size: r.page?.size ?? f.page.size,
      margin: r.page?.margin ?? f.page.margin,
    },
    font: {
      family: r.font?.family ?? f.font.family,
      base_pt: r.font?.base_pt ?? f.font.base_pt,
    },
    colors: { ...f.colors, ...(r.colors ?? {}) },
    cv: {
      pages: r.cv?.pages ?? f.cv.pages,
      sections: sections(r.cv?.sections, f.cv.sections),
      sidebar: sections(r.cv?.sidebar, f.cv.sidebar),
    },
    cover_letter: { din5008: r.cover_letter?.din5008 ?? f.cover_letter.din5008 },
  };
}

/**
 * The FileField URL may be absolute (host of whoever served the API); fetch same-origin via
 * the pathname so the vite `/media` proxy (dev) / nginx (prod) serves it.
 */
export function templatePath(url: string, origin?: string): string {
  try {
    return new URL(url, origin ?? window.location.origin).pathname;
  } catch {
    return url;
  }
}

export function useLayoutSpec(layout: LayoutRow | undefined) {
  return useQuery({
    queryKey: ["jac", "layout-spec", layout?.id ?? "none", layout?.template ?? ""],
    queryFn: async (): Promise<LayoutSpec> => {
      if (!layout?.template) return FALLBACK_SPEC;
      const res = await fetch(templatePath(layout.template));
      if (!res.ok) throw new Error(`layout template: HTTP ${res.status}`);
      return parseLayoutSpec(await res.json());
    },
    enabled: layout !== undefined,
    staleTime: 5 * 60 * 1000,
  });
}
```

### 2. `frontend/src/lib/render/fit.ts` — pure (unit-tested)

```ts
/**
 * Fit the (active) cv_content to a page budget by dropping ranked tail entries.
 *
 * Ranking is scale-free by design: within a section the stored order IS the rank, but the
 * scores are incomparable across rungs and sections (light = cosine, standard = 0–3 labels,
 * strong = none — see memory `no-json-llm-io` / `project_jac`). So the drop order uses
 * *position fraction* within the section — the deepest tail entry relative to its section's
 * size drops first — rather than raw scores.
 */
import type { CvContent } from "@/lib/cv-doc";

/** Per-section floor the auto-fit never drops below (default 1 per non-empty section). */
export const MIN_KEEP: Record<string, number> = { skills: 3 };
const minKeep = (section: string) => MIN_KEEP[section] ?? 1;

/**
 * Ids in drop-first order. Ties: bigger section first, then section name. Favourites
 * (via `isFavourite`, built from the career DB) drop only after every non-favourite.
 * The first `minKeep(section)` entries of each section are never dropped.
 */
export function dropOrder(
  content: CvContent,
  isFavourite: (id: string) => boolean = () => false,
): string[] {
  type Cand = { id: string; frac: number; size: number; section: string; fav: boolean };
  const cands: Cand[] = [];
  for (const [section, list] of Object.entries(content)) {
    const floor = minKeep(section);
    list.forEach((e, i) => {
      if (i < floor) return;
      cands.push({
        id: e.id,
        frac: (i + 1) / list.length,
        size: list.length,
        section,
        fav: isFavourite(e.id),
      });
    });
  }
  cands.sort(
    (a, b) =>
      Number(a.fav) - Number(b.fav) ||
      b.frac - a.frac ||
      b.size - a.size ||
      a.section.localeCompare(b.section),
  );
  return cands.map((c) => c.id);
}

export function applyDrop(content: CvContent, ids: string[]): CvContent {
  const dropped = new Set(ids);
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    out[section] = list.filter((e) => !dropped.has(e.id));
  }
  return out;
}

/**
 * Page count of a rendered PDF, from its object dictionaries: one "/Type /Page" per page
 * ("/Type /Pages" is the tree node — excluded). react-pdf/pdfkit writes dictionaries
 * uncompressed, so a latin1 decode of the bytes is scannable.
 */
export function countPdfPages(pdfText: string): number {
  const m = pdfText.match(/\/Type\s*\/Page(?![a-zA-Z])/g);
  return m ? m.length : 0;
}

export type FitResult = {
  content: CvContent;
  droppedIds: string[];
  pages: number;
  fits: boolean; // false: even the min-keep floor overflows the budget
};

/**
 * Smallest drop count that fits `maxPages`, by binary search — the page count is
 * monotonically non-increasing in the drop count. `pagesFor` renders a candidate and counts
 * its pages (the only impure part, injected: ~log2(n) renders per export).
 */
export async function fitCv(
  content: CvContent,
  maxPages: number,
  pagesFor: (c: CvContent) => Promise<number>,
  isFavourite?: (id: string) => boolean,
): Promise<FitResult> {
  const order = dropOrder(content, isFavourite);
  const pagesAt = (k: number) => pagesFor(applyDrop(content, order.slice(0, k)));

  const full = await pagesAt(0);
  if (full <= maxPages) return { content, droppedIds: [], pages: full, fits: true };

  let lo = 0; // known: doesn't fit
  let hi = order.length;
  let hiPages = await pagesAt(hi);
  if (hiPages > maxPages) {
    return { content: applyDrop(content, order), droppedIds: order, pages: hiPages, fits: false };
  }
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    const p = await pagesAt(mid);
    if (p <= maxPages) {
      hi = mid;
      hiPages = p;
    } else {
      lo = mid;
    }
  }
  return {
    content: applyDrop(content, order.slice(0, hi)),
    droppedIds: order.slice(0, hi),
    pages: hiPages,
    fits: true,
  };
}
```

### 3. `frontend/src/lib/render/parts.ts` — pure (unit-tested)

```ts
/**
 * One entry → {heading, meta, body} — the shared shape the PDF templates and the markdown
 * exporter both render. Joins against the career DB; a missing row falls back to the stored
 * label (mirrors the editor's behaviour, guide 2).
 */
import {
  dateRange,
  entryId,
  joinEntry,
  SECTION_ORDER,
  type SectionKey,
} from "@/lib/cv-doc";
import type { CvEntry } from "@/lib/queries/generations";
import type {
  CertificationRow,
  CvEntriesResponse,
  EducationRow,
  JobRow,
  LanguageRow,
  ProjectRow,
  SkillRow,
} from "@/lib/queries/jac";

export type EntryParts = {
  heading: string;
  meta: string;
  body: string;
  favourite: boolean;
};

export function skillNames(db: CvEntriesResponse | undefined, ids: number[]): string {
  if (!db || ids.length === 0) return "";
  return ids
    .map((id) => db.skills.find((s) => s.id === id)?.name)
    .filter(Boolean)
    .join(", ");
}

export function entryParts(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): EntryParts {
  const row = db ? joinEntry(db, section, entry) : null;
  if (!row) return { heading: entry.label, meta: "", body: "", favourite: false };
  const favourite = "favourite" in row ? Boolean(row.favourite) : false;
  switch (section) {
    case "jobs": {
      const j = row as JobRow;
      return {
        heading: `${j.title} — ${j.company}`,
        meta: [dateRange(j.started, j.ended), skillNames(db, j.skills)]
          .filter(Boolean)
          .join(" · "),
        body: j.description,
        favourite,
      };
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      return {
        heading: head ? `${head} @ ${e.institution}` : e.institution,
        meta: [dateRange(e.started, e.ended), e.grade ? `Grade: ${e.grade}` : ""]
          .filter(Boolean)
          .join(" · "),
        body: e.description,
        favourite,
      };
    }
    case "projects": {
      const p = row as ProjectRow;
      return {
        heading: p.name,
        meta: [dateRange(p.started, p.ended), skillNames(db, p.skills), p.url]
          .filter(Boolean)
          .join(" · "),
        body: p.description,
        favourite,
      };
    }
    case "skills": {
      const s = row as SkillRow;
      return {
        heading: s.name,
        meta: `${s.proficiency} (${s.category})`,
        body: "",
        favourite,
      };
    }
    case "certifications": {
      const c = row as CertificationRow;
      return {
        heading: `${c.name} — ${c.issuer}`,
        meta: c.issued_on ? `Issued: ${c.issued_on}` : "",
        body: c.description,
        favourite,
      };
    }
    case "languages": {
      const l = row as LanguageRow;
      return { heading: l.name, meta: l.fluency, body: "", favourite };
    }
  }
}

/** id → favourite?, across all sections — feeds fitCv so favourites drop last. */
export function isFavouriteLookup(
  db: CvEntriesResponse | undefined,
): (id: string) => boolean {
  if (!db) return () => false;
  const favs = new Set<string>();
  for (const section of SECTION_ORDER) {
    for (const row of db[section]) {
      if (row.favourite) favs.add(entryId(section, row.id));
    }
  }
  return (id) => favs.has(id);
}
```

### 4. `frontend/src/lib/render/templates.tsx`

```tsx
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
            {!compact && p.body ? <Text style={styles.body}>{p.body}</Text> : null}
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
    date: { position: "absolute", top: mm(45), right: mm(20), fontSize: base * 0.9 },
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
  const returnLine = [snd.name, snd.street, [snd.zip, snd.city].filter(Boolean).join(" ")]
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
  const contactLine = [snd.email, snd.phone, snd.website].filter(Boolean).join(" · ");

  return (
    <Page size={spec.page.size} style={styles.page}>
      <View style={styles.addressField} fixed>
        {returnLine ? <Text style={styles.returnLine}>{returnLine}</Text> : null}
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

export async function renderPdfBlob(doc: ReactElement): Promise<Blob> {
  return pdf(doc).toBlob();
}

export async function pdfPages(doc: ReactElement): Promise<number> {
  const blob = await renderPdfBlob(doc);
  const bytes = await blob.arrayBuffer();
  return countPdfPages(new TextDecoder("latin1").decode(bytes));
}
```

Subtle: `fontFamily: "Helvetica-Bold"` is a built-in react-pdf font; if a spec ever names a
non-built-in family, registering it (`Font.register`) becomes a follow-up — out of scope here.

### 5. `frontend/src/lib/export.ts` — pure builders (unit-tested) + download

```ts
/**
 * Markdown/JSON exports. The markdown mirrors the backend renderers (jac/render.py
 * CvRender.export_md and jac/cover_letter.py render_markdown) so every export format tells
 * the same story.
 */
import {
  joinEntry,
  SECTION_ORDER,
  SECTION_TITLES,
  type CvContent,
} from "@/lib/cv-doc";
import type { LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { entryParts } from "@/lib/render/parts";

export function cvToMarkdown(
  name: string,
  content: CvContent,
  db: CvEntriesResponse | undefined,
): string {
  const lines: string[] = [`# ${name}`, ""];
  for (const section of SECTION_ORDER) {
    const entries = content[section] ?? [];
    if (entries.length === 0) continue;
    lines.push(`## ${SECTION_TITLES[section]}`, "");
    for (const e of entries) {
      const p = entryParts(db, section, e);
      lines.push(`### ${p.favourite ? "★ " : ""}${p.heading}`);
      if (p.meta) lines.push(p.meta);
      if (p.body) lines.push(p.body);
      lines.push("");
    }
  }
  return lines.join("\n").replace(/\n+$/, "") + "\n";
}

/** Mirrors CoverLetter.render_markdown: sender block, recipient block, date, subject, …, name. */
export function letterToMarkdown(meta: LetterMeta, body: string): string {
  const snd = meta.sender;
  const rcp = meta.recipient;
  const out: string[] = [];
  const push = (line: string | undefined) => {
    if (line) out.push(line);
  };

  push(snd.name);
  push(snd.street);
  push(snd.address_line2);
  push([snd.zip, snd.city].filter(Boolean).join(" "));
  push(snd.country);
  push([snd.email, snd.phone].filter(Boolean).join(" · "));
  out.push("");

  push(rcp.company);
  push(rcp.contact_name);
  push(rcp.street);
  push(rcp.address_line2);
  push([rcp.zip, rcp.city].filter(Boolean).join(" "));
  push(rcp.country);
  out.push("");

  out.push(meta.date, "", `**${meta.subject}**`, "", meta.salutation, "", body, "");
  out.push(meta.closing, "", snd.name ?? "");
  return out.join("\n").replace(/\n+$/, "") + "\n";
}

export type ExportScope = "complete" | "cv" | "letter";

/** JSON export: the selection joined with the career-DB rows behind it (frozen snapshot). */
export function exportJson(
  scope: ExportScope,
  args: {
    content: CvContent; // active (deselected stripped); pass the fitted one if you want WYSIWYG
    meta: LetterMeta;
    body: string;
    db: CvEntriesResponse | undefined;
  },
): string {
  const cv = Object.fromEntries(
    SECTION_ORDER.map((section) => [
      section,
      (args.content[section] ?? []).map((e) => ({
        ...e,
        entry: joinEntry(args.db, section, e),
      })),
    ]),
  );
  const letter = { meta: args.meta, body: args.body };
  const payload =
    scope === "cv" ? { cv } : scope === "letter" ? { letter } : { cv, letter };
  return JSON.stringify(payload, null, 2);
}

/* ---------- downloads (browser-only, not unit-tested) ---------- */

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadText(text: string, filename: string, mime = "text/plain") {
  downloadBlob(new Blob([text], { type: mime }), filename);
}
```

### 6. Small `cv-doc.ts` additions (if guide 2 landed without them)

`parts.ts` imports `dateRange` and `entryId` from `cv-doc.ts` — make sure they are exported
there:

```ts
export function dateRange(started: string | null, ended: string | null): string {
  return `${started ?? "?"}–${ended ?? "present"}`;
}

export function entryId(section: SectionKey, pk: number): string {
  return `${SINGULAR[section]}:${pk}`;
}
```

(and `labelFor` / `fromCareerDb` use them internally.)

### 7. `frontend/src/routes/_authenticated/applications/$applicationId.tsx` — `ExportCard`

New imports:

```tsx
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { activeContent } from "@/lib/cv-doc";
import { normalizeLetterMeta } from "@/lib/letter-doc";
import { fitCv, type FitResult } from "@/lib/render/fit";
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
  exportJson,
  letterToMarkdown,
  type ExportScope,
} from "@/lib/export";
```

Mount it in `ApplicationDetailPage` under the content card:

```tsx
      <ApplicationContentCard app={app.data} />
      <ExportCard app={app.data} />
```

The card. It exports the **saved** application state (drafts must be saved first — the note in
the card says so). PDF building is async: fit the CV to the layout budget, measure the letter,
then compose per scope:

```tsx
/* ---------- export ---------- */

type BuiltPdf = {
  blob: Blob;
  fit: FitResult | null; // null for letter-only
  letterPages: number | null;
};

function ExportCard({ app }: { app: ApplicationRow }) {
  const layouts = useFullList<LayoutRow>("layouts");
  const layout = layouts.data?.find((l) => l.id === app.layout);
  const spec = useLayoutSpec(layout);
  const careerDb = useCvEntries();
  const [scope, setScope] = useState<ExportScope>("complete");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ url: string; info: BuiltPdf } | null>(null);

  const meta = normalizeLetterMeta(app.letter_meta);
  const name = meta.sender.name || "CV";
  const stem = `application-${app.id}-${scope}`;

  async function buildPdf(): Promise<BuiltPdf> {
    if (!spec.data) throw new Error("layout spec not loaded");
    const s = spec.data;
    const db = careerDb.data;
    const active = activeContent(app.cv_content ?? {});

    const fit =
      scope === "letter"
        ? null
        : await fitCv(
            active,
            s.cv.pages,
            (c) => pdfPages(<CvDocument spec={s} name={name} content={c} db={db} />),
            isFavouriteLookup(db),
          );
    const letterPages =
      scope === "cv"
        ? null
        : await pdfPages(<LetterDocument spec={s} meta={meta} body={app.cover_letter} />);

    const doc =
      scope === "cv" ? (
        <CvDocument spec={s} name={name} content={fit!.content} db={db} />
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

  function onDownloadPdf() {
    void withBusy(async () => {
      const built = await buildPdf();
      downloadBlob(built.blob, `${stem}.pdf`);
      notify(built);
    });
  }

  function onPreview() {
    void withBusy(async () => {
      const built = await buildPdf();
      setPreview({ url: URL.createObjectURL(built.blob), info: built });
    });
  }

  function onDownloadMd() {
    const db = careerDb.data;
    const active = activeContent(app.cv_content ?? {});
    const cvMd = cvToMarkdown(name, active, db);
    const letterMd = letterToMarkdown(meta, app.cover_letter);
    const md =
      scope === "cv" ? cvMd : scope === "letter" ? letterMd : `${letterMd}\n---\n\n${cvMd}`;
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
          Exports the saved application content — save your edits first. The CV is auto-fitted
          to the layout's page budget by dropping the lowest-ranked entries; the letter is
          never cut, only flagged.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label className="text-xs">Scope</Label>
            <Select value={scope} onValueChange={(v) => setScope(v as ExportScope)}>
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
          <Button size="sm" onClick={onDownloadPdf} disabled={busy || !spec.data}>
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
            <iframe src={preview.url} title="PDF preview" className="h-full w-full" />
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
```

(on preview open, `notify(built)` can also be called for the fit/overflow toasts — add
`notify(built)` after `setPreview(...)` in `onPreview`.)

## Tests (written by the AI, already on this branch — start red)

- `frontend/tests/lib/render-fit.test.ts` — `dropOrder` (min-keep floors, tail-first within a
  section, bigger-section-first tie-break, favourites last), `applyDrop`, `countPdfPages`
  (synthetic PDF text, `/Pages` excluded), `fitCv` (no-drop when it fits, minimal drop via a
  fake `pagesFor`, `fits: false` when even the floor overflows).
- `frontend/tests/lib/render-spec.test.ts` — `parseLayoutSpec` (fallbacks, legacy
  `"education"` → `"educations"`, partial specs), `templatePath` (absolute URL → same-origin
  path; relative passthrough).
- `frontend/tests/lib/export.test.ts` — `entryParts` (joined rows vs. label fallback,
  favourite flag), `cvToMarkdown` (section order/headings, ★, meta/body lines),
  `letterToMarkdown` (block order, bold subject, empty lines collapse), `exportJson` (scope
  shapes, joined `entry` field).

```bash
cd frontend && npx vitest run tests/lib/render-fit.test.ts tests/lib/render-spec.test.ts tests/lib/export.test.ts
npm test   # full suite once green
```

## Verification

1. `npm install @react-pdf/renderer`; `npm test` red → green; `npm run build` clean.
2. Application with generated content + `default` layout (1-page CV): Preview PDF (complete) →
   letter page first (recipient sits in the window position, date right, bold subject), then a
   single CV page; if content overflowed, a toast reports how many entries were dropped.
3. Switch the layout to `two-page` → preview again → CV may now span two pages and drops fewer
   entries.
4. Deselect a few entries (guide 2), save, re-export: the deselected ones are gone *before*
   fitting; reorder a tail entry to the top → it survives the fit.
5. Write a 2-page letter body → export → "cover letter exceeds one page" warning, letter not
   truncated.
6. All six download combos (3 scopes × md/json) produce sensible files; the md CV matches the
   backend `cv_test` artifact style; `complete.pdf` ≤ 3 pages when the letter fits.
