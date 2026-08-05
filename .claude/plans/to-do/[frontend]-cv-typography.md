# [frontend] CV typography

> Roadmap: **UI polish phase, item 2** — "the pdf itself is not the most beautiful cv i have ever
> seen": skill cloud out of the entries, weird date line breaks, use the space that frees up.
> Branch: `frontend/cv-typography`

## Context / goal

Five concrete changes to what the CV page looks like, plus the layout budgets that follow from them.

1. **Skill cloud out of the entries.** `entryParts` puts `skillNames(db, j.skills)` on every job's
   meta line (`parts.ts:87`) and skills + url on every project's (`:108`). At 7.5pt that's the grey
   wall in `pdf_preview2.png`. The visible entry becomes **headline + dates + description**; the
   skills move to the machine layer.
   ⚠️ They must be moved *deliberately*: `joinedContent` spreads the raw DB row, so the hidden JSON
   carries `skills: [3, 7, 12]` — **ids**, which no ATS can read. Dropping them from the page
   without resolving them into the hidden layer would delete them from the document altogether.
2. **The date column stops wrapping.** `hints` is `mm(22)` wide holding `"Aug 2020 – Jan 2023"` as
   one string, so it breaks wherever it likes — plus react-pdf hyphenates by default. Fix: the
   range renders as two deliberate lines (`Aug 2020` / `– Jan 2023`) with non-breaking spaces
   inside each, and hyphenation is switched off globally (it also mangles German compounds in the
   descriptions).
3. **Descriptions render as markdown.** Career-DB descriptions are written with `- ` bullets; today
   they print as literal hyphens. Real bullets, and `**bold**` markers stripped rather than shown.
4. **Spend the freed space.** Skills get their proficiency back (technical/domain only — nobody
   believes "Teamwork (expert)"), and the per-section budgets go up.
4b. **Two levels of entry detail.** Not every job deserves a paragraph. The way Lukas writes a CV by
   hand is two jobs described in full and the rest as a list of positions — so the renderer gets a
   `full` / `compact` detail level per entry: `full` is today's heading + dates + description,
   `compact` is the same dated row with the description and meta line dropped. The layout says how
   many detailed entries a section gets (`cv.detailed`), rank picks which, and the user can override
   any single entry. **This is also the fit's missing middle gear** — demoting a deep job beats
   dropping it, because a CV that lists every position but only describes the relevant ones is a
   normal good CV, while a CV missing positions has gaps. The fit half lives in
   `[frontend]-fit-preflight`; this guide builds the two render forms it will choose between.
5. **Certifications stop shouting.** In the screenshot three Udemy courses get the same visual
   weight as three jobs. Moving them to the layout's `sidebar` list renders them as one compact
   joined line — no code change, one line of layout JSON. (The *user-controlled* version of this,
   switching sections off per application, is guide `[fullstack]-cv-section-toggles`.)

Plus widow control: a section title landing at the bottom of page 2 with its entries on page 3.

## Affected files

| path | why |
| --- | --- |
| `frontend/src/lib/render/parts.ts` | `dateFrom`/`dateTo` split, skills off the meta line onto their own field, proficiency in `skillGroups`. |
| `frontend/src/lib/render/templates.tsx` | hyphenation off, two-line date column, markdown body, page footer, widow control. |
| `frontend/src/lib/export.ts` | `joinedContent` resolves skill names for the hidden layer; markdown keeps the skills it has today. |
| `frontend/src/lib/render/spec.ts` | `FALLBACK_SPEC` mirrors the new default layout (certs → sidebar, bigger budgets) + `cv.detailed`. |
| `frontend/src/lib/queries/generations.ts` | `CvEntry.detail` — the per-entry override. |
| `backend/jac/resources/default_layout.json` | same, for the seeded system layout. |
| `backend/jac/resources/two_page_layout.json` | same. |
| `frontend/tests/lib/render-typography.test.ts` | **new** — acceptance tests. |
| `frontend/tests/lib/export.test.ts` | existing assertion `p.meta === "Python"` moves to `p.skills`. |
| `frontend/tests/lib/render-moderncv.test.ts` | existing `skillGroups` expectation gains proficiency. |

## The code

### 1. `frontend/src/lib/render/parts.ts`

**a.** the shape, and a non-breaking-space helper. Replace the `EntryParts` type (lines 25–31):

