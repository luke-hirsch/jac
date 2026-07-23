# [frontend] letter-matrix-ui

> **QUEUED — do after `[backend]-letter-matrix-pipeline`.** Guide 2 of 3 of the "gold-standard cover
> letter" rework. Volatile/dev phase, one clean break, no dead code ([[no-compat-clean-breaks]]).
> Branch: `frontend/letter-matrix-ui` off `main` (after guide 1 merged). Tests land skip-marked at
> activation (step 0 = unskip); [[frontend-test-layout]] (vitest, `frontend/tests/` mirror, pure
> `lib/` only).

## Context / goal

Guide 1 rebuilt the backend letter around a **tone × focus matrix** + a **writing-style dossier** on
`PersonalityProfile`, dropped snippets / `ai_share` / the personal-paragraph split, and added an
optional per-run matrix override on `GenerationRun`. This guide brings the SPA in line:

1. **Personality page** gains the matrix pickers (tone/focus), a **writing-sample** textarea, and a
   read-only **style-dossier** display beside the existing personality dossier.
2. **Generate panel** gains an optional per-application **tone/focus override** (defaulted from the
   personality row), threaded into the run payload.
3. **Remove snippets from the UI entirely** — the Snippets route, its form lib, the CRUD query
   resource, the letter-editor "Append a snippet" affordance, and any orphaned pickers.
4. **Result surfaces**: drop the `ai_share` and quality badges + the snippet-ranking /
   personal-paragraph-stub chrome; keep the grounding badge. Two stubs now: the **hard** `LETTER_STUB`
   (writer failure) still gates export; the new **soft** `COMPANY_STUB` (no-research company hook)
   shows in the editor but is **stripped from pdf/md exports** and never blocks.

## Affected files

| path | change |
| --- | --- |
| `frontend/src/lib/queries/generations.ts` | `CoverLetterResult`: drop snippet/ai_share/critique/personal_paragraph fields, add `tone`/`focus`/`sources`/`is_stub`; `GenerationForm`/`GenerationPayload`/`toPayload`: add `letter_tone`/`letter_focus`; **delete** `aiShareBadge`/`qualityBadge` |
| `frontend/src/lib/queries/personality.ts` | `PersonalityRow`: add matrix + style fields; `TONE_OPTIONS`/`FOCUS_OPTIONS`; `styleState()`; `useUpdateLetterSettings()` |
| `frontend/src/routes/_authenticated/account/personality.tsx` | matrix Selects + writing-sample textarea + style-dossier display |
| `frontend/src/components/applications/generate-panel.tsx` | per-run tone/focus override; remove ai/quality badges + snippet-ranking + personal-paragraph-stub blocks |
| `frontend/src/lib/letter-doc.ts` | `PERSONAL_STUB` → `LETTER_STUB`; add soft `COMPANY_STUB` + `stripSoftStub`; `editableBody` = just `body`; prune personal-paragraph-only helpers |
| `frontend/src/lib/export.ts` | `exportBlocker` blocks on `LETTER_STUB` only; `stripSoftStub` the body in md + pdf export paths |
| `frontend/src/components/applications/content-card.tsx` | stub-warning toast copy |
| `frontend/src/components/applications/letter-editor.tsx` | delete the "Append a snippet" block + `ResumeSnippetRow` usage |
| `frontend/src/lib/queries/jac.ts` | delete the `snippets` resource + its union membership |
| `frontend/src/routes/_authenticated/cv.tsx`, `cv/index.tsx` | delete the Snippets nav link + resource card |
| `frontend/src/routes/_authenticated/cv/snippets.tsx`, `frontend/src/lib/snippet-form.ts` | **delete** the files |
| `frontend/src/components/cv/{job,project}-picker.tsx` | delete **only if** orphaned once the snippet form is gone (grep first) |

---

## The code

### 1. `frontend/src/lib/queries/generations.ts`

Replace the snippet/personal-paragraph fields on `CoverLetterResult` with the new matrix shape (keep
`Grounding`; **delete** the `Critique` type + `snippet_ranking`):

```ts
export type CoverLetterResult = {
  language: string;
  subject: string;
  salutation: string;
  body: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
  date: string;
  closing: string;
  /** The tone×focus cell this letter was written in (run override or profile default). */
  tone: string;
  focus: string;
  grounding: Grounding;
  /** Company-research source URLs (commercial web-search runs); [] otherwise. */
  sources: string[];
  /** The writer produced nothing — body is LETTER_STUB, must be regenerated. */
  is_stub: boolean;
  text: string;
};
```

