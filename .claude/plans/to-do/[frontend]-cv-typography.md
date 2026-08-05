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
   ⚠️ They must be moved _deliberately_: `joinedContent` spreads the raw DB row, so the hidden JSON
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
   joined line — no code change, one line of layout JSON. (The _user-controlled_ version of this,
   switching sections off per application, is guide `[fullstack]-cv-section-toggles`.)

Plus widow control: a section title landing at the bottom of page 2 with its entries on page 3.

## Affected files

| path                                           | why                                                                                                                          |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/lib/render/parts.ts`             | `dateFrom`/`dateTo` split, skills off the meta line onto their own field, proficiency in `skillGroups`.                      |
| `frontend/src/lib/render/templates.tsx`        | hyphenation off, two-line date column, markdown body, page footer, widow control.                                            |
| `frontend/src/lib/export.ts`                   | `joinedContent` resolves skill names for the hidden layer; markdown keeps the skills it has today.                           |
| `frontend/src/lib/render/spec.ts`              | `FALLBACK_SPEC` mirrors the new default layout (certs → sidebar, bigger budgets) + `cv.detailed`.                            |
| `frontend/src/lib/queries/generations.ts`      | `CvEntry.detail` — the per-entry override.                                                                                   |
| `backend/jac/resources/default_layout.json`    | same, for the seeded system layout.                                                                                          |
| `backend/jac/resources/two_page_layout.json`   | same.                                                                                                                        |
| `frontend/tests/lib/render-typography.test.ts` | **new** — acceptance tests.                                                                                                  |
| `frontend/tests/lib/export.test.ts`            | existing: `p.meta === "Python"` moves to `p.skills`, and the `toEqual` fallback shape gains the three new `EntryParts` keys. |
| `frontend/tests/lib/render-moderncv.test.ts`   | existing `skillGroups` expectation gains proficiency.                                                                        |
| `frontend/tests/lib/render-templates.test.ts`  | existing `max_entries.skills === 14` follows the new budget.                                                                 |

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
no migration**. Note the existing behaviour it inherits: applying a _new_ run replaces the section
lists, so an override is lost unless the entry is pinned — exactly what already happens to
`deselected`.

**b.** `frontend/src/lib/render/spec.ts` — the budget, in three places. In the `LayoutSpec.cv`
type, next to `max_entries` (line 20):

```ts
/** How many entries per section render in full. The rest are one-liners. Sections
 *  absent from this map are compact throughout (skills, languages — they have no
 *  description to show anyway). */
detailed: Record<string, number>;
```

in `FALLBACK_SPEC.cv`, after `max_entries` (line 42):

```ts
    detailed: { jobs: 2, projects: 1, educations: 1 },
```

and in `parseLayoutSpec`. The existing `maxEntries` helper (lines 62–70) parses exactly the shape
`detailed` needs — positive ints keyed by section, legacy names remapped — but it hardcodes
`f.cv.max_entries` as its empty-input fallback, so it can't be called twice as it stands.
Generalise it (one renamed function, one extra argument, the two call sites below it):

```ts
/** Positive ints keyed by section, legacy names remapped. Shared by `max_entries`
 *  and `detailed` — same shape, different fallback. */
const sectionCounts = (
  raw: Record<string, number> | undefined,
  fallback: Record<string, number>,
) => {
  if (!raw) return { ...fallback };
  const out: Record<string, number> = {};
  for (const [name, cap] of Object.entries(raw)) {
    if (typeof cap === "number" && cap > 0)
      out[LEGACY_SECTIONS[name] ?? name] = Math.floor(cap);
  }
  return out;
};
```

```ts
      max_entries: sectionCounts(r.cv?.max_entries, f.cv.max_entries),
      detailed: sectionCounts(r.cv?.detailed, f.cv.detailed),