```ts
export type EntryParts = {
  heading: string;
  /** Joined range — markdown export only. The PDF uses dateFrom/dateTo. */
  date: string;
  /** Left column, line 1 (NBSP-joined, never wraps). */
  dateFrom: string;
  /** Left column, line 2 — "– present" / "– Jan 2023"; "" for a point in time. */
  dateTo: string;
  /** Secondary line: grade, url. NEVER skills — they belong to the machine layer. */
  meta: string;
  /** Resolved skill names. Hidden layer + markdown only; the page never shows them. */
  skills: string;
  body: string;
  favourite: boolean;
};

/** "Aug 2020" must survive the mm(22) column as one unit — the space is the only break
 *  opportunity in it, so make it unbreakable and let the range break where WE say. */
const nb = (s: string) => s.replace(/ /g, "\u00A0");

/** The date column as two deliberate lines instead of one wrapping string. */
export function dateParts(
  started: string | null,
  ended: string | null,
): { from: string; to: string } {
  return {
    from: nb(formatMonthYear(started) || "?"),
    to: `– ${nb(ended ? formatMonthYear(ended) : "present")}`,
  };
}

/** A single point in time (certifications) — one line, no continuation. */
export function datePoint(iso: string | null): { from: string; to: string } {
  return { from: nb(formatMonthYear(iso)), to: "" };
}
```

**b.** proficiency in the sidebar skills. Replace `skillGroups` (lines 43–57):

```ts
/**
 * Category rows for the compact skills section. Technical and domain skills carry their
 * proficiency — that is the information a reader actually weighs, and there is room for it
 * now that the per-entry skill cloud is gone. Soft skills deliberately do not: a
 * self-declared "Teamwork (expert)" reads as a joke.
 */
const PROFICIENCY_IN = new Set(["technical", "domain"]);

export function skillGroups(
  db: CvEntriesResponse | undefined,
  entries: CvEntry[],
): { label: string; names: string }[] {
  const groups: Record<string, string[]> = {};
  for (const e of entries) {
    const row = db ? (joinEntry(db, "skills", e) as SkillRow | null) : null;
    const cat = row?.category ?? "other";
    const name = row?.name ?? e.label;
    (groups[cat] ??= []).push(
      PROFICIENCY_IN.has(cat) && row?.proficiency
        ? `${name} (${row.proficiency})`
        : name,
    );
  }
  return SKILL_CATEGORY_ORDER.filter((c) => groups[c]?.length).map((c) => ({
    label: SKILL_CATEGORY_LABELS[c] ?? c,
    names: groups[c].join(", "),
  }));
}
```

**c.** `entryParts` — every branch gains `dateFrom`/`dateTo`/`skills`, and jobs/projects lose the
skill cloud from `meta`. Replace lines 66–144:

```ts
export function entryParts(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): EntryParts {
  const row = db ? joinEntry(db, section, entry) : null;
  const blank = { date: "", dateFrom: "", dateTo: "", meta: "", skills: "" };
  if (!row)
    return { heading: entry.label, ...blank, body: "", favourite: false };
  const favourite = "favourite" in row ? Boolean(row.favourite) : false;
  switch (section) {
    case "jobs": {
      const j = row as JobRow;
      const d = dateParts(j.started, j.ended);
      return {
        heading: `${j.title} — ${j.company}`,
        date: dateRange(j.started, j.ended),
        dateFrom: d.from,
        dateTo: d.to,
        meta: "", // was the skill cloud — now machine-layer only
        skills: skillNames(db, j.skills),
        body: j.description,
        favourite,
      };
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const d = dateParts(e.started, e.ended);
      return {
        heading: head ? `${head} @ ${e.institution}` : e.institution,
        date: dateRange(e.started, e.ended),
        dateFrom: d.from,
        dateTo: d.to,
        meta: e.grade ? `Grade: ${e.grade}` : "",
        skills: "",
        body: e.description,
        favourite,
      };
    }
    case "projects": {
      const p = row as ProjectRow;
      const d = dateParts(p.started, p.ended);
      return {
        heading: p.name,
        date: dateRange(p.started, p.ended),
        dateFrom: d.from,
        dateTo: d.to,
        meta: p.url, // the link stays visible; the skill cloud does not
        skills: skillNames(db, p.skills),
        body: p.description,
        favourite,
      };
    }
    case "skills": {
      const s = row as SkillRow;
      return {
        heading: s.name,
        ...blank,
        meta: `${s.proficiency} · ${s.category}`,
        body: "",
        favourite,
      };
    }
    case "certifications": {
      const c = row as CertificationRow;
      const d = datePoint(c.issued_on);
      return {
        heading: `${c.name} — ${c.issuer}`,
        date: formatMonthYear(c.issued_on),
        dateFrom: d.from,
        dateTo: d.to,
        meta: "",
        skills: skillNames(db, c.skills),
        body: c.description,
        favourite,
      };
    }
    case "languages": {
      const l = row as LanguageRow;
      return {
        heading: l.name,
        ...blank,
        meta: l.fluency,
        body: "",
        favourite,
      };
    }
  }
}
```