Add the matrix override to the form/payload and `toPayload`:

```ts
export type GenerationForm = {
  job_application: number;
  mode: Exclude<Mode, ""> | "manual"; // "" = server default (standard)
  provider: string; // "" = the user's default executor
  model: string; // "" = catalog default
  params?: GenerationParams;
  letter_tone?: string; // "" = the profile default
  letter_focus?: string;
};

export type GenerationPayload = {
  job_application: number;
  mode?: string;
  provider?: string;
  model?: string;
  params?: GenerationParams;
  letter_tone?: string;
  letter_focus?: string;
};

export function toPayload(f: GenerationForm): GenerationPayload {
  const p: GenerationPayload = { job_application: f.job_application };
  if (f.mode) p.mode = f.mode;
  if (f.provider) p.provider = f.provider;
  if (f.model) p.model = f.model;
  if (f.params && Object.keys(f.params).length) p.params = f.params;
  if (f.letter_tone) p.letter_tone = f.letter_tone;
  if (f.letter_focus) p.letter_focus = f.letter_focus;
  return p;
}
```

**Delete** `aiShareBadge` and `qualityBadge` (and the `Critique` type). Keep `Badge` +
`groundingBadge` unchanged.

### 2. `frontend/src/lib/queries/personality.ts`

Extend the row type and add the matrix option lists, a style-freshness helper (mirror of
`dossierState`), and a settings mutation:

```ts
export type LetterTone = "personal" | "neutral" | "formal";
export type LetterFocus = "soft_skill" | "balanced" | "technical";

export type PersonalityRow = {
  id: number;
  answers: Record<string, string>;
  dossier: string;
  questions: PersonalityQuestion[];
  answers_updated_at: string | null;
  dossier_built_at: string | null;
  // Letter matrix + writing-style probe (guide 1).
  letter_tone: LetterTone;
  letter_focus: LetterFocus;
  writing_sample: string;
  style_dossier: string;
  sample_updated_at: string | null;
  style_built_at: string | null;
  updated_at: string;
};

/** Labels mirror spa PersonalityProfile.Tone/.Focus (German intent in comments). */
export const TONE_OPTIONS: { value: LetterTone; label: string }[] = [
  { value: "personal", label: "Personal" }, // persönlich
  { value: "neutral", label: "Neutral" },
  { value: "formal", label: "Formal" }, // förmlich
];
export const FOCUS_OPTIONS: { value: LetterFocus; label: string }[] = [
  { value: "soft_skill", label: "Soft-skill focus" }, // Soft-Skill-Fokus
  { value: "balanced", label: "Balanced" }, // ausgewogen
  { value: "technical", label: "Technical focus" }, // technischer Fokus
];

/** Style-dossier freshness — mirrors PersonalityProfile.style_stale server-side.
 *  Generation rebuilds it automatically on the next run (ensure_style_dossier). */
export function styleState(
  row: Pick<
    PersonalityRow,
    "writing_sample" | "style_dossier" | "sample_updated_at" | "style_built_at"
  >,
): "none" | "stale" | "fresh" {
  if (!row.writing_sample.trim()) return "none";
  if (!row.style_dossier || !row.style_built_at) return "stale";
  if (
    row.sample_updated_at &&
    Date.parse(row.sample_updated_at) > Date.parse(row.style_built_at)
  )
    return "stale";
  return "fresh";
}
```

Settings mutation (a partial PATCH — tone/focus save on change, sample via its own Save button):

```ts
export function useUpdateLetterSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      patch: Partial<
        Pick<PersonalityRow, "letter_tone" | "letter_focus" | "writing_sample">
      >,
    ) =>
      api<PersonalityRow>(URL, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
```

`personalityHint` (the generate-panel nag) referenced the personal paragraph — its wording is stale
now that the paragraph is folded in. Keep the hook but retune the copy (a capable executor + zero
personality answers still yields a thinner letter):