```

⚠️ Note what the `if (!raw)` branch means for a **stored** layout: a template file written before
this change has no `detailed` key, so it inherits the fallback's `{ jobs: 2, projects: 1,
educations: 1 }` rather than an empty map. That is the intended default — but it also means step 4's
`seed_system_defaults` is not optional for the _budgets_, only for the section split.

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

**d.** `frontend/src/lib/render/templates.tsx` — the detail level reaches the entry loop. Three
edits:

1. `CvSectionView`'s props gain **two** optionals: `detailed?: Record<string, number>` and
   `demoted?: Set<string>`. It receives `spec`-derived `styles` but **not `spec` itself, and this
   step does not change that** — the budget map is passed on its own so the function keeps the
   narrow signature it has today;
2. `CvPages` passes both down — `detailed={spec.cv.detailed}` and `demoted={demoted}` — on the
   **main-flow** `CvSectionView` map (line 242) only. The `sidebar` map (line 251) does not: a
   compact section has no per-entry detail to choose. `CvPages` itself takes a new optional
   `demoted?: Set<string>` prop; nothing passes it yet — `[frontend]-fit-preflight` is what fills
   it, and until then every caller renders with rank alone;
3. the entry loop drops the meta line and the body for a compact entry.

The props (replace the destructuring + type of `CvSectionView`):

```tsx
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
```

and the `entries.map` from step 2d, in full — this **replaces** the one written there, it is not a
second loop:

```tsx
{
  entries.map((e, i) => {
    const p = entryParts(db, section, e);
    const full = entryDetail(e, i, section, detailed ?? {}, demoted) === "full";
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
  });
}
```

and in `CvPages`, the **main-flow** map only:

```tsx
{
  spec.cv.sections.map((s) => (
    <CvSectionView
      key={s}
      section={s as SectionKey}
      content={content}
      db={db}
      styles={styles}
      detailed={spec.cv.detailed}
      demoted={demoted}
    />
  ));
}
```

**e.** `frontend/src/lib/export.ts` — markdown honours **only** the user's explicit override, not
the page's. In `cvToMarkdown`'s entry loop (line 31):

```ts
// The three detail signals are not equal. `entry.detail` is editorial — "this job
// is a footnote" — and holds in any format. The other two (rank against the
// layout budget, and a fit demotion) exist because the PAGE ran out of room, and
// markdown has no pages. So markdown reads the override off the entry and ignores
// the rest; it needs no `detailed` map and keeps its three-argument signature.
if (p.body && e.detail !== "compact") lines.push(p.body);
```

The invisible-ink payload and the JSON dump stay untouched in either case — both carry the joined
career-DB row, so a machine reader still gets every description whatever the page decided.

### 4. layout budgets — three files, **same section split, different numbers**

The section split (certifications out of the main flow, into `sidebar`) is identical in all three.
The budgets are **not** — the two-page layout keeps its own, bigger set. Each file is spelled out
below; do not paste one into another.

**a.** `backend/jac/resources/default_layout.json` — the one-page shape:

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

**b.** `backend/jac/resources/two_page_layout.json` — twice the page, twice the room to describe
things. ⚠️ `"pages"` stays **2**; the whole point of this file is that it is not the one-pager.
Its `font.base_pt` is 10 and stays 10.

```json
  "cv": {
    "pages": 2,
    "sections": ["jobs", "educations", "projects"],
    "sidebar": ["certifications", "skills", "languages"],
    "max_entries": {
      "jobs": 9,
      "educations": 4,
      "projects": 8,
      "certifications": 8,
      "skills": 24,
      "languages": 6
    },
    "detailed": { "jobs": 4, "projects": 2, "educations": 1 }
  },
```

**c.** `frontend/src/lib/render/spec.ts` `FALLBACK_SPEC` (lines 31–43) — must match **a** exactly.
It is what renders when the template file fails to load, and a mismatch means the preview and the
export disagree. The `sections`/`sidebar`/`max_entries` all move, not just `detailed`:

```ts
  cv: {
    pages: 1,
    sections: ["jobs", "educations", "projects"],
    sidebar: ["certifications", "skills", "languages"],
    max_entries: {
      jobs: 5,
      educations: 3,
      projects: 4,
      certifications: 4,
      skills: 18,
      languages: 6,
    },
    detailed: { jobs: 2, projects: 1, educations: 1 },
  },