### 2. `frontend/src/lib/render/templates.tsx`

**a.** hyphenation off, once, at module load. Add `Font` to the `@react-pdf/renderer` import list
(line 12–21) and put this right after the imports:

```tsx
/**
 * react-pdf hyphenates by default. That is wrong twice over here: it breaks German
 * compounds mid-word in the descriptions, and it was half the "weird line breaks" in the
 * date column. One global opt-out — the layout math below assumes words stay whole.
 */
Font.registerHyphenationCallback((word) => [word]);
```

**b.** markdown-ish body blocks — a pure function, exported for the tests:

```tsx
export type BodyBlock = { bullet: boolean; text: string };

/**
 * Career-DB descriptions are typed as markdown-lite. Only what actually appears in them is
 * honoured: leading "- " / "* " / "• " bullets, and **bold**/__bold__ markers, which are
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
```

**c.** styles — add to `cvStyles`'s `StyleSheet.create({...})` (after `hints`, line 118–123):

```tsx
    // Two stacked lines instead of one wrapping string; mm(23) fits "– present" at 7.5pt.
    hints: {
      width: mm(23),
      color: spec.colors.muted,
      fontSize: small,
      paddingRight: base * 0.5,
    },
    hintLine: { lineHeight: 1.2 },
```

and after `body` (line 128):

```tsx
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
```

**d.** the entry render — replace the non-compact branch of `CvSectionView` (lines 186–207):

```tsx
  return (
    <View>
      {/* minPresenceAhead keeps a section title from stranding at a page bottom. If
          react-pdf ignores it (log it in Results), wrap title + first entry in a
          <View wrap={false}> instead. */}
      <Text style={styles.sectionTitle} minPresenceAhead={mm(14)}>
        {SECTION_TITLES[section]}
      </Text>
      {entries.map((e) => {
        const p = entryParts(db, section, e);
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
              {p.meta ? <Text style={styles.meta}>{p.meta}</Text> : null}
              {bodyBlocks(p.body).map((b, i) =>
                b.bullet ? (
                  <View key={i} style={styles.bulletRow}>
                    <Text style={styles.bulletDot}>•</Text>
                    <Text style={styles.bulletText}>{b.text}</Text>
                  </View>
                ) : (
                  <Text key={i} style={styles.body}>
                    {b.text}
                  </Text>
                ),
              )}
            </View>
          </View>
        );
      })}
    </View>
  );
```

The compact branch keeps `styles.hints` as a `Text` — it holds a category label, not a date, so
leave lines 176–181 alone.

**e.** the page footer component, next to `HiddenInk`:

```tsx
/**
 * "Name — page 2 of 3", suppressed on a single-page CV. Same physics as HiddenInk: a
 * `fixed`, absolutely positioned Text, so page counts and the fit loop stay invariant.
 */
function PageFooter({ name, styles }: { name: string; styles: ReturnType<typeof cvStyles> }) {
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
```

and render it in `CvPages`, right before `<HiddenInk … />` (line 261):

```tsx
      <PageFooter name={name} styles={styles} />
```

### 3. `frontend/src/lib/export.ts`

**a.** `joinedContent` (lines 99–112) — resolve the skills that left the page:

```ts
/** content joined with the career-DB rows behind it — the shared export/hidden-layer shape.
 *  `skill_names` is the resolved form of the row's `skills` id array: since the CV page
 *  stopped printing the per-entry skill cloud, this is where a parser reads it. */
export function joinedContent(
  content: CvContent,
  db: CvEntriesResponse | undefined,
) {
  return Object.fromEntries(
    SECTION_ORDER.map((section) => [
      section,
      (content[section] ?? []).map((e) => {
        const skills = entryParts(db, section, e).skills;
        return {
          ...e,
          entry: joinEntry(db, section, e),
          ...(skills ? { skill_names: skills } : {}),
        };
      }),
    ]),
  );
}
```

