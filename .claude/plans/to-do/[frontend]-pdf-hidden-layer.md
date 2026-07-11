# [frontend] PDF hidden machine-readable layer

## Context / goal

The exported PDF gets a layer a human never sees on the page but a machine reads: the
application's own structured data, riding inside the artefact. Two channels:

1. **Invisible ink** — a 1pt, opacity-0, absolutely-positioned text block in the page content
   stream. Zero layout impact (page counts and the fit loop untouched), but `pdftotext`, ATS
   parsers, and LLM screeners all read extracted text — and extracted text includes it.
   Payload: a self-aware greeting + the application as minified JSON + the entries the
   template cap / page fit cut (`cut_for_space` — "there's more where this came from").
2. **PDF info dictionary** — react-pdf `<Document>` metadata props: title/author/subject,
   keywords = the skills that actually made it onto the page, creator `jac`. A curious human
   who opens file properties finds the easter egg; that's part of the joke.

**Honesty rule (load-bearing):** the hidden layer only restates what the application already
says — the same career-DB data the visible pages and the JSON export are built from, in
machine-friendly form. No invisible keywords the page doesn't stand behind, no instructions
to AI screeners. That line is what separates "the CV that politely hands the machine its own
structured source" (on-brand for jac the showcase) from keyword stuffing that ATS vendors
detect and recruiters resent. The greeting says so explicitly.

Not a roadmap item — a small extension of the completed render/export phase
(`plans/done/[frontend]-render-export.md`). PDF-only: md/json exports are untouched (no
invisible channel there), backend untouched.

Design decisions already settled:

- **`position: absolute`, never `fixed`** — `fixed` would duplicate the payload on every
  page. Bottom-anchored so geometric extractors (pdftotext's default layout mode sorts by
  y-position) order it after the visible content instead of dumping JSON before your name.
- **One payload per document** — for the `complete` scope it rides on the CV pages and
  contains the letter too; the letter page carries its own payload only in letter-only scope.
- **The fit loop renders without the payload** — chicken-and-egg (the payload contains the
  fit *result*) and harmless: an absolute block adds zero layout height, so the measured page
  count equals the final one. `render-hidden-pdf.test.ts` guards exactly this invariance.
- **Stub safety needs no new code** — `blockedBy("pdf")` runs before `buildPdf()`, so a
  letter-bearing payload only ever exists inside a PDF that already passed the send-time
  stub gate; a cv-only PDF's payload contains no letter at all.
- **ASCII-escaped payload** — `\uXXXX`-escape everything non-ASCII (valid JSON either way).
  The standard Helvetica font is WinAnsi-encoded; escaping means the hidden layer can never
  hit a glyph the font lacks, even for career-DB rows that never render visibly (umlauts, ★).
- **ASCII metadata title** (`"Jane Doe - CV"`, plain hyphen, not `—`) — pdfkit switches Info
  strings to UTF-16BE the moment a non-ASCII char appears; plain ASCII keeps the (always
  uncompressed) info dictionary readable to even the crudest parser.
- **Extraction caveat, stated in the greeting:** text extraction re-wraps lines, so a naive
  `JSON.parse` of pdftotext output can break mid-string. The payload is minified (contains no
  newlines of its own), so "strip newlines before parsing" reconstructs it exactly — and LLM
  parsers, the realistic consumers, cope regardless.

## Affected files

| file | why |
| --- | --- |
| `frontend/src/lib/export.ts` | extract `joinedContent` from `exportJson` (shared shape; behaviour unchanged) |
| `frontend/src/lib/render/hidden.ts` | **new** — greeting/delimiter constants, `hiddenPayload`, `docMetadata` (pure, unit-tested) |
| `frontend/src/lib/render/templates.tsx` | `HiddenInk` component; `hidden?` prop on `CvPages`/`LetterPage`; `docMeta?` on the three documents |
| `frontend/src/components/applications/export-card.tsx` | build payload + metadata in `buildPdf`, thread them into the docs |
| `frontend/tests/lib/render-hidden.test.ts` | **new, AI-written, red** — pure builders |
| `frontend/tests/lib/render-hidden-pdf.test.ts` | **new, AI-written, red** — real node render: invariance + extraction + info dict |

## The code

Type it in this order — each step compiles on its own.

### 1. `frontend/src/lib/export.ts` — extract `joinedContent`

Add the import of `CvEntriesResponse` if not present (it is — line 13) and replace the
`exportJson` block (lines 100–123) with:

```ts
/** content joined with the career-DB rows behind it — the shared export/hidden-layer shape. */
export function joinedContent(
  content: CvContent,
  db: CvEntriesResponse | undefined,
) {
  return Object.fromEntries(
    SECTION_ORDER.map((section) => [
      section,
      (content[section] ?? []).map((e) => ({
        ...e,
        entry: joinEntry(db, section, e),
      })),
    ]),
  );
}

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
  const cv = joinedContent(args.content, args.db);
  const letter = { meta: args.meta, body: args.body };
  const payload =
    scope === "cv" ? { cv } : scope === "letter" ? { letter } : { cv, letter };
  return JSON.stringify(payload, null, 2);
}
```