```

## Tests

**Step 0 — done at activation.** This guide's tests landed `describe.skip`-marked while guide 1 was
the active one (the executor-rework convention: the active guide's red set stays unambiguous). The
`.skip`s are now removed and the three existing assertions below have been flipped to the new
expectations, so **the red set is this guide** — `25 failed | 31 passed (56)` across the four files,
before a line of implementation is typed.

Six of this file's 27 tests pass already, deliberately: they are the regression guards, not weak
tests. `joinedContent` "omits the key for an entry with no skills" and "suppresses the footer on a
single-page CV" pass trivially today because neither feature exists yet — they are there to catch
the implementation going too far (a `skill_names: ""` on every entry, a `page 1 of 1` on a one-pager).

`frontend/tests/lib/render-typography.test.ts` — 27 tests. Covers:

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

i had trouble coding 3b.d.

there is also problems with the spec. i think what happend: yuou wanted the one funciton to stay slim and iused detailed, but in the helper function you expect spec. anyways, amaybe i misunderstood, or the guide was ambigous or wrong, it needs to fixed

four tests fail. nothing commited yet

### AI triage @ 3bc92d0 (2026-08-05)

Lukas's read was right — the guide contradicted itself. **3b.d prose** said pass `detailed`
explicitly and keep `spec` out of `CvSectionView`; the **3b.d code block** then wrote
`spec.cv.detailed` inside that same function, where `spec` is not in scope. Both the props block and
the full loop body are now written out above (no `…` elisions — the two in the old block were typed
into `templates.tsx` verbatim, and a literal `…` is a parse error, which is why 3 of the 4 suites
failed to load rather than failing tests).

Verified in a throwaway worktree off `3bc92d0`: with 3b.d written as it now reads plus the two
items below, `npx vitest run` is **352 passed | 85 skipped, 0 failed** across the whole frontend
tree.

### AI implementation @ branch `ai/cv-typography-fixes` (volatile phase, Lukas asked)

Lukas opened a volatile phase for the repairs, so the AI typed the remaining source on its own
branch off `cv_typo`. **Lukas still tests and merges.** Five edits:

1. `templates.tsx` — 3b.d as rewritten above: `detailed?: Record<string, number>` on
   `CvSectionView`'s props, the full `entries.map` (two-line date column + bullet/plain body
   blocks, both gated on `full`), and `detailed={spec.cv.detailed}` on the main-flow map in
   `CvPages`. The sidebar map deliberately gets neither.
2. `spec.ts` `FALLBACK_SPEC` — step 4c. Only `detailed` had landed; `sections`/`sidebar`/
   `max_entries` now match `default_layout.json`. This was the one **real** test failure
   (`render-templates.test.ts:63` wants `max_entries.skills === 18`).
3. `backend/jac/resources/two_page_layout.json` — step 4b restored: `"pages"` back to **2** and the
   two-page budgets (`jobs: 9, educations: 4, projects: 8, certifications: 8, skills: 24`,
   `detailed: { jobs: 4, projects: 2, educations: 1 }`). It had been overwritten with the one-page
   shape; no test covers this file, so nothing went red.
4. `export.ts` step 3e — `cvToMarkdown`'s `e.detail !== "compact"` guard, with the rationale
   comment. No test covers it either.
5. `components/portfolio/content-picker.tsx` — the pre-existing `tsc` error, out of this guide's
   scope but fixed on request: `addPlaceholder`'s draft block was missing `links`, required on
   `BlockInput` since the block-links guide. A placeholder references nothing, so `links: []`.

**Fixed test-side (AI's own):** `tests/lib/_pdf-text.ts` decoded PDF bytes 1:1, which is exact for
ASCII but wrong for WinAnsi `0x80–0x9F` — where the standard-14 Helvetica keeps `•` (0x95), `–`
(0x96) and `—` (0x97). So `expect(text).toContain("•")` failed on a bullet that had in fact
rendered correctly. The extractor now maps that block back to Unicode, which also makes future
assertions on the en/em dashes writable as real characters.

**Green as of this pass:** `npx vitest run` → `Test Files 33 passed | 4 skipped (37)`,
`Tests 352 passed | 85 skipped (437)`, 0 failed. `npx tsc -b` → clean, exit 0.

**Not run by the AI — still Lukas's:** everything in `## Verification` from step 3 down. Notably
`python manage.py seed_system_defaults` (without it the DB serves the old spec and the browser
shows nothing new), the Preview-PDF eyeball pass, the `pdftotext` check that `skill_names` really
carries the moved skills, and the two-page footer/page-count check — which now has teeth again,
since `two_page_layout.json` is a two-pager once more.

### update

system seeds done
test are green

layout shitty see @.claude/pdf_preview3.png and @.claude/pdf_preview4.png due to the date formatting a lot of whitspace

[shitty layout](/Users/lukas/Projects/jac/.claude/pdf_preview3.png)

[shitty layout](/Users/lukas/Projects/jac/.claude/pdf_preview4.png)

### AI triage — the whitespace was two bugs, not the date format

Measured off the PDF content stream (throwaway probe that resolves each text run's absolute
`y` through the `q`/`Q` + `cm` stack; deleted again). **Two independent defects stacked**, and
neither was the two-line concept itself.