```ts
export function personalityHint(
  capable: boolean,
  row: PersonalityRow | undefined,
): string | null {
  if (!capable || !row) return null;
  if (answeredCount(row.answers) > 0) return null;
  return (
    "No personality answers yet — the letter can't reflect who you are. " +
    "Fill the questionnaire under Account → Personality."
  );
}
```

### 3. `frontend/src/routes/_authenticated/account/personality.tsx`

Import `Select*` (from `@/components/ui/select`) and the new symbols
(`TONE_OPTIONS`, `FOCUS_OPTIONS`, `styleState`, `useUpdateLetterSettings`). Add a matrix + style
block above the `Dossier` section. Tone/focus persist immediately on change; the writing sample uses
a dirty-tracked local draft + Save (mirroring the answers pattern):

```tsx
const settings = useUpdateLetterSettings();
const [sample, setSample] = useState<string | null>(null);
if (personality.data && sample === null) setSample(personality.data.writing_sample);
// ... existing loading guard covers sample === null too ...

const styleFresh = styleState(row);
const sampleDirty = sample !== null && sample.trim() !== row.writing_sample.trim();

function saveMatrix(patch: { letter_tone?: LetterTone; letter_focus?: LetterFocus }) {
  settings.mutate(patch, {
    onError: () => toast.error("Could not save the letter setting"),
  });
}
function saveSample() {
  settings.mutate(
    { writing_sample: (sample ?? "").trim() },
    {
      onSuccess: () => toast.success("Writing sample saved"),
      onError: () => toast.error("Could not save the writing sample"),
    },
  );
}
```

```tsx
<Separator />

<div className="space-y-4">
  <div>
    <h3 className="text-sm font-medium">Cover-letter voice</h3>
    <p className="text-sm text-muted-foreground">
      The default tone × focus for every letter, and a sample of your own writing
      the model imitates. A run can override the tone/focus for one application.
    </p>
  </div>

  <div className="grid grid-cols-2 gap-3">
    <div className="space-y-1">
      <Label>Tone</Label>
      <Select
        value={row.letter_tone}
        onValueChange={(v) => saveMatrix({ letter_tone: v as LetterTone })}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {TONE_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
    <div className="space-y-1">
      <Label>Focus</Label>
      <Select
        value={row.letter_focus}
        onValueChange={(v) => saveMatrix({ letter_focus: v as LetterFocus })}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {FOCUS_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  </div>

  <div className="space-y-1">
    <Label htmlFor="writing-sample">Writing sample</Label>
    <Textarea
      id="writing-sample"
      value={sample ?? ""}
      rows={6}
      placeholder="Paste a few paragraphs you wrote — an email, a blog post, an old cover letter…"
      onChange={(e) => setSample(e.target.value)}
    />
    <div className="flex items-center gap-3">
      <Button onClick={saveSample} disabled={!sampleDirty || settings.isPending}>
        {settings.isPending ? "Saving…" : "Save sample"}
      </Button>
      <Badge variant="outline">
        {styleFresh === "none"
          ? "no style yet"
          : styleFresh === "stale"
            ? "rebuilds on the next generation"
            : "up to date"}
      </Badge>
    </div>
    {row.style_dossier && (
      <p className="whitespace-pre-wrap rounded border bg-muted/40 p-3 text-sm">
        {row.style_dossier}
      </p>
    )}
  </div>
</div>
```

### 4. `frontend/src/components/applications/generate-panel.tsx`

**Remove** the dropped badges + chrome:

- delete the `aiShareBadge`/`qualityBadge` imports and the `ai`/`quality` badge locals (≈ lines
  34–39, 182, 186) — render only `groundingBadge(result.cover_letter.grounding)`;
- delete the `result.cover_letter.snippet_ranking` block (≈ 478–483) and the
  `result.cover_letter.personal_paragraph_is_stub` block (≈ 486).

**Add** the per-run tone/focus override. Read the personality row (`usePersonality()` is already the
hint's source) for the defaults, and hold two local override states seeded to `""` (= use the profile
default). Wire them into the `GenerationForm` passed to `useCreateGeneration`:

```tsx
// defaults shown in the pickers; "" in the form means "use the profile default"
const personalityRow = personality.data;
const [toneOverride, setToneOverride] = useState<LetterTone | "">("");
const [focusOverride, setFocusOverride] = useState<LetterFocus | "">("");

// in the form object handed to the mutation:
letter_tone: toneOverride,
letter_focus: focusOverride,
```