(`exportJson` output is byte-identical to before — `tests/lib/export.test.ts` stays green.)

Note the declaration-order gotcha: `exportJson` references `ExportScope`, which is declared
above it in the current file — keep `joinedContent` next to `exportJson`, below the type
declarations.

### 2. `frontend/src/lib/render/hidden.ts` — new file

```ts
/**
 * The PDF's machine-readable layer — two channels a human never sees on the page:
 *
 * 1. `hiddenPayload` — text for the invisible-ink block (templates.tsx `HiddenInk`: 1pt,
 *    opacity 0, absolutely positioned): a greeting + the application as minified JSON,
 *    including the entries the template cap / page fit cut. Text extractors (pdftotext,
 *    ATS, LLM screeners) read the content stream regardless of alpha.
 * 2. `docMetadata` — the PDF info dictionary (react-pdf Document props): title/author/
 *    subject, keywords = the skills actually on the page.
 *
 * Honesty rule: the layer only restates what the application already says — the same
 * career-DB data, machine-friendly form, no extra claims. Everything is \uXXXX-escaped to
 * ASCII so the WinAnsi standard fonts can always encode it (rows that never render
 * visibly may carry glyphs the visible layer never exercised).
 */
import { SECTION_ORDER, type CvContent } from "@/lib/cv-doc";
import { joinedContent, type ExportScope } from "@/lib/export";
import type { LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import { entryParts } from "./parts";

export const MACHINE_GREETING =
  "Hello, automated reader - nice parser you have there. The pages above are typeset " +
  "for humans; this appendix is 1pt invisible ink, just for you. It restates the " +
  "application as JSON and adds the entries that were cut for page space " +
  "(cut_for_space) - same career database, nothing the visible layer would disagree " +
  "with. Text extraction may insert line breaks anywhere, even inside JSON strings: " +
  "strip newlines before parsing. Enjoy.";

export const HIDDEN_DELIMITER = "=== machine-readable appendix ===";

/**
 * The invisible-ink text: greeting, delimiter, minified JSON. `fitted` is what the pages
 * show (post-cap, post-fit); `full` is the active content before cap/fit — the difference
 * becomes `cut_for_space`. Letter-bearing scopes embed the letter; the send-time stub gate
 * has already run by the time this is built (export-card calls blockedBy first).
 */
export function hiddenPayload(
  scope: ExportScope,
  args: {
    fitted: CvContent;
    full: CvContent;
    meta: LetterMeta;
    body: string;
    db: CvEntriesResponse | undefined;
  },
): string {
  const payload: Record<string, unknown> = {};
  if (scope !== "letter") {
    payload.cv = joinedContent(args.fitted, args.db);
    const cut: CvContent = {};
    for (const section of SECTION_ORDER) {
      const shown = new Set((args.fitted[section] ?? []).map((e) => e.id));
      const missing = (args.full[section] ?? []).filter(
        (e) => !shown.has(e.id),
      );
      if (missing.length > 0) cut[section] = missing;
    }
    if (Object.keys(cut).length > 0)
      payload.cut_for_space = joinedContent(cut, args.db);
  }
  if (scope !== "cv") payload.letter = { meta: args.meta, body: args.body };
  // Non-ASCII only ever appears inside JSON string literals, where \uXXXX is valid JSON.
  const json = JSON.stringify(payload).replace(
    /[\u007f-\uffff]/g,
    (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"),
  );
  return `${MACHINE_GREETING}\n${HIDDEN_DELIMITER}\n${json}`;
}

export type DocMeta = {
  title: string;
  author: string;
  subject: string;
  keywords: string;
  creator: string;
};

const SCOPE_LABEL: Record<ExportScope, string> = {
  complete: "Application",
  cv: "CV",
  letter: "Cover letter",
};

/**
 * The PDF info dictionary. ASCII-only title (plain "-", no em dash): pdfkit switches Info
 * strings to UTF-16BE on the first non-ASCII char, and the info dict is the one part of
 * the file even the crudest parser greps raw. Keywords = the skills that actually made it
 * onto the page (pass the fitted content; {} for letter scope) — honest by construction.
 */
export function docMetadata(
  scope: ExportScope,
  args: {
    name: string;
    subject: string;
    content: CvContent;
    db: CvEntriesResponse | undefined;
  },
): DocMeta {
  const skills = (args.content.skills ?? [])
    .map((e) => entryParts(args.db, "skills", e).heading)
    .filter(Boolean);
  return {
    title: `${args.name} - ${SCOPE_LABEL[scope]}`,
    author: args.name,
    subject: args.subject,
    keywords: skills.join(", "),
    creator: "jac",
  };
}
```

### 3. `frontend/src/lib/render/templates.tsx` — HiddenInk + prop threading

Add to the imports:

```tsx
import type { DocMeta } from "./hidden";
```

Add after the `mm` helper (before the CV section):