**Bug 1 — the range overflowed the column and broke at the wrong space.** `"– Apr 2025"` needs
`31.7 + 6.3 + 30.4 = 68.4pt`; `mm(23)` minus the 4.5pt padding offered `60.7pt`. `nb()` glued
`Apr 2025` but left the space *after the en dash* breakable — the only break opportunity in the
string — so react-pdf orphaned `"– "` onto a line of its own. Three line boxes per entry, and the
"empty line" in the screenshots is that orphan. Fixed in `dateParts`: `nb()` now wraps the whole
`to` side, dash included.

**Bug 2 — `hintLine: { lineHeight: 1.2 }` was a 21.6pt line box.** `@react-pdf/stylesheet`
resolves a unitless `lineHeight` against **the same style object's** `fontSize`, or its
`DEFAULT_FONT_SIZE = 18` when absent (`transformLineHeight`, `lib/index.js:598`) — the parent
View's `fontSize: small` never reaches the resolver. So `1.2 × 18 = 21.6pt` wrapped every 7.5pt
date in a double-height box, which is why a **compact one-liner cost the same 24.6pt as a
two-line entry**. Both `hintLine` and the pre-existing `summary` (`1.4 × 18 = 25.2pt` on a 9pt
paragraph — latent, `summary` just wasn't in the fixtures) now declare their `fontSize`.

**Design change that follows:** with the dash glued, each side is atomic, so the template renders
**one** `<Text>` joining them with the single plain space left between — the range sets on one
line, and a narrower custom layout degrades to the intended two-line split instead of an orphaned
dash. `hints` widened `mm(23) → mm(27)` (~11pt off a 499pt text column) to hold
`"Mar 2023 – present"` outright.

**Effect:** the sample CV went from 2 pages to 1 with room to spare; compact entries dropped
`24.6pt → 12.9pt`.

**Tests added** (`render-typography.test.ts`): each `dateParts` side contains no breakable space;
`hints.width` is wide enough for the whole range; and a loop over every `cvStyles` key asserting
no resolved `lineHeight` exceeds `1.6 × base` — the general form of bug 2, at two different
`base_pt`. Suite `355 passed | 85 skipped`, `tsc -b` clean.

**Open, needs Lukas's call — the favourite star renders as nothing.** `{p.favourite ? "★ " : ""}`
puts U+2605 in front of the heading, but Helvetica standard-14/WinAnsi has no such glyph: it is
dropped and only its trailing space survives, so a favourite entry is silently indented 2.5pt
(measured: `"JAC"` at `x=127.0`, its siblings at `x=124.5`) with no star. Three ways out — drop the
marker from the PDF (favourites are a ranking/fit signal; arguably a recruiter should not see
them), swap in a WinAnsi glyph, or register a font that has ★. Recommend dropping it; left
untouched because it is a product decision, not a layout bug.

### AI triage — the heading/body jitter (`pdf_preview5.png`) was the star, resolved

Lukas: *"the text jitters between title and markdown field"*. Confirmed positionally — a probe
resolving each run's absolute `x` through the `q`/`Q` + `cm` stack (deleted again):

```
x=48.00   "Mar 2021 – present"
x=124.54  ""             ← the ★: font-subset glyph 5, zero advance, no ink
x=127.04  "Fav Job — ACME"     ← favourite heading
x=124.54  "Prose line here."   ← its own description
x=124.54  "Plain Job — Initech"← non-favourite heading
```

The gap is **2.502pt = one Helvetica-Bold space** (278/1000 × 9pt): the star paints nothing but
its trailing space still advances the pen. So the entry's own title and body disagreed on the left
edge, and favourite headings disagreed with non-favourite ones. **Marker dropped from the PDF**
(`templates.tsx`, heading is now bare `{p.heading}`). Markdown keeps its `★` — it is UTF-8 and has
no left edge to break; `export.test.ts:127` stands.

**Test + new primitive.** `_pdf-text.ts` gains `pdfPositionedRuns()` / `runAt()` — the text
extractor with absolute positions, so *alignment* is assertable, not just presence. New test
"starts heading and description on the same left edge, favourite or not" pins heading ≡ body ≡
sibling heading. It is positional rather than `not.toContain("★")` on purpose: the star was never
in the text stream to begin with. Verified red before the fix (`expected 124.535553 to be close to
127.037553, difference 2.502`). Suite `356 passed | 85 skipped`, `tsc -b` clean.

**Deliberately NOT fixed — belongs to a later guide.** The blank lower third of
`pdf_preview5.png` is `[frontend]-fit-preflight`'s grow pass (its own header quotes the same
request: *"if we gain some space because the skill cloud is removed … i would love to use the
space"*). "Drop Out Education Physics / Mathematics" as a headline is
`[fullstack]-education-degree` problem 2.

**Two smaller findings** (neither claimed by a guide) — one parked, one fixed:

1. *Sidebar sections without a label hug the page margin.* **Parked by Lukas** — revisit once
   `[frontend]-fit-preflight` shows what the freed space is worth. `CvSectionView`'s compact branch
   emits the `hints` Text only when `r.label` is non-empty (`templates.tsx:261`), so Skills — which
   has `Technical` / `Soft` labels — indents its text to the `x=124.54` content edge while
   Languages, which has no label, starts at `x=48`, a gutter-width left of every other body line.
   The fix if it is ever wanted is `<View style={styles.hints} />` as a spacer in the falsy branch:
   no label, no extra rows, purely an indent costing that line 76.5pt of measure. (Not to be
   confused with giving Languages `skillGroups`-style fluency *grouping*, which would turn one row
   into three — a different change, and the one that is not worth it for six words.)

2. *The date rode 1.35pt above the heading baseline* — **fixed**, `hintLine` gains
   `paddingTop: base * 0.116`. Top-aligned columns put a smaller font's baseline higher inside its
   line box; both terms of the correction are proportional to `base` (`small` is a fixed
   `0.833 × base`), so it scales instead of hard-coding 1.05pt.

   **The target is the dashes, not the baselines** (Lukas: *"the dash between the date and the dash
   in the job title should align"* — flush baselines read a touch low). A dash's ink sits a fixed
   fraction of the font size above its own baseline, and the two fractions differ, so the right
   baseline offset is not zero. Measured off a 1200dpi Ghostscript raster of a calibration page
   rendered through this pipeline:

   | glyph | ink centre above own baseline | as em |
   | --- | --- | --- |
   | em dash, 9pt Helvetica-**Bold** (heading) | 2.340pt | 0.260 |
   | en dash, 7.5pt Helvetica (date) | 2.037pt | 0.272 |

   The heavier dash rides 0.303pt higher off its baseline, so the date's baseline must sit that
   much **above** the heading's. Hence `base * 0.150` (top-aligned → baselines flush) minus
   `base * 0.034` (baselines flush → dashes flush) = `base * 0.116`. Verified end to end at the
   ink level on a real CV render: the two dash centres land **0.030pt** apart, below the 0.06pt
   resolution of the raster.

   Four variants measured (date `y` vs heading `y`, same fixture):

   | variant | date | heading | baseline gap | dashes |
   | --- | --- | --- | --- | --- |
   | original, top-aligned | 725.81 | 724.46 | 1.35 high | 1.05 high ✗ |
   | `row: alignItems: "baseline"` | 724.91 | 724.46 | 0.45 high | 0.15 high |
   | `hintLine: paddingTop: base * 0.15` | 724.46 | 724.46 | flush | 0.30 low ✗ |
   | **`hintLine: paddingTop: base * 0.116`** | 724.77 | 724.46 | 0.31 high | **0.03 — level** |
   | `row: alignItems: "center"` | 708.49 | 724.46 | 16 low ✗ | 16 low ✗ |

   Both `alignItems` routes are wrong. `center` centres the gutter against the **whole entry**
   (heading + prose + bullets ≈ 46pt), parking the date next to the second bullet — invisible on a
   single-line fixture, which is why the new test uses a multi-line entry. `baseline` is supported
   (`@react-pdf/layout/lib/index.js:2132` → `Yoga.Align.Baseline`) but leaves 0.45pt: Yoga has no
   font baseline for these nodes and falls back to box height, so it aligns box *bottoms*
   (9.9 vs 9.0pt) rather than baselines. Centring the line boxes alone would recover only that same
   0.45 of the 1.35 — the boxes differ far less in height than the baselines do in position.

   Cost: `paddingTop` grows the gutter box 9.0 → 10.04pt, so a **compact** row (content = heading
   only, 9.9pt) gains 0.14pt. Full entries are unaffected — content dominates the row height.

   Test: "lines the date's dash up with the dash in the heading" — re-derives the expected
   baseline offset from the two measured em fractions instead of hard-coding a pt value, so it
   stays honest if the sizes move, and runs on a multi-line entry so the `alignItems: "center"`
   trap cannot slip past. Suite `357 passed | 85 skipped`, `tsc -b` clean.