**b.** `cvToMarkdown` (line 29) — markdown is a text CV with no hidden layer, so it keeps the
skills it prints today:

```ts
      const metaLine = [p.date, p.meta, p.skills].filter(Boolean).join(" · ");
```

### 3b. entry detail level

**a.** `frontend/src/lib/queries/generations.ts` — `CvEntry` gains the override:

```ts
  /** Per-entry render detail. Absent = derived from rank + the layout's `cv.detailed`
   *  budget; present = the user overrode it in the editor and rank no longer decides. */
  detail?: "full" | "compact";
```

`cv_content` is a raw JSONField with no serializer validation, so this needs **no backend change and
no migration**. Note the existing behaviour it inherits: applying a *new* run replaces the section
lists, so an override is lost unless the entry is pinned — exactly what already happens to
`deselected`.

**b.** `frontend/src/lib/render/spec.ts` — the budget, next to `max_entries` in the `LayoutSpec`
type, `FALLBACK_SPEC`, and `parseLayoutSpec` (reuse the `maxEntries` parser — it is the same
shape: positive ints keyed by section, legacy names remapped):

```ts
    /** How many entries per section render in full. The rest are one-liners. Sections
     *  absent from this map are compact throughout (skills, languages — they have no
     *  description to show anyway). */
    detailed: Record<string, number>;
```
```ts
    detailed: { jobs: 2, projects: 1, educations: 1 },
```

**c.** `frontend/src/lib/render/parts.ts` — the resolution rule, in one pure place:

```ts
/** A section always keeps at least one detailed entry — a CV whose every entry is a
 *  one-liner is a list, not a CV. The fit's demote ladder honours the same floor. */
export const MIN_DETAILED = 1;

/**
 * Whether entry #`index` of `section` renders in full. Precedence, highest first:
 *   1. the user's explicit per-entry override (`entry.detail`);
 *   2. a demotion the page fit had to make (`demoted`);
 *   3. rank against the layout's `detailed` budget.
 * A section with no budget entry is compact throughout.
 */
export function entryDetail(
  entry: CvEntry,
  index: number,
  section: string,
  detailed: Record<string, number>,
  demoted: Set<string> = new Set(),
): "full" | "compact" {
  if (entry.detail) return entry.detail;
  if (demoted.has(entry.id)) return "compact";
  return index < (detailed[section] ?? 0) ? "full" : "compact";
}
```

**d.** `frontend/src/lib/render/templates.tsx` — `CvPages` passes `spec.cv.detailed` and an optional
`demoted` set into `CvSectionView`, and the entry loop drops the meta line and the body for a
compact entry. Inside the `entries.map` from step 2d:

```tsx
      {entries.map((e, i) => {
        const p = entryParts(db, section, e);
        const full =
          entryDetail(e, i, section, spec.cv.detailed, demoted) === "full";
        return (
          <View key={e.id} style={styles.row} wrap={false}>
            <View style={styles.hints}>
              …
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
                ? bodyBlocks(p.body).map((b, j) => …)
                : null}
            </View>
          </View>
        );
      })}
```

**e.** `frontend/src/lib/export.ts` — markdown mirrors the page (it is a sendable artefact, so it
shows what the PDF shows). `cvToMarkdown` takes the same two arguments and skips `p.body` for a
compact entry; the invisible-ink payload and the JSON dump are untouched — both already carry the
full career-DB row, so the description is still there for a machine reader.

### 4. layout budgets — three files, same edit

`frontend/src/lib/render/spec.ts` `FALLBACK_SPEC` (lines 31–43), `backend/jac/resources/default_layout.json`
and `backend/jac/resources/two_page_layout.json`. The one-page shape:

```json
  "cv": {
    "pages": 1,
    "sections": ["jobs", "educations", "projects"],
    "sidebar": ["certifications", "skills", "languages"],
    "max_entries": {
      "jobs": 5,
      "educations": 3,
      "projects": 4,
      "certifications": 4,
      "skills": 18,
      "languages": 6
    },
    "detailed": { "jobs": 2, "projects": 1, "educations": 1 }
  },
```

