# [frontend] polished-render

> **Sits between the portfolio stack and the three letter-pipeline rework guides.** Replaces the parked
> `[fullstack]-latex-render` (in `backlog/`): instead of compiling LaTeX server-side, we make the
> existing **react-pdf** render as polished as the moderncv gold standard — no TeX install, no server
> CPU, no compile subprocess. Branch: `frontend/polished-render` (pointer created off the current tip;
> `git branch -f frontend/polished-render main` to re-base on `main`). Volatile/dev phase, one clean
> break ([[no-compat-clean-breaks]]). Cert-attachment merge (`\includepdf` equivalent) is its own
> follow-up guide, `[frontend]-cert-attachments`.

## Context / goal

The gold standard is a **moderncv "classic"** document (`~/Documents/01 Bewerbungen/dawndenim/
cv_Hirschhausen_de.tex`): colored name header, a **dated left column** per entry, ruled section titles,
`\cvitem` label/value rows for skills/languages, a signature image in the letter closing. Lukas's call
(2026-07-23): *"I don't need TeX, I need it pretty"* — and prefers react-pdf if it can look as good,
for zero ops. It can. This guide redesigns `lib/render/*` to that look; the render already consumes the
tailored `cv_content` + letter, so the tailoring is untouched — only the styling changes.

Decisions:

1. **Match moderncv-classic closely** — dated left column, colored/ruled section titles, `\cvitem`-style
   grouped skills. (Not the old single-column ATS layout — see #3.)
2. **Helvetica for v1** (react-pdf built-in, zero font-licensing/registration). A custom embedded font
   is an optional later polish; layout + color + spacing carry the look.
3. **The two-column look reverses the single-column ATS stance** in [[cv-render-export-decision]] — that
   is fine **because the hidden-ink layer already injects a clean linear text run** for parsers
   (`HiddenInk` in `templates.tsx`, `hiddenPayload` in `hidden.ts`). Pretty layout on top, machine-
   readable text underneath. Update that memory at `/wrap-up`.
4. **Signature = a per-user uploaded asset** on `UserProfile` (media, never git), rendered conditionally
   in the letter closing (`{signatureUrl && <Image/>}`). No `\IfFileExists` ceremony.

The date currently lives inside `entryParts().meta`; the moderncv date-column needs it **split out** into
its own `EntryParts.date`. That ripples to `cvToMarkdown` (recombine date+meta into one md line, so the
markdown export is unchanged) and to `export.test.ts` (updated). The density + header tests in
`render-templates.test.ts` stay green — the `cvStyles` keys and their values are preserved; the redesign
only **adds** keys and changes JSX.

## Affected files

| path | change |
| --- | --- |
| `frontend/src/lib/render/parts.ts` | `EntryParts` gains `date`; split the date out of `meta`; add `skillGroups()` (skills → category-labelled `\cvitem` rows) |
| `frontend/src/lib/export.ts` | `cvToMarkdown`: recombine `date` + `meta` into one line (markdown output unchanged) |
| `frontend/src/lib/render/templates.tsx` | moderncv-classic redesign: `cvStyles` two-column + section rule; `CvSectionView` dated-left-column + grouped compact rows; header rule; `LetterPage` optional signature `<Image>` |
| `backend/spa/models.py` | `UserProfile.signature` ImageField |
| `backend/spa/serializers.py` | expose `signature` on the profile serializer |
| `backend/spa/migrations/000X_*.py` | `makemigrations spa` — `AddField signature` |
| `frontend/src/lib/queries/profile.ts` | `ProfileRow.signature` |
| `frontend/src/components/applications/export-card.tsx` | pass `signatureUrl` into `LetterDocument`/`ApplicationDocument` |
| `frontend/src/routes/_authenticated/account/…` (profile form) | a signature file upload (mirror the avatar upload) |

---

## The code

### 1. `frontend/src/lib/render/parts.ts` — split the date, group skills

`EntryParts` gains `date`; every block entry moves its date range there and keeps `meta` for the
*secondary* line (skills / grade / url); compact entries leave `date` empty. Add `skillGroups`.

```ts
export type EntryParts = {
  heading: string;
  date: string; // the moderncv left-column date/period; "" for skills/languages
  meta: string; // secondary line: skills, grade, url — NO date
  body: string;
  favourite: boolean;
};

export function entryParts(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): EntryParts {
  const row = db ? joinEntry(db, section, entry) : null;
  if (!row)
    return { heading: entry.label, date: "", meta: "", body: "", favourite: false };
  const favourite = "favourite" in row ? Boolean(row.favourite) : false;
  switch (section) {
    case "jobs": {
      const j = row as JobRow;
      return {
        heading: `${j.title} — ${j.company}`,
        date: dateRange(j.started, j.ended),
        meta: skillNames(db, j.skills),
        body: j.description,
        favourite,
      };
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      return {
        heading: head ? `${head} @ ${e.institution}` : e.institution,
        date: dateRange(e.started, e.ended),
        meta: e.grade ? `Grade: ${e.grade}` : "",
        body: e.description,
        favourite,
      };
    }
    case "projects": {
      const p = row as ProjectRow;
      return {
        heading: p.name,
        date: dateRange(p.started, p.ended),
        meta: [skillNames(db, p.skills), p.url].filter(Boolean).join(" · "),
        body: p.description,
        favourite,
      };
    }
    case "skills": {
      const s = row as SkillRow;
      return {
        heading: s.name,
        date: "",
        meta: `${s.proficiency} · ${s.category}`,
        body: "",
        favourite,
      };
    }
    case "certifications": {
      const c = row as CertificationRow;
      return {
        heading: `${c.name} — ${c.issuer}`,
        date: c.issued_on ?? "",
        meta: "",
        body: c.description,
        favourite,
      };
    }
    case "languages": {
      const l = row as LanguageRow;
      return { heading: l.name, date: "", meta: l.fluency, body: "", favourite };
    }
  }
}
```

Add below `entryParts` (grouping mirrors the backend Skill categories):

```ts
const SKILL_CATEGORY_LABELS: Record<string, string> = {
  technical: "Technical",
  soft: "Soft",
  domain: "Domain",
  other: "Other",
};
const SKILL_CATEGORY_ORDER = ["technical", "soft", "domain", "other"];

/** Skills → moderncv \cvitem rows: one {label, names} per non-empty category, in category order,
 *  preserving cv_content order within a category. A row missing from the DB falls back to its label. */
export function skillGroups(
  db: CvEntriesResponse | undefined,
  entries: CvEntry[],
): { label: string; names: string }[] {
  const groups: Record<string, string[]> = {};
  for (const e of entries) {
    const row = db ? (joinEntry(db, "skills", e) as SkillRow | null) : null;
    const cat = row?.category ?? "other";
    (groups[cat] ??= []).push(row?.name ?? e.label);
  }
  return SKILL_CATEGORY_ORDER.filter((c) => groups[c]?.length).map((c) => ({
    label: SKILL_CATEGORY_LABELS[c] ?? c,
    names: groups[c].join(", "),
  }));
}
```

### 2. `frontend/src/lib/export.ts` — keep markdown output stable

`cvToMarkdown` now recombines `date` + `meta` into the single line it used to emit:

```ts
    for (const e of entries) {
      const p = entryParts(db, section, e);
      lines.push(`### ${p.favourite ? "★ " : ""}${p.heading}`);
      const metaLine = [p.date, p.meta].filter(Boolean).join(" · ");
      if (metaLine) lines.push(metaLine);
      if (p.body) lines.push(p.body);
      lines.push("");
    }