```tsx
{/* Letter voice (optional per-application override of the account default) */}
<div className="grid grid-cols-2 gap-2">
  <Select
    value={toneOverride || (personalityRow?.letter_tone ?? "neutral")}
    onValueChange={(v) => setToneOverride(v as LetterTone)}
  >
    <SelectTrigger className="h-8 text-xs">
      <SelectValue placeholder="Tone" />
    </SelectTrigger>
    <SelectContent>
      {TONE_OPTIONS.map((o) => (
        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
      ))}
    </SelectContent>
  </Select>
  <Select
    value={focusOverride || (personalityRow?.letter_focus ?? "balanced")}
    onValueChange={(v) => setFocusOverride(v as LetterFocus)}
  >
    <SelectTrigger className="h-8 text-xs">
      <SelectValue placeholder="Focus" />
    </SelectTrigger>
    <SelectContent>
      {FOCUS_OPTIONS.map((o) => (
        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
      ))}
    </SelectContent>
  </Select>
</div>
```

(Selecting a value stamps the override; leaving it stamps `""` so the server uses the profile
default. If you prefer an explicit "account default" sentinel row, add one — but `""` + the shown
default reads fine.)

### 5. `frontend/src/lib/letter-doc.ts`

Two sentinels now. `LETTER_STUB` is the **hard** failure marker (blocks export). `COMPANY_STUB` is
the **soft** company-hook nudge — the editor shows it, but it is **stripped from exports**, so a
right-away export is clean and never blocked:

```ts
/** Must match backend jac/cover_letter.py LETTER_STUB byte for byte. Hard failure — blocks export. */
export const LETTER_STUB =
  "⚠️⚠️ THE MODEL COULD NOT WRITE THIS LETTER — regenerate before sending ⚠️⚠️";

/** Must match backend jac/cover_letter.py COMPANY_STUB byte for byte. Soft nudge — shown in the
 *  editor, stripped from pdf/md exports (never blocks). */
export const COMPANY_STUB =
  "⟨ add one line on why THIS company — omitted from exports until you do ⟩";

export function editableBody(letter: CoverLetterResult): string {
  return letter.body;
}

/** Hard-stub check — the export blocker keys on THIS only (the soft stub never blocks). */
export function hasStub(text: string): boolean {
  return text.includes(LETTER_STUB);
}

/** Drop the soft company-hook line (and its blank-line padding) — applied to the body before any
 *  pdf/md export so a right-away export is clean. The editor keeps the raw body. */
export function stripSoftStub(text: string): string {
  return text
    .split("\n\n")
    .filter((block) => !block.includes(COMPANY_STUB))
    .join("\n\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
```

Delete `PERSONAL_STUB`, `replaceStub` (there is no user-replaces-the-paragraph flow anymore — a hard
stub means regenerate), and `appendParagraph` **iff** grep shows the only caller was the
letter-editor snippet block removed in step 8 (`rg -n "appendParagraph|replaceStub" frontend/src`).
`replaceRange` / the meta helpers stay.

### 6. `frontend/src/lib/export.ts` + `content-card.tsx`

`exportBlocker` blocks on the **hard** stub only (`hasStub` → `LETTER_STUB`); the soft company stub
never blocks — it is stripped instead:

```ts
  if (hasStub(body)) {
    return "The letter could not be generated (placeholder still in the body) — regenerate before exporting.";
  }
```

**Strip the soft stub in the sendable export paths.** In `letterToMarkdown`, strip first so md never
carries it:

```ts
export function letterToMarkdown(meta: LetterMeta, body: string): string {
  body = stripSoftStub(body); // soft company-hook nudge never reaches an export
  // …unchanged assembly…
}
```