two-page: `"pages": 2`, same section split, `jobs: 9, educations: 4, projects: 8,
certifications: 8, skills: 24, languages: 6`, and `"detailed": { "jobs": 4, "projects": 2,
"educations": 1 }` — twice the page, twice the room to describe things.

`FALLBACK_SPEC` must match the one-page JSON exactly — it is what renders when the template file
fails to load, and a mismatch means the preview and the export disagree.

## Tests

**Step 0 — unskip.** This guide is not the active one, so its acceptance tests land on disk
`describe.skip`-marked (the same convention the executor-rework stack used, so the active guide's
red set stays unambiguous). Before writing any code: delete every `.skip` in
`frontend/tests/lib/render-typography.test.ts` and confirm the suite goes red.

`frontend/tests/lib/render-typography.test.ts` — 21 tests. Covers:

- `dateParts` / `datePoint`: NBSP inside each side, the dash leads line 2, open-ended ranges say
  `present`, a missing start renders `?`, a certification gets one line and no continuation.
- `bodyBlocks`: `- ` / `* ` / `• ` bullets flagged, `**bold**` and `__bold__` stripped, blank lines
  dropped, plain prose passes through, empty/undefined → `[]`.
- `entryParts`: a job's `meta` is empty and its `skills` carries the resolved names; a project keeps
  its url in `meta` and its skills in `skills`; `date` still holds the joined range for markdown.
- `skillGroups`: technical/domain names carry `(proficiency)`, soft ones don't, unknown rows still
  fall back to the stored label.
- `joinedContent`: emits `skill_names` for a job that has skills, omits the key when it has none.
- `cvToMarkdown`: a job line still lists its skills (the markdown export must not lose them).
- `entryDetail`: rank decides by default (first `detailed[section]` full, rest compact); a section
  with no budget is compact throughout; a fit demotion overrides rank; the user's `entry.detail`
  overrides both, in either direction (a deep entry forced to `full`, a top entry forced to
  `compact`); `MIN_DETAILED` is at least 1.
- real render (`renderToBuffer`): a job's skill name is **absent** from the visible text runs while
  the heading and description are present; the bullet glyph appears; a single-page CV shows no
  "page 1 of 1"; the footer does not change the page count; with `detailed: { jobs: 1 }` and two
  jobs, **both headings** appear but only the first job's description does.

Two existing tests move with the code:

- `frontend/tests/lib/export.test.ts:83` — `expect(p.meta).toBe("Python")` becomes
  `expect(p.meta).toBe("")` + `expect(p.skills).toBe("Python")`.
- `frontend/tests/lib/render-moderncv.test.ts:68` — the expected `skillGroups` output becomes
  `[{ label: "Technical", names: "Python (expert)" }, { label: "Soft", names: "Teamwork" }]`.

```bash
cd frontend && npx vitest run tests/lib/render-typography.test.ts tests/lib/export.test.ts tests/lib/render-moderncv.test.ts
```

## Verification

1. Run the three suites above — red, then green after implementing.
2. `cd frontend && npx tsc -b` — the `EntryParts` shape change is the likely breakage; every
   `entryParts` consumer must still compile (`export.ts`, `hidden.ts`, `templates.tsx`).
3. `cd backend && python manage.py seed_system_defaults` → expect
   `Layout 'default': template refreshed` and the same for `'two-page'`. Without this the DB still
   serves the old spec and nothing changes in the browser.
4. `npm run dev` → an application → **Preview PDF**, scope CV:
   - no grey skill line under any job or project;
   - dates read as two clean lines, no mid-word or mid-date breaks anywhere on the page;
   - descriptions show real bullets, no stray `-` or `**`;
   - certifications are one compact line near the bottom, not three full entries;
   - the skills line reads `Technical  Python (expert), Django (advanced), …`;
   - **the top two jobs carry their description, the rest are one-liners** — every position
     still visible, no employment gaps, but only the relevant ones described.
5. `pdftotext` the downloaded PDF (or open the JSON export): confirm the job skills are still in
   the document — `skill_names` in the invisible-ink JSON. **This is the check that matters**: the
   skills must have moved, not vanished.
6. Switch the application to the two-page layout and confirm `Name — page 1 of 2` appears centred
   at the foot of both pages, and that the page count is still 2 (the footer must not have pushed
   anything).
7. Eyeball a section that lands near a page break — the title should not sit alone at the bottom.

## Results

<!-- human: raw test output, observed issues, what works -->