```

### 3. `frontend/src/lib/render/templates.tsx` — the moderncv-classic redesign

Add `Image` to the react-pdf import:

```tsx
import {
  Document,
  Image,
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
  type DocumentProps,
} from "@react-pdf/renderer";
```

Import the new helper:

```tsx
import { entryParts, skillGroups } from "./parts";
```

`cvStyles` — **preserve every existing key + value** (the density tests pin them), **add** the two-column
+ rule styles:

```tsx
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
    name: { fontSize: base * 2, marginBottom: base * 0.4, color: spec.colors.accent },
    subtitle: { fontSize: base * 1.1, color: spec.colors.muted, marginBottom: base * 0.4 },
    contact: { color: spec.colors.muted, fontSize: base * 0.9, marginBottom: base * 0.4 },
    summary: { marginBottom: base * 0.4, lineHeight: 1.4 },
    // The moderncv accent line under the header block.
    headerRule: {
      borderBottomWidth: 1.5,
      borderBottomColor: spec.colors.accent,
      marginTop: base * 0.3,
      marginBottom: base * 0.4,
    },
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
      width: mm(22),
      color: spec.colors.muted,
      fontSize: small,
      paddingRight: base * 0.5,
    },
    content: { flex: 1 },
    entry: { marginBottom: base / 3 }, // kept — density decision
    heading: { fontFamily: `${spec.font.family}-Bold` },
    meta: { color: spec.colors.muted, fontSize: small },
    body: { marginTop: base * 0.15 },
    compact: { fontSize: small, marginBottom: base / 3 },
  });
}
```

`CvSectionView` — dated left column for block sections; `\cvitem`-style label/value rows for compact
(skills grouped by category; languages one row):

```tsx
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
      <Text style={styles.sectionTitle}>{SECTION_TITLES[section]}</Text>
      {entries.map((e) => {
        const p = entryParts(db, section, e);
        return (
          <View key={e.id} style={styles.row} wrap={false}>
            <Text style={styles.hints}>{p.date}</Text>
            <View style={styles.content}>
              <Text style={styles.heading}>
                {p.favourite ? "★ " : ""}
                {p.heading}
              </Text>
              {p.meta ? <Text style={styles.meta}>{p.meta}</Text> : null}
              {p.body ? <Text style={styles.body}>{p.body}</Text> : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}
```

`CvPages` — add an optional `subtitle` and the header rule (header text order name → summary → contact is
preserved, so the header-order test stays green):

```tsx
export function CvPages({
  spec,
  name,
  content,
  db,
  contact,
  summary,
  subtitle,
  hidden,
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  contact?: string;
  summary?: string;
  subtitle?: string;
  hidden?: string;
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      <Text style={styles.name}>{name}</Text>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      {summary ? <Text style={styles.summary}>{summary}</Text> : null}
      {contact ? <Text style={styles.contact}>{contact}</Text> : null}
      <View style={styles.headerRule} />
      {spec.cv.sections.map((s) => (
        <CvSectionView key={s} section={s as SectionKey} content={content} db={db} styles={styles} />
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
```

`LetterPage` — an optional signature image before the typed name in the closing:

```tsx
// in letterStyles(spec), add:
    signatureImg: { width: mm(40), marginTop: base * 0.5, marginBottom: base * 0.2 },
```

```tsx
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
  // … unchanged up to the closing …
      <Text style={styles.para}>{meta.closing}</Text>
      {signatureUrl ? <Image src={signatureUrl} style={styles.signatureImg} /> : null}
      <Text style={styles.signature}>{snd.name}</Text>
  // … unchanged (footer + HiddenInk) …
}
```

The `LetterDocProps`/`ApplicationDocument` types flow from `Parameters<typeof LetterPage>[0]`, so
`signatureUrl` propagates to `LetterDocument` and `ApplicationDocument` automatically — no other type
edits.

### 4. Backend — the per-user signature asset

`backend/spa/models.py`, on `UserProfile` beside `avatar`:

```python
    # A transparent-background signature image (PNG) rendered in the JAC cover-letter closing.
    # Media-stored (never git). Optional — the render just omits it when absent.
    signature = models.ImageField(upload_to="signatures", blank=True)
```

Expose it on the profile serializer (the one serving `/api/spa/profile/`) — add `"signature"` to its
`fields`. `ImageField` serializes to a URL on read and accepts an upload on write (the profile form
already does multipart for `avatar`; the signature rides the same submit).

```bash
cd backend && python manage.py makemigrations spa && python manage.py migrate
```

### 5. Frontend — plumb the signature URL

`frontend/src/lib/queries/profile.ts`: add `signature: string;` to `ProfileRow`.

`frontend/src/components/applications/export-card.tsx`: derive the URL and pass it down — the export
already loads `profile`:

```tsx
  const signatureUrl = profile.data?.signature || undefined;
```

Add `signatureUrl={signatureUrl}` to both the `scope === "letter"` `<LetterDocument …>` and the
`ApplicationDocument`'s `letter={{ …, signatureUrl }}` prop bag.

Profile form (account settings): add a signature file input mirroring the existing avatar upload, so the
user can set it. (Same multipart submit — no new endpoint.)

---

## Tests

Red until the code lands. The existing `render-templates.test.ts` (density + header order) and the
rest of `export.test.ts` stay **green** — the change preserves the `cvStyles` keys/values and the header
text order.

- `frontend/tests/lib/export.test.ts` — **update** the two `entryParts` assertions to the new split
  shape (goes red until `parts.ts` lands, then green):
  - the job's `p.date` is `"2021-01-01–present"` and `p.meta` is `"Python"` (no date in `meta`);
  - the missing-row fallback is `{ heading, date: "", meta: "", body: "", favourite: false }`.
- `frontend/tests/lib/render-moderncv.test.ts` — **new**:
  - `cvStyles` carries the two-column look: `row.flexDirection === "row"`, `hints.width > 0`,
    `sectionTitle.borderBottomColor === spec.colors.accent`.
  - `skillGroups` groups by category with display labels, in category order, and falls back to the
    stored label when the DB row is missing.
  - a real `renderToBuffer(CvDocument(…))` (mirroring the header-order test) whose skills span two
    categories → the extracted text contains the category label `"Technical"` (the current joined-line
    render does not — that's the red).
  - Signature image embedding is **not** asserted here (brittle to read out of a PDF) — covered in
    Verification. Flagged per the setup-guide "couldn't make meaningfully red" rule.

Run: `cd frontend && npx vitest run tests/lib/render-moderncv.test.ts tests/lib/export.test.ts tests/lib/render-templates.test.ts`

---

## Verification

1. `cd frontend && npx vitest run` — green (new + updated red→green; density/header untouched).
2. In the account profile, upload a signature PNG (transparent background). `GET /api/spa/profile/`
   returns a `signature` URL.
3. Open an application, **Preview PDF** (react-pdf) → the CV shows: colored name + rule, dated left
   column per job/education/project, ruled section titles, skills grouped as `Technical: …` / `Soft: …`
   rows; the letter shows the signature image above the typed name.
4. Reorder / deselect a CV entry → the PDF reflects it (tailoring intact).
5. **Download Markdown** → unchanged from before (date + skills on one line under each heading).
6. Compare side by side with `cv_Hirschhausen_de.pdf` — close enough to be proud of. Note any gaps in
   `## Results` for a font/detail follow-up.

**Done looks like:** the react-pdf export reads as a moderncv-classic document (dated columns, ruled
sections, grouped skills, signature) rendering the tailored `cv_content` + letter, with the hidden ATS
layer intact and zero server/TeX cost — pretty enough to retire the LaTeX idea.

## Results

<!-- Human fills this in after testing: raw test output, observed issues, what works. -->
