# Phase 3c setup guide — editor chrome + certification fix (frontend)

Goal: turn the career-entry side drawer into something you can actually work in for an hour
straight, and stop it from silently swallowing certification creates. By the end the editor
Sheet is meaningfully wider and its form body is padded to share the header's gutter (today
fields run flush to the panel edge under an inset title, and the panel is capped far narrower
than intended); creating a certification with a name + issuer hits the backend and persists;
and a required field that the form forgot to render can no longer fail submission *silently* —
a form-level banner tells you the form is invalid even when there's no visible field to pin the
error to.

This is **Phase 3c only** — pure frontend, three small things in three files. It deliberately
does **not** add the skill/relation pickers (those are 3d), touch the backend (serializers
already accept everything), or restyle anything beyond the drawer chrome. If you reach for a new
component or a model field, you're in 3d's lane.

Run frontend steps from `frontend/`. If a step's "verify" check fails, stop and fix before
moving on.

---

## 0. Preflight

Phase 3b must be committed and green.

```bash
cd frontend
git log --oneline -1     # expect "phase 3b - bulk api backend" (def0c80) or later
npx tsc -b               # zero output
```

Reproduce the bug you're about to fix, so you know the fix took: with the app running, open
`/cv/certifications` → **New** → fill in only the visible fields (Title, dates, URL, credential,
description) → **Create**. Nothing happens: no toast, no row, and the Network tab shows **no**
`POST /api/jac/certifications/`. That silent no-op is the symptom.

---

## 1. The contract you're coding against

Backend is unchanged — pin what it already expects so the fix targets the real cause.

`CertificationSerializer` ([backend/jac/serializers.py](backend/jac/serializers.py)) requires
**both** `name` and `issuer` (neither has `blank=True` on the model), everything else optional:

```jsonc
POST /api/jac/certifications/
{ "name": "AWS SAA", "issuer": "Amazon", "issued_on": "2023-04-01",
  "expires_on": null, "credential_id": "", "url": "", "description": "" }
// 201 → the created row
```

The frontend Zod schema in [certifications.tsx](frontend/src/routes/_authenticated/cv/certifications.tsx)
already mirrors this — `issuer: z.string().min(1).max(200)` — **but the editor never renders an
`issuer` input.** So `issuer` is initialised to `""` and can never change; `validators.onChange`
fails every keystroke; `form.handleSubmit()` sees an invalid form and **no-ops without calling
`onSubmit`**; and because there's no `issuer` field on screen, the per-field `<FieldError>` has
nowhere to show. Net effect: the user fills everything they can see and the POST never fires.

> The root cause is *a required field with no input*, not anything backend-side. The fix is to
> render the field — plus a safety net (§3.3) so the next time this happens it's loud, not silent.

---

## 2. Stack additions

**None.** Tailwind utility classes + an existing `form.Subscribe` from TanStack Form.

---

## 3. The three changes

### 3.1. Roomier drawer — [section-page.tsx](frontend/src/components/cv/section-page.tsx)

Two problems in the one `<SheetContent>`:

1. **It's narrower than it looks.** The shared `SheetContent`
   ([components/ui/sheet.tsx](frontend/src/components/ui/sheet.tsx)) hardcodes
   `data-[side=right]:sm:max-w-sm` (384px). The page passes a plain `sm:max-w-2xl`, but the
   `data-[side=right]:` variant carries higher CSS specificity, so the 384px cap *wins* — the
   drawer is stuck tiny regardless of the `2xl`. Override with the **same** `data-[side=right]:`
   variant so `tailwind-merge` collapses the duplicate and the wider value actually applies.
2. **No body padding.** The form body wrapper is a bare `<div className="mt-4">`; the header is
   `p-8`. Give the body the header's horizontal gutter + a bottom inset.

```tsx
<SheetContent className="w-full data-[side=right]:sm:max-w-2xl data-[side=right]:lg:max-w-3xl overflow-y-auto">
  <SheetHeader>
    <SheetTitle>
      {editing ? "Edit" : "New"} {title.toLowerCase()}
    </SheetTitle>
  </SheetHeader>
  {/* Key by row id so the form fully remounts … (keep the existing comment) */}
  <div
    className="px-8 pb-8"
    key={editing ? `edit-${(editing as { id?: number }).id ?? "?"}` : "new"}
  >
    {editor(editing, () => onOpenChange(false))}
  </div>
</SheetContent>
```

> Why the `data-[side=right]:` prefix on *our* override and not just `sm:max-w-3xl`: specificity.
> The base class is `data-[side=right]:sm:max-w-sm`; a plain `sm:max-w-2xl` is lower-specificity
> and loses at the `sm` breakpoint. Matching the variant lets tailwind-merge dedupe by the
> `data-[side=right]:sm:max-w` key and keep ours. (`lg:max-w-3xl` likewise needs the prefix to beat
> the base at `lg`.) `2xl`→`3xl` (672→768px on large screens) is a deliberate, modest bump — wide
> enough for the side-by-side Markdown preview to breathe, not so wide it covers the list.

The shared `Sheet` is used **only** by `section-page.tsx`, so this override is the single place
the editor width/padding is defined — no other surface is affected.

