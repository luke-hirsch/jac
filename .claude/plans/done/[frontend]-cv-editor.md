# [frontend] cv-editor — deselect / move / delete + manual CV building

> Guide 2 of 4 for the "frontend polish" phase. Branch: `frontend/cv-editor`.
> **Depends on guide 1** (`[backend]-application-content-v2`) being merged to `main` — it fixes
> the layout specs and defines the serializer contract; merge `main` into this branch before
> starting. Guides 3/4 build on the helpers introduced here.

## Context / goal

The application detail page shows `cv_content` as a read-only list. This guide turns it into the
manual-customisation stage of the flow: the user can **reorder** (move up/down), **deselect**
(keep, but excluded from render/export — recoverable), and **delete** entries, **add** any
career-DB entry the selection is missing (per-section picker — also the way back after a
delete, and how an AI run's drops get overridden), pick one of the two standard **layouts**,
and — for the no-AI path — **build the CV manually** from the full career DB
(`/api/jac/cv/entries/`, which already exists backend-side).

Design decision to be aware of: `cv_content` stays a _selection_ (`{section: [{id, label,
relevance_score, deselected?}]}`), not a data snapshot. The editor joins the ids against the live
career DB for display (and guide 4 does the same for rendering); the stored `label` is only a
fallback for entries later deleted from the career DB. The downloaded PDF is the frozen artefact
— the application row is not. Entries are stored in ranked order; order is the rank (that is what
"drop from the end until it fits" in guide 4 leans on).

## Affected files

| file                                                                 | why                                                                                        |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `frontend/src/lib/cv-doc.ts`                                         | **new** — pure cv_content editing logic (join, labels, move/remove/deselect, manual build) |
| `frontend/src/lib/queries/jac.ts`                                    | `CvEntriesResponse` + `useCvEntries` hook; `layouts` resource + `LayoutRow`                |
| `frontend/src/lib/queries/generations.ts`                            | `CvEntry` gains `deselected?: boolean`                                                     |
| `frontend/src/routes/_authenticated/applications/$applicationId.tsx` | `ApplicationContentCard` becomes the CV editor + layout picker                             |

## The code

### 1. `frontend/src/lib/queries/generations.ts`

```ts
export type CvEntry = {
  id: string;
  label: string;
  relevance_score: number | null;
  // Kept in the application's cv_content but excluded from render/export — the user's
  // recoverable "not this one" toggle (delete is the destructive sibling).
  deselected?: boolean;
};
```

### 2. `frontend/src/lib/queries/jac.ts`

Add the layouts resource to the `R` map (last line of the map):

```ts
  snippets: { key: "snippets", url: "/api/jac/resume-snippets/" },
  layouts: { key: "layouts", url: "/api/jac/layouts/" },
```

New row type next to the other `*Row` types:

```ts
export type LayoutRow = {
  id: number;
  name: string;
  template: string | null; // media URL of the JSON layout spec (guide 4 fetches it)
  is_default: boolean; // true = shared system layout (read-only)
};
```

And below the generic hooks, the full-career-DB dump (`CVEntryListView` — a plain `APIView`, not
paginated, so none of the `R`-map machinery applies):

```ts
export type CvEntriesResponse = {
  skills: SkillRow[];
  jobs: JobRow[];
  educations: EducationRow[];
  certifications: CertificationRow[];
  projects: ProjectRow[];
  languages: LanguageRow[];
};

export function useCvEntries(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["jac", "cv-entries"],
    queryFn: () => api<CvEntriesResponse>("/api/jac/cv/entries/"),
    enabled: options.enabled ?? true,
  });
}
```

### 3. `frontend/src/lib/cv-doc.ts` — new file, pure logic (unit-tested)

```ts
/**
 * Pure editing logic for the application's tailored-CV JSON (`cv_content`).
 *
 * Shape: `{section: [{id, label, relevance_score, deselected?}]}` — plural section keys
 * ("jobs"), ids "<singular>:<pk>" ("job:12"), entries in ranked order (the order IS the rank;
 * backend jac/generation_result.py). Display joins the ids against the live career DB
 * (`/api/jac/cv/entries/`); the stored label is the fallback for rows since deleted there.
 * All operations are immutable — they return a new CvContent.
 */
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

export type CvContent = Record<string, CvEntry[]>;

/** Render order — mirrors backend CvRender.SECTION_ORDER (jac/render.py). */
export const SECTION_ORDER = [
  "jobs",
  "educations",
  "projects",
  "skills",
  "certifications",
  "languages",
] as const;
export type SectionKey = (typeof SECTION_ORDER)[number];

export const SECTION_TITLES: Record<SectionKey, string> = {
  jobs: "Experience",
  educations: "Education",
  projects: "Projects",
  skills: "Skills",
  certifications: "Certifications",
  languages: "Languages",
};

const SINGULAR: Record<SectionKey, string> = {
  jobs: "job",
  educations: "education",
  projects: "project",
  skills: "skill",
  certifications: "certification",
  languages: "language",
};

/** "job:12"-style id for a career-DB row. */
export function entryId(section: SectionKey, pk: number): string {
  return `${SINGULAR[section]}:${pk}`;
}

export type AnyRow =
  | SkillRow
  | JobRow
  | EducationRow
  | CertificationRow
  | ProjectRow
  | LanguageRow;

export function parseEntryId(id: string): { type: string; pk: number } | null {
  const m = /^([a-z_]+):(\d+)$/.exec(id);
  return m ? { type: m[1], pk: Number(m[2]) } : null;
}

/** The career-DB row behind a cv_content entry, or null (db not loaded / row deleted). */
export function joinEntry(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): AnyRow | null {
  if (!db) return null;
  const parsed = parseEntryId(entry.id);
  if (!parsed) return null;
  return (db[section] as AnyRow[]).find((r) => r.id === parsed.pk) ?? null;
}

export function dateRange(
  started: string | null,
  ended: string | null,
): string {
  return `${started ?? "?"}–${ended ?? "present"}`;
}

/**
 * One-line label per entry — mirrors the backend labelers (jac/generation_result.py) so a
 * manually built CV reads like a generated one. Divergence: the skill label omits the
 * "| domains: …" suffix (the API row carries domain pks, not names — not worth a lookup here).
 */
export function labelFor(section: SectionKey, row: AnyRow): string {
  switch (section) {
    case "skills": {
      const s = row as SkillRow;
      return `${s.name} (${s.proficiency}, ${s.category})`;
    }
    case "jobs": {
      const j = row as JobRow;
      return `${j.title} at ${j.company} (${dateRange(j.started, j.ended)})`;
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const w = dateRange(e.started, e.ended);
      return head
        ? `${head} @ ${e.institution} (${w})`
        : `${e.institution} (${w})`;
    }
    case "certifications": {
      const c = row as CertificationRow;
      return `${c.name} — ${c.issuer}${c.issued_on ? ` (${c.issued_on})` : ""}`;
    }
    case "projects": {
      const p = row as ProjectRow;
      return `${p.name} (${dateRange(p.started, p.ended)})`;
    }
    case "languages": {
      const l = row as LanguageRow;
      return `${l.name} (${l.fluency})`;
    }
  }
}

/** Manual mode: a cv_content with every career-DB entry, API order, no scores. */
export function fromCareerDb(db: CvEntriesResponse): CvContent {
  const content: CvContent = {};
  for (const section of SECTION_ORDER) {
    content[section] = (db[section] as AnyRow[]).map((row) => ({
      id: `${SINGULAR[section]}:${row.id}`,
      label: labelFor(section, row),
      relevance_score: null,
    }));
  }
  return content;
}

export function moveEntry(
  content: CvContent,
  section: string,
  index: number,
  delta: -1 | 1,
): CvContent {
  const list = content[section] ?? [];
  const target = index + delta;
  if (
    index < 0 ||
    index >= list.length ||
    target < 0 ||
    target >= list.length
  ) {
    return content;
  }
  const next = [...list];
  [next[index], next[target]] = [next[target], next[index]];
  return { ...content, [section]: next };
}

export function removeEntry(
  content: CvContent,
  section: string,
  index: number,
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  return { ...content, [section]: list.filter((_, i) => i !== index) };
}

export function toggleDeselect(
  content: CvContent,
  section: string,
  index: number,
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  const next = list.map((e, i) =>
    i === index ? { ...e, deselected: !e.deselected } : e,
  );
  return { ...content, [section]: next };
}

/** Deselected entries stripped — the shape the render/export pipeline (guide 4) consumes. */
export function activeContent(content: CvContent): CvContent {
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    out[section] = list.filter((e) => !e.deselected);
  }
  return out;
}

/** Career-DB rows of a section not (or no longer) in the content — the add-picker options. */
export function missingEntries(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entries: CvEntry[],
): AnyRow[] {
  if (!db) return [];
  const present = new Set(entries.map((e) => e.id));
  return (db[section] as AnyRow[]).filter(
    (r) => !present.has(entryId(section, r.id)),
  );
}

/** Append a career-DB row to a section — end of list (= lowest rank), no score. */
export function addEntry(
  content: CvContent,
  section: SectionKey,
  row: AnyRow,
): CvContent {
  const list = content[section] ?? [];
  const id = entryId(section, row.id);
  if (list.some((e) => e.id === id)) return content;
  return {
    ...content,
    [section]: [
      ...list,
      { id, label: labelFor(section, row), relevance_score: null },
    ],
  };
}
```

### 4. `frontend/src/routes/_authenticated/applications/$applicationId.tsx`

New imports:

```tsx
import { ArrowDown, ArrowUp, Eye, EyeOff, Trash2 } from "lucide-react";
import {
  SECTION_ORDER,
  SECTION_TITLES,
  addEntry,
  fromCareerDb,
  joinEntry,
  labelFor,
  missingEntries,
  moveEntry,
  removeEntry,
  toggleDeselect,
  type CvContent,
  type SectionKey,
} from "@/lib/cv-doc";
import {
  useCvEntries,
  useFullList,
  type CvEntriesResponse,
  type LayoutRow,
} from "@/lib/queries/jac";
```

Replace `ApplicationContentCard` and add the editor section component. The read-only `CvSection`
stays — the _run result_ card still uses it; only the application card becomes editable. The
cover-letter textarea block is untouched here (guide 3 rebuilds it):

```tsx
function ApplicationContentCard({ app }: { app: ApplicationRow }) {
  const update = useUpdateApplication();
  const layouts = useFullList<LayoutRow>("layouts");
  const careerDb = useCvEntries();
  const [coverLetter, setCoverLetter] = useState(app.cover_letter);
  const [status, setStatus] = useState<ApplicationStatus>(app.status);
  const [cvDraft, setCvDraft] = useState<CvContent>(app.cv_content ?? {});

  // "Adjusting state during render" (React docs, same pattern as usePagedList):
  // re-seed the local drafts when the server copy changes (apply / auto-fill),
  // discarding any unsaved edits in favour of the fresher server state. cv_content is
  // compared by value — a refetch returning identical JSON must not clobber the draft.
  const serverCv = JSON.stringify(app.cv_content ?? {});
  const [prevServer, setPrevServer] = useState({
    cover: app.cover_letter,
    status: app.status,
    cv: serverCv,
  });
  if (
    prevServer.cover !== app.cover_letter ||
    prevServer.status !== app.status ||
    prevServer.cv !== serverCv
  ) {
    setPrevServer({
      cover: app.cover_letter,
      status: app.status,
      cv: serverCv,
    });
    setCoverLetter(app.cover_letter);
    setStatus(app.status);
    setCvDraft(app.cv_content ?? {});
  }

  const dirty =
    coverLetter !== app.cover_letter ||
    status !== app.status ||
    JSON.stringify(cvDraft) !== serverCv;

  function onSave() {
    update.mutate(
      {
        id: app.id,
        body: { cover_letter: coverLetter, status, cv_content: cvDraft },
      },
      {
        onSuccess: () => toast.success("Application saved"),
        onError: () => toast.error("Could not save the application"),
      },
    );
  }

  // The layout is a FK pick, not a draft — persist it immediately.
  function onLayoutChange(v: string) {
    update.mutate(
      { id: app.id, body: { layout: Number(v) } },
      { onError: () => toast.error("Could not change the layout") },
    );
  }

  const hasCv = SECTION_ORDER.some((s) => (cvDraft[s] ?? []).length > 0);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Application content</CardTitle>
        <div className="flex items-center gap-2">
          <Select value={String(app.layout)} onValueChange={onLayoutChange}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Layout" />
            </SelectTrigger>
            <SelectContent>
              {(layouts.data ?? []).map((l) => (
                <SelectItem key={l.id} value={String(l.id)}>
                  {l.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={status}
            onValueChange={(v) => setStatus(v as ApplicationStatus)}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            onClick={onSave}
            disabled={!dirty || update.isPending}
          >
            Save
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {hasCv ? (
          <div className="space-y-4">
            {/* Every section renders (the section component hides itself only when it has
                neither entries nor addable rows), so an AI run that kept no project still
                offers the project add-picker. */}
            {SECTION_ORDER.map((section) => (
              <CvEditorSection
                key={section}
                section={section}
                entries={cvDraft[section] ?? []}
                db={careerDb.data}
                onEdit={setCvDraft}
              />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              No CV content yet — generate a run above, or build it by hand:
            </p>
            <Button
              variant="outline"
              size="sm"
              disabled={!careerDb.data}
              onClick={() =>
                careerDb.data && setCvDraft(fromCareerDb(careerDb.data))
              }
            >
              Start from full career DB
            </Button>
          </div>
        )}
        <Separator />
        <div className="space-y-1">
          <Label>Cover letter</Label>
          <Textarea
            rows={12}
            value={coverLetter}
            onChange={(e) => setCoverLetter(e.target.value)}
            placeholder="The applied run's cover letter lands here — edit freely."
          />
        </div>
      </CardContent>
    </Card>
  );
}

function CvEditorSection({
  section,
  entries,
  db,
  onEdit,
}: {
  section: SectionKey;
  entries: CvEntry[];
  db: CvEntriesResponse | undefined;
  onEdit: (fn: (c: CvContent) => CvContent) => void;
}) {
  const missing = missingEntries(db, section, entries);
  if (entries.length === 0 && missing.length === 0) return null;
  return (
    <div>
      <h3 className="text-sm font-semibold">{SECTION_TITLES[section]}</h3>
      <ul className="space-y-1">
        {entries.map((e, i) => {
          const row = db ? joinEntry(db, section, e) : null;
          const gone = db != null && row == null; // deleted from the career DB
          return (
            <li
              key={e.id}
              className={`flex items-center gap-1 text-sm ${
                e.deselected ? "opacity-50" : ""
              }`}
            >
              <span className={`flex-1 ${e.deselected ? "line-through" : ""}`}>
                {row ? labelFor(section, row) : e.label}
                {gone && (
                  <span className="ml-1 text-xs text-destructive">
                    (no longer in the career DB)
                  </span>
                )}
              </span>
              {e.relevance_score != null && (
                <Badge variant="outline">{e.relevance_score.toFixed(2)}</Badge>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Move up"
                disabled={i === 0}
                onClick={() => onEdit((c) => moveEntry(c, section, i, -1))}
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Move down"
                disabled={i === entries.length - 1}
                onClick={() => onEdit((c) => moveEntry(c, section, i, 1))}
              >
                <ArrowDown className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={e.deselected ? "Reselect" : "Deselect"}
                onClick={() => onEdit((c) => toggleDeselect(c, section, i))}
              >
                {e.deselected ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Delete"
                onClick={() => onEdit((c) => removeEntry(c, section, i))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          );
        })}
      </ul>
      {missing.length > 0 && (
        <Select
          value=""
          onValueChange={(v) => {
            const row = missing.find((r) => String(r.id) === v);
            if (row) onEdit((c) => addEntry(c, section, row));
          }}
        >
          <SelectTrigger className="mt-1 h-8 w-72 text-xs">
            <SelectValue
              placeholder={`Add ${SECTION_TITLES[section].toLowerCase()}…`}
            />
          </SelectTrigger>
          <SelectContent>
            {missing.map((r) => (
              <SelectItem key={r.id} value={String(r.id)}>
                {labelFor(section, r)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}
```

Subtleties:

- `onEdit` takes an updater so consecutive clicks compose on the latest draft
  (`setCvDraft(fn)` — React state updater form).
- Deselect ≠ delete: deselected entries stay in `cv_content` (dimmed, struck through) and are
  stripped by `activeContent()` at render time (guide 4). Delete removes the entry; the
  add-picker is the way back (it lists exactly the career-DB rows the section doesn't
  reference, so a deleted entry immediately reappears there).
- Added entries land at the **end** of their section with `relevance_score: null` — the tail
  is the lowest rank, so guide 4's fit drops a hand-added entry first unless the user moves it
  up. That's deliberate: an explicit ↑ is the user's ranking statement.
- The add Select keeps `value=""` so it always resets to the placeholder after a pick
  (a command dressed as a select, not a persistent choice).
- The layout Select PATCHes immediately (a FK pick, not part of the drafted content). The two
  system rows ("default" = one-page, "two-page") come from guide 1's seeder.

## Tests (written by the AI, already on this branch — start red)

- `frontend/tests/lib/cv-doc.test.ts` — `parseEntryId`, `labelFor` (job/education/certification
  shapes), `fromCareerDb` (order, ids, null scores), `moveEntry` (swap, clamp at edges,
  immutability), `removeEntry`, `toggleDeselect` (round-trip), `activeContent`,
  `missingEntries` (unreferenced rows only; empty without a DB), `addEntry` (appends at the
  tail with a built label + null score; no duplicates).

```bash
cd frontend && npx vitest run tests/lib/cv-doc.test.ts   # red until cv-doc.ts + jac.ts additions exist
npm test                                                  # full suite once green
```

## Verification

1. `npm test` red → green, `npm run build` clean (`tsc -b` catches the type additions).
2. Dev stack up; open an application **with** generated content: entries render with joined
   labels + score badges; move/deselect/delete update the list instantly; Save persists (reload
   → same state); deselected rows come back dimmed.
3. Open a **fresh** application: "Start from full career DB" fills every section, no score
   badges; prune + Save.
4. Switch the layout select between `default` and `two-page` → PATCH fires (network tab), reload
   keeps the pick.
5. Delete a career-DB row that an application references (e.g. a test skill) → the entry shows
   its stored label + "(no longer in the career DB)" instead of crashing.
6. Delete an entry from the CV, then re-add it via the section's "Add …" select → it reappears
   at the section's tail without a score badge; the select resets to its placeholder.

## Results

tests are green,
issues in the frontend

- opus generating run failed. needs to be invastigated
- ligth/default run dumped info. but i knwe there has been a job that fits good to the application.
- so far i could not add other entries from the db. manual adding entries only appeared after failed run. should
- changing the layout doesn't change a thing
- generation run can be applied. works well
- removing, hiding etc works