Do the same on the **pdf** path: wherever the letter body is handed to the react-pdf
`LetterDocument` builder (`lib/render/*` / the export-card's pdf action), pass `stripSoftStub(body)`.
JSON export may keep it (a raw data dump, not a sendable artefact). The **editor keeps the raw body**
(`content-card`/`letter-editor` render `app.cover_letter` as-is) so the nudge stays visible.

`content-card.tsx` `onSave` warning stays keyed on the **hard** stub only (a soft stub is fine to
save — it just won't export):

```tsx
      toast.warning(
        "The letter is a placeholder — regenerate it before marking the application sent.",
      );
```

### 7–9. Snippet removal (checklist)

Confirm with `rg -n "snippet" frontend/src --glob '!routeTree.gen.ts'` coming back empty when done.

- **`letter-editor.tsx`** — delete the `useFullList<ResumeSnippetRow>("snippets")` call, the
  `snippetId` state, the "Append a snippet" `Select`/`Button` JSX (≈ lines 81–302), and the
  `ResumeSnippetRow` import.
- **`lib/queries/jac.ts`** — delete the `snippets: { key, url }` resource (line ≈ 152) and drop
  `"snippets"` from the `Extract<ResourceKey, …>` union (line ≈ 282) + the `ResumeSnippetRow` type
  if it lives here.
- **`routes/_authenticated/cv.tsx`** — delete the `{ to: "/cv/snippets", label: "Snippets" }` nav
  entry.
- **`routes/_authenticated/cv/index.tsx`** — delete the `snippets` resource card (≈ lines 41–44).
- **Delete** `routes/_authenticated/cv/snippets.tsx` and `lib/snippet-form.ts`.
- **`components/cv/job-picker.tsx` / `project-picker.tsx`** — these were the snippet form's job/
  project pickers. `rg -n "JobPicker|ProjectPicker" frontend/src`: if the snippet form was their only
  importer, delete them; if reused (e.g. project→job linkage), keep.
- Run `tsc -b`; the TanStack router regenerates `routeTree.gen.ts` on the next dev/build (or
  `npm run build`) so the deleted route drops out — don't hand-edit it.

---

## Tests

Land skip-marked at activation; unskip = step 0. Pure `lib/` only per [[frontend-test-layout]].

- `frontend/tests/lib/generations.test.ts` — extend: `toPayload` includes `letter_tone`/
  `letter_focus` only when non-empty; omits them when `""`. Confirm `aiShareBadge`/`qualityBadge` are
  gone (a compile-time removal — the import in the test file is deleted).
- `frontend/tests/lib/personality.test.ts` — `styleState` matrix: `none` (blank sample), `stale`
  (no dossier / sample newer than build), `fresh`; `personalityHint` new copy still gates on
  `answeredCount`.
- `frontend/tests/lib/letter-doc.test.ts` — `hasStub` true for `LETTER_STUB`, **false for
  `COMPANY_STUB`** and arbitrary text; `stripSoftStub` removes a `COMPANY_STUB` block + collapses its
  blank lines, leaves a stub-free body untouched; `editableBody` returns exactly `letter.body`.
- `frontend/tests/lib/export.test.ts` — `exportBlocker` refuses a letter-scope pdf/md containing
  `LETTER_STUB`, **allows one containing only `COMPANY_STUB`**, allows json + cv scope;
  `letterToMarkdown` output does **not** contain `COMPANY_STUB` when the input body did.

Run: `cd frontend && npx vitest run tests/lib/generations.test.ts tests/lib/personality.test.ts tests/lib/letter-doc.test.ts tests/lib/export.test.ts`

---

## Verification

1. `cd frontend && tsc -b` — clean (all removed fields/functions have no live references).
2. `npx vitest run` — green.
3. Click-through: Account → Personality shows Tone/Focus selects + writing-sample box; save a sample,
   run a generation, confirm the style dossier appears afterward (rebuilt by `ensure_style_dossier`).
4. Generate panel: pick a non-default tone/focus for one application, run it → the result's
   `cover_letter.tone`/`focus` reflect the override; a run with no override uses the account default.
5. No Snippets nav item anywhere; `/cv/snippets` 404s; the letter editor has no "Append a snippet".
6. Kill ollama, run → the letter body shows `LETTER_STUB`; exporting a letter/complete pdf is
   blocked with the "regenerate" message; JSON export still works.
7. A working HirschAI standard run → the editor shows the soft `COMPANY_STUB` line; exporting pdf/md
   right away produces a clean letter **without** it and is **not** blocked; typing a real company
   line replaces the nudge and that text exports normally.

**Done looks like:** the matrix + writing-style live on the personality page and thread into runs
(with an optional per-app override), snippets are gone from the UI, and the result surfaces show only
the grounding badge + the letter — no ai_share, no personal-paragraph stub chrome.

## Results

<!-- Human fills this in after testing: raw test output, observed issues, what works. -->