### 3.2. Render the missing `issuer` — [certifications.tsx](frontend/src/routes/_authenticated/cv/certifications.tsx)

Add an `issuer` field next to `name` (the existing `name` field is mislabelled "Title" — keep its
label as-is to avoid scope creep, or relabel to "Name"; the field that's *missing* is `issuer`).
Drop it directly under the name field:

```tsx
<form.Field name="issuer">
  {(f) => (
    <div className="space-y-1">
      <Label htmlFor={f.name}>Issuer</Label>
      <Input
        id={f.name}
        value={f.state.value}
        onChange={(e) => f.handleChange(e.target.value)}
      />
      <FieldError errors={f.state.meta.errors} />
    </div>
  )}
</form.Field>
```

### 3.3. Don't let validation fail silently — recurrence guard

Add a form-level banner that appears when a submit was attempted but the form is invalid — so a
future "required field with no input" surfaces loudly instead of no-op'ing. Put it just above the
Cancel/Create buttons in the certification form, using TanStack Form's `Subscribe`:

```tsx
<form.Subscribe selector={(s) => ({ submitted: s.submissionAttempts, canSubmit: s.canSubmit })}>
  {({ submitted, canSubmit }) =>
    submitted > 0 && !canSubmit ? (
      <p className="text-sm text-destructive">
        Some fields are invalid — check the highlighted fields above.
      </p>
    ) : null
  }
</form.Subscribe>
```

> `submissionAttempts` increments on every `handleSubmit()` even when it no-ops, so
> `submitted > 0 && !canSubmit` is exactly "you tried to save and couldn't" — the signal that was
> invisible before. Apply it in the certification editor now; if a second editor needs the same
> net, lift it into a tiny shared `<FormInvalidBanner form={form} />` then (not pre-emptively).

**Verify (all three):**

```bash
npx tsc -b      # zero output
```

In the app:
- Open any editor → the panel is wider and the form fields are padded, aligned under the title
  (not flush to the edge).
- `/cv/certifications` → New → fill **Name + Issuer** (+ optionally a date) → Create → Network
  shows `POST /api/jac/certifications/ → 201`, toast "Created", the row appears. Reload → still
  there.
- New → fill Name only, leave Issuer blank → Create → the red "Some fields are invalid" banner
  appears and the Issuer field shows its own error; no silent failure.

---

## 4. End-to-end verification — the full loop

App running, logged in as a verified user.

1. **Drawer feels like a form.** Open `/cv/jobs` → New → the Sheet is wider than before and every
   field has left/right breathing room; the Markdown preview pane on `description` isn't cramped.
2. **Certification create works.** `/cv/certifications` → New → Name "AWS SAA", Issuer "Amazon",
   Issued on a date → Create → 201, toast, row visible. Edit it → change issuer → Save → persists.
   Reload the page → both edits survive.
3. **Silent-failure guard.** New → Name only → Create → banner + field error, no toast, no POST.
4. **No regressions elsewhere.** Open the skills / jobs / projects editors → they still save
   (they were never broken; confirm the padding change didn't disturb their layout).
5. `npx tsc -b` → zero output.

All five pass → 3c is done.

---

## 5. What you should have at the end

```
frontend/src/
├── components/cv/section-page.tsx                    # wider + padded editor Sheet
└── routes/_authenticated/cv/certifications.tsx       # renders `issuer`; form-invalid banner
```

No backend change, no migration, no new dependency. Re-run the type-check, then commit code +
this guide (+ the roadmap/CLAUDE.md re-slice) together:

```bash
npx tsc -b
git add frontend/src/components/cv/section-page.tsx \
        frontend/src/routes/_authenticated/cv/certifications.tsx \
        .claude/plans/phase-3c-setup-guide.md .claude/plans/roadmap-2026-06-02.md CLAUDE.md
git commit -m "Phase 3c: roomier drawer + fix certification issuer bug"
```

---

## 6. Known gaps to revisit

- **Skill/relation editing → 3d.** Jobs/Projects still can't attach skills; the Skill editor has
  no `related_skills` / `years_of_experience_override` and a raw certification number input. That's
  the whole of 3d — don't bolt it on here.
- **The "Title" label on `name`.** The certification name field is labelled "Title"; cosmetic, left
  alone to keep 3c to its three changes. Relabel whenever the form gets its next pass.
- **Shared `FormInvalidBanner`.** Only the certification editor gets the guard now. Lift it into a
  shared component the moment a second editor needs it — not before.

---

## What's next

**3d — skill/relation editing + pickers**: a `SkillPicker` (multi-select M2M over
`/api/jac/skills/`, modelled on `domain-picker.tsx`, with an `excludeId` so a skill can't relate to
itself) and a `CertificationPicker` (single-select FK, modelled on `location-picker.tsx`); wiring
the already-declared `skills` M2M into the Job and Project editors; and adding `related_skills`
(symmetric self-M2M) + `years_of_experience_override` (with an "auto: N" hint) to the Skill editor,
replacing its raw certification number input. The backend already accepts all of it (Phase 3a).
