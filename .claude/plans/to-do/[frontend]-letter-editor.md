# [frontend] letter-editor — structured letter meta, stub replacement, snippet append

> Guide 3 of 4 for the "frontend polish" phase. Branch: `frontend/letter-editor`.
> **Depends on guides 1 + 2 being merged** — guide 1 defines `letter_meta` /
> `closing` / body-only `cover_letter`; guide 2 reshaped `ApplicationContentCard`, which this
> guide extends. Merge `main` into this branch before starting.

## Context / goal

After guide 1 the application stores the letter as **body** (`cover_letter`) + **furniture**
(`letter_meta`: language, subject, salutation, date, closing, sender/recipient blocks). This
guide gives both halves an editor:

- meta fields (subject, date, salutation, closing, recipient + sender blocks) — prefilled by
  apply/auto-fill, hand-typed in manual mode;
- the body textarea (existing) plus two affordances the user asked for:
  - **stub replacement** — when the body still contains the backend's loud
    `PERSONAL_STUB` marker ("write your own paragraph…"), show a red banner with a textarea
    that swaps the marker for the user's own paragraph (or removes it);
  - **snippet append** — pull any `ResumeSnippet` into the body, for building/extending a
    letter manually.

Also: "applying" a run now writes `letter_meta` + the body-only letter, and the page's
`applied` check compares against the body (the full `text` is no longer what's stored).

## Affected files

| file | why |
| --- | --- |
| `frontend/src/lib/letter-doc.ts` | **new** — pure letter logic: meta types/mapping, `editableBody`, stub detect/replace, append |
| `frontend/src/lib/queries/generations.ts` | `CoverLetterResult` gains `closing: string` (added backend-side in guide 1) |
| `frontend/src/lib/queries/applications.ts` | `letter_meta` on row/patch types; `runToApplicationPatch` maps body + meta |
| `frontend/src/routes/_authenticated/applications/$applicationId.tsx` | letter area of `ApplicationContentCard` becomes `LetterEditor`; `applied` check updated |

## The code

### 1. `frontend/src/lib/queries/generations.ts`

In `CoverLetterResult`, after `date`:

```ts
  date: string;
  closing: string;
```

### 2. `frontend/src/lib/letter-doc.ts` — new file, pure logic (unit-tested)

```ts
/**
 * Pure logic for the application's cover letter.
 *
 * Post guide 1 the letter lives in two fields: `cover_letter` = the editable body (woven
 * snippets + personal paragraph or stub) and `letter_meta` = the furniture (subject,
 * salutation, date, closing, sender/recipient) that render/export re-assemble around it.
 */
import type { CoverLetterResult } from "@/lib/queries/generations";

/** Must match backend jac/cover_letter.py PERSONAL_STUB byte for byte. */
export const PERSONAL_STUB =
  "⚠️⚠️ WRITE A PERSONAL PARAGRAPH YOU LAZY PIECE OF SHIT ⚠️⚠️";

export type LetterMeta = {
  language: string;
  subject: string;
  salutation: string;
  date: string; // ISO yyyy-mm-dd
  closing: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
};

export function emptyLetterMeta(): LetterMeta {
  return {
    language: "en",
    subject: "",
    salutation: "",
    date: new Date().toISOString().slice(0, 10),
    closing: "",
    sender: {},
    recipient: {},
  };
}

/** Stored letter_meta may be `{}` (pre-guide-1 rows, manual mode) or partial — fill gaps. */
export function normalizeLetterMeta(raw: unknown): LetterMeta {
  const r = (raw ?? {}) as Partial<LetterMeta>;
  return {
    ...emptyLetterMeta(),
    ...r,
    sender: { ...(r.sender ?? {}) },
    recipient: { ...(r.recipient ?? {}) },
  };
}

export function letterMetaFromResult(letter: CoverLetterResult): LetterMeta {
  return {
    language: letter.language,
    subject: letter.subject,
    salutation: letter.salutation,
    date: letter.date,
    closing: letter.closing,
    sender: letter.sender,
    recipient: letter.recipient,
  };
}

/** Mirror of backend jac/cover_letter.py editable_body(): body + personal paragraph/stub. */
export function editableBody(letter: CoverLetterResult): string {
  const parts = [letter.body];
  if (letter.personal_paragraph) parts.push(letter.personal_paragraph);
  return parts.filter(Boolean).join("\n\n");
}

export function hasStub(text: string): boolean {
  return text.includes(PERSONAL_STUB);
}

/**
 * Swap every stub marker for the user's paragraph. An empty paragraph removes the stub
 * instead, collapsing the blank-line padding it sat between.
 */
export function replaceStub(text: string, paragraph: string): string {
  const p = paragraph.trim();
  if (p) return text.split(PERSONAL_STUB).join(p);
  return text
    .split("\n\n")
    .map((block) => block.split(PERSONAL_STUB).join("").trim())
    .filter((block) => block !== "")
    .join("\n\n");
}

/** Append a paragraph (e.g. a snippet's content) as its own block. */
export function appendParagraph(text: string, paragraph: string): string {
  const p = paragraph.trim();
  if (!p) return text;
  const base = text.replace(/\s+$/, "");
  return base ? `${base}\n\n${p}` : p;
}
```

### 3. `frontend/src/lib/queries/applications.ts`

```ts
import { editableBody, letterMetaFromResult, type LetterMeta } from "@/lib/letter-doc";
```

`ApplicationRow` gains (after `cover_letter`):

```ts
  letter_meta: Partial<LetterMeta>;
```

`ApplicationPatch` gains:

```ts
  letter_meta: LetterMeta;
```

And the apply-patch helper becomes:

```ts
/** The PATCH that "applies" a finished run's result onto the application. */
export function runToApplicationPatch(result: TailoredResult): ApplicationPatch {
  return {
    cv_content: result.cv,
    cover_letter: editableBody(result.cover_letter),
    letter_meta: letterMetaFromResult(result.cover_letter),
  };
}
```

### 4. `frontend/src/routes/_authenticated/applications/$applicationId.tsx`

New imports:

```tsx
import {
  appendParagraph,
  hasStub,
  normalizeLetterMeta,
  replaceStub,
  type LetterMeta,
} from "@/lib/letter-doc";
import { editableBody } from "@/lib/letter-doc";
import { Input } from "@/components/ui/input";
import { type ResumeSnippetRow } from "@/lib/queries/jac";
```

**`applied` check** in `ApplicationDetailPage` — the application now stores the body, not the
full text:

```tsx
        <ResultView
          applicationId={id}
          state={state}
          applied={app.data.cover_letter === editableBody(state.result.cover_letter)}
        />
```

**`ApplicationContentCard`** — add a `letterMeta` draft next to the guide-2 drafts. The
normalized server snapshot is what both the seed and the dirty check compare against, so a
legacy `{}` meta doesn't read as "dirty" on load:

```tsx
  const serverMeta = JSON.stringify(normalizeLetterMeta(app.letter_meta));
  const [letterMeta, setLetterMeta] = useState<LetterMeta>(() =>
    normalizeLetterMeta(app.letter_meta),
  );
```

extend `prevServer` with `meta: serverMeta`, the reset branch with
`setLetterMeta(normalizeLetterMeta(app.letter_meta))`, the `dirty` check with
`JSON.stringify(letterMeta) !== serverMeta`, and `onSave`'s body with
`letter_meta: letterMeta`.

Then replace the plain cover-letter block (label + textarea) with:

```tsx
        <Separator />
        <LetterEditor
          meta={letterMeta}
          onMeta={setLetterMeta}
          body={coverLetter}
          onBody={setCoverLetter}
        />
```

And add the two new components at the bottom of the file:

```tsx
/* ---------- letter editor ---------- */

function MetaField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

const RECIPIENT_FIELDS: [string, string][] = [
  ["company", "Company"],
  ["contact_name", "Contact"],
  ["street", "Street"],
  ["zip", "ZIP"],
  ["city", "City"],
  ["country", "Country"],
  ["email", "Email"],
];

const SENDER_FIELDS: [string, string][] = [
  ["name", "Name"],
  ["street", "Street"],
  ["zip", "ZIP"],
  ["city", "City"],
  ["email", "Email"],
  ["phone", "Phone"],
];

function LetterEditor({
  meta,
  onMeta,
  body,
  onBody,
}: {
  meta: LetterMeta;
  onMeta: (m: LetterMeta) => void;
  body: string;
  onBody: (b: string) => void;
}) {
  const snippets = useFullList<ResumeSnippetRow>("snippets");
  const [stubDraft, setStubDraft] = useState("");
  const [snippetId, setSnippetId] = useState("");

  const setField = (field: keyof LetterMeta) => (v: string) =>
    onMeta({ ...meta, [field]: v });
  const setBlockField =
    (block: "recipient" | "sender", field: string) => (v: string) =>
      onMeta({ ...meta, [block]: { ...meta[block], [field]: v } });

  function onAppendSnippet() {
    const s = snippets.data?.find((r) => String(r.id) === snippetId);
    if (!s) return;
    onBody(appendParagraph(body, s.content));
    setSnippetId("");
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <div className="col-span-2">
          <MetaField label="Subject" value={meta.subject} onChange={setField("subject")} />
        </div>
        <MetaField label="Date" value={meta.date} onChange={setField("date")} />
        <MetaField label="Language" value={meta.language} onChange={setField("language")} />
        <div className="col-span-2">
          <MetaField
            label="Salutation"
            value={meta.salutation}
            onChange={setField("salutation")}
          />
        </div>
        <div className="col-span-2">
          <MetaField label="Closing" value={meta.closing} onChange={setField("closing")} />
        </div>
      </div>

      <details className="rounded border p-3">
        <summary className="cursor-pointer text-sm font-medium">Recipient</summary>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          {RECIPIENT_FIELDS.map(([field, label]) => (
            <MetaField
              key={field}
              label={label}
              value={meta.recipient[field] ?? ""}
              onChange={setBlockField("recipient", field)}
            />
          ))}
        </div>
      </details>

      <details className="rounded border p-3">
        <summary className="cursor-pointer text-sm font-medium">Sender</summary>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          {SENDER_FIELDS.map(([field, label]) => (
            <MetaField
              key={field}
              label={label}
              value={meta.sender[field] ?? ""}
              onChange={setBlockField("sender", field)}
            />
          ))}
        </div>
      </details>

      <div className="space-y-1">
        <Label>Cover letter body</Label>
        <Textarea
          rows={12}
          value={body}
          onChange={(e) => onBody(e.target.value)}
          placeholder="The applied run's letter body lands here — or write your own."
        />
      </div>

      {hasStub(body) && (
        <div className="space-y-2 rounded border border-destructive/50 bg-destructive/10 p-3">
          <p className="text-xs font-medium">
            The body still contains the personal-paragraph stub — not sendable. Write your
            own paragraph to replace it:
          </p>
          <Textarea
            rows={4}
            value={stubDraft}
            onChange={(e) => setStubDraft(e.target.value)}
            placeholder="Why this company, in your own words…"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!stubDraft.trim()}
              onClick={() => {
                onBody(replaceStub(body, stubDraft));
                setStubDraft("");
              }}
            >
              Replace stub
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onBody(replaceStub(body, ""))}
            >
              Remove stub
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Append a snippet</Label>
          <Select value={snippetId} onValueChange={setSnippetId}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Pick a snippet…" />
            </SelectTrigger>
            <SelectContent>
              {(snippets.data ?? []).map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.kind}: {s.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" variant="outline" disabled={!snippetId} onClick={onAppendSnippet}>
          Append
        </Button>
      </div>
    </div>
  );
}
```

Subtleties:

- Stub replacement edits the **draft** body — nothing persists until Save, so a bad paragraph
  is one reseed (reload) away from gone.
- The stub banner triggers on the marker text itself, so it also fires on a letter applied
  *before* this guide (full-text letters contain the same marker) — that's fine, replacing it
  is exactly what the user wants there too.
- The snippet select reuses `useFullList("snippets")` (the resource already exists in `R`).

## Tests (written by the AI, already on this branch — start red)

- `frontend/tests/lib/letter-doc.test.ts` — meta normalize/mapping, `editableBody` (with/
  without personal paragraph), `hasStub`/`replaceStub` (replace, multi-occurrence, removal
  collapses padding), `appendParagraph`.
- `frontend/tests/lib/applications.test.ts` — **updated**: `runToApplicationPatch` now expected
  to carry the body-only letter + `letter_meta` (this file was green; it goes red until the
  helper is reworked).

```bash
cd frontend && npx vitest run tests/lib/letter-doc.test.ts tests/lib/applications.test.ts
npm test   # full suite once green
```

## Verification

1. `npm test` red → green; `npm run build` clean.
2. Generate a run with the personal paragraph **off** or non-capable (light grade) → apply →
   the red stub banner appears; write a paragraph → Replace stub → banner gone, body reads
   naturally; Save; reload keeps it.
3. Apply a run: subject/salutation/date/closing/recipient fields are prefilled from the run
   (`letter_meta`); edit the subject; Save; reload keeps it. The Apply button flips to
   "Applied" (body comparison).
4. Manual path: fresh application, type recipient + subject by hand, append two snippets,
   Save — `letter_meta` and `cover_letter` persist (check the PATCH payload in the network
   tab).