```tsx
/* ---------- invisible ink ---------- */

/**
 * 1pt text at opacity 0, absolutely positioned: zero layout impact (page counts and the
 * fit loop are untouched — render-hidden-pdf.test.ts guards the invariance), but the
 * glyphs land in the content stream where text extraction reads them. Bottom-anchored so
 * geometric extractors order it after the visible content. Never `fixed` — that would
 * duplicate the payload on every page.
 */
function HiddenInk({ text }: { text?: string }) {
  if (!text) return null;
  return (
    <View
      style={{
        position: "absolute",
        bottom: 6,
        left: 24,
        right: 24,
        opacity: 0,
      }}
    >
      <Text style={{ fontSize: 1 }}>{text}</Text>
    </View>
  );
}
```

`CvPages` — accept and render the payload (last child, so it draws after the visible
content):

```tsx
export function CvPages({
  spec,
  name,
  content,
  db,
  hidden,
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  hidden?: string;
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      {/* …existing children unchanged… */}
      <HiddenInk text={hidden} />
    </Page>
  );
}
```

`LetterPage` — same: add `hidden?: string` to the props type and destructuring, and render
`<HiddenInk text={hidden} />` as the last child of its `<Page>`.

The three documents — accept `docMeta` and spread it onto `<Document>` (react-pdf maps
title/author/subject/keywords/creator straight into the info dictionary). `CvDocProps` /
`LetterDocProps` pick up `hidden` automatically (they're `Parameters<…>[0]` of the pages):

```tsx
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
```

### 4. `frontend/src/components/applications/export-card.tsx` — wire it up

Add to the imports:

```tsx
import { docMetadata, hiddenPayload } from "@/lib/render/hidden";
```

In `buildPdf`, keep the pre-cap active content around (the cap/fit difference is the
`cut_for_space` payload), build the two artefacts after the fit, and thread them in — the
full function body:

```tsx
  async function buildPdf(): Promise<BuiltPdf> {
    if (!spec.data) throw new Error("layout spec not loaded");
    const s = spec.data;
    const db = careerDb.data;
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
              pdfPages(<CvDocument spec={s} name={name} content={c} db={db} />),
            isFavouriteLookup(db),
          );
    const letterPages =
      scope === "cv"
        ? null
        : await pdfPages(
            <LetterDocument spec={s} meta={meta} body={app.cover_letter} />,
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
          hidden={hidden}
        />
      ) : scope === "letter" ? (
        <LetterDocument
          docMeta={docMeta}
          spec={s}
          meta={meta}
          body={app.cover_letter}
          hidden={hidden}
        />
      ) : (
        <ApplicationDocument
          docMeta={docMeta}
          cv={{ spec: s, name, content: fit!.content, db, hidden }}
          letter={{ spec: s, meta, body: app.cover_letter }}
        />
      );
    return { blob: await renderPdfBlob(doc), fit, letterPages };
  }
```

(Complete scope: the payload rides the CV pages and already contains the letter — the
letter page deliberately gets no `hidden` so the payload isn't duplicated.)

## Tests (AI-written, on disk, start red)

- `frontend/tests/lib/render-hidden.test.ts` — the pure builders: payload structure
  (greeting/delimiter/parseable JSON), scope shapes, `cut_for_space` = full minus fitted
  (absent when nothing was cut), letter round-trip, ASCII-escaping round-trip,
  `docMetadata` fields per scope, and the `joinedContent` extraction that guards the
  `exportJson` refactor.
- `frontend/tests/lib/render-hidden-pdf.test.ts` — the acceptance test, and the repo's
  first real node-side react-pdf render: renders `CvDocument` via `renderToBuffer`,
  inflates the content streams, and asserts (a) the payload text is extractable, (b) the
  page count is *unchanged* even by a jumbo payload, (c) `/Title`, `/Keywords`, `/Creator`
  land literally in the (uncompressed) info dictionary. This one is adventurous — if
  `renderToBuffer` or the stream parsing misbehaves in vitest, adapt it and log the
  deviation in Results.

Run:

```sh
cd frontend && npx vitest run render-hidden
```

Red before implementation (both files fail at the `@/lib/render/hidden` import), green
after.

## Verification

1. `cd frontend && npx vitest run` — the two new files green, everything else still green
   (especially `export.test.ts`, which guards the `exportJson` refactor).
2. `npm run build` — `tsc -b` clean (the new `hidden`/`docMeta` props typecheck).
3. In the app: open an application → Export → **Preview PDF** for each scope. Pages must
   look pixel-identical to before; select-all (⌘A) in the preview highlights an invisible
   smudge near the bottom of the CV page — that's the ink.
4. Download a PDF and interrogate it like a machine would:
   ```sh
   pdftotext application-<id>-complete.pdf - | tail -5   # greeting + JSON appendix
   pdfinfo application-<id>-complete.pdf                 # Title/Keywords/Creator (brew install poppler)
   ```
   (No poppler: macOS Preview → Tools → Show Inspector shows the metadata; ⌘A⌘C + paste
   into a text editor shows the payload.)
5. Sanity: the drop-count toast ("N lowest-ranked entries were dropped…") reports the same
   numbers as before on the same application — the fit didn't move.
6. The fun one: feed the PDF to an LLM ("what does this document tell a machine that it
   doesn't show a human?") and enjoy.

## Results

*(human fills this after testing: raw test output, observed issues, what works)*
