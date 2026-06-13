# Phase 3d setup guide — skill/relation editing + pickers (frontend)

Goal: finish wiring the career model into the editors so a CV can actually be made *complete*.
By the end you can attach **skills** to jobs and projects (the `skills` M2M was declared in both
schemas but never rendered), link a skill to its **related skills** (the symmetric self-M2M shipped
in 3a), set a manual **years-of-experience override** on a skill (so C/C++ stops reading 16 years),
and pick a skill's **certification** from a searchable combobox instead of typing a raw database id.
Two new reusable pickers (`SkillPicker`, `CertificationPicker`) carry all of it.

This is **Phase 3d only** — pure frontend. The backend already accepts every field (3a made
`SkillSerializer.related_skills` + `years_of_experience_override` writable and user-scoped;
`JobSerializer.skills` / `ProjectSerializer.skills` have always been writable). It deliberately does
**not**: add inline skill/certification creation from the pickers (a skill needs
name+proficiency+category, a cert needs name+issuer — both more than a combobox should ask; create
on their own pages), build a ResumeSnippet editor (deferred to Phase 6/7), or touch the backend.

Run from `frontend/`. If a step's "verify" check fails, stop and fix before moving on.

---

## 0. Preflight

Phase 3c committed and green.

```bash
cd frontend
git log --oneline -1     # expect "Phase 3c: roomier drawer + fix certification issuer bug" (489e43a) or later
npx tsc -b               # zero output
```

Confirm the gaps you're about to close: open `/cv/jobs` → New → there's **no** way to attach a
skill. Open `/cv/skills` → New → "Certification" is a bare number input, and there's no
"related skills" or "years override" control. Those three absences are the phase.

---

## 1. The contract you're coding against

All three field shapes are already in the live serializers — pin them at `/api/docs`:

```jsonc
// Skill — related_skills (writable, user-scoped, symmetric), years_of_experience_override (writable),
// years_of_experience (read-only; returns the override when set, else the computed delta)
PATCH /api/jac/skills/12/
{ "related_skills": [3, 7], "years_of_experience_override": 2, "certification": 5 }

// Job / Project — skills M2M (writable, user-scoped)
PATCH /api/jac/jobs/4/
{ "skills": [12, 19] }
```

Two non-obvious backend facts that shape the UI:

- **`related_skills` is symmetric** (`ManyToManyField("self")`, Django default `symmetrical=True`).
  Set `[B]` on A and after a refetch B lists A automatically — the editor only ever edits *one*
  side. Self-reference is rejected server-side (`SkillSerializer.validate_related_skills`), so the
  picker hides the row's own id (`excludeId`) rather than relying on the 400.
- **`years_of_experience` already collapses override-or-computed**, so the serializer can't show the
  raw auto value once an override is set. The editor's hint reads the *effective* `years_of_experience`
  (honest: "currently Ny"); surfacing the pre-override computed value alongside needs a tiny extra
  read-only serializer field — logged as a gap, not built here.

`SkillRow` ([frontend/src/lib/queries/jac.ts](frontend/src/lib/queries/jac.ts)) gains
`related_skills: number[]` and `years_of_experience_override: number | null`.

---

## 2. Stack additions

**None.** The pickers reuse the existing shadcn `Command` + `Popover` primitives and the `useList`
query factory — exactly what `DomainPicker` / `LocationPicker` already use.

---

## 3. Shared infra — the two pickers

Both model directly on the existing pickers, so behaviour (search, badges, scoping via `useList`)
stays consistent.

### 3.1. `SkillPicker` — [frontend/src/components/cv/skill-picker.tsx](frontend/src/components/cv/skill-picker.tsx)

Multi-select M2M, modelled on [domain-picker.tsx](frontend/src/components/cv/domain-picker.tsx):
removable Badges for the selection, a `Command` popover for adding. Returns `number[]`. Two
deliberate differences from `DomainPicker`:

- **No inline create.** `DomainPicker` can mint a domain from just a name; a skill can't be created
  from a combobox (it needs proficiency + category). Drop the "Create …" `CommandItem` entirely.
- **`excludeId?: number`.** Filters one skill out of the options so a skill can't relate to itself.
- **Selected-badge resolution.** The selection's names come from an *unsearched* `useList("skills", {})`
  page (not the search-filtered options list) so a selected skill's badge doesn't vanish when you
  type a search that doesn't match it. (`DomainPicker` has the smaller-list version of this edge; the
  two-query split is the correctness upgrade here.)

### 3.2. `CertificationPicker` — [frontend/src/components/cv/certification-picker.tsx](frontend/src/components/cv/certification-picker.tsx)

Single-select FK, modelled on [location-picker.tsx](frontend/src/components/cv/location-picker.tsx):
a full-width trigger button showing the current pick, a `Command` popover with a "Clear" item and the
options. Returns `number | null`. Label each row `"<name> — <issuer>"`. Same no-inline-create rule
(a cert needs name+issuer); resolve the current selection from an unsearched base list so it shows
even mid-search.

**Verify:** `npx tsc -b` clean after both files exist (they're not imported yet — the import wiring is §4–5).

---

## 4. Worked example — the Job editor gets `skills`

[frontend/src/routes/_authenticated/cv/jobs.tsx](frontend/src/routes/_authenticated/cv/jobs.tsx).
The `skills` field already lives in the Zod schema and the initial values (both branches) — only the
control is missing. Import `SkillPicker` and drop a field in right after `domains`:

```tsx
<form.Field name="skills">
  {(f) => (
    <div className="space-y-1">
      <Label>Skills</Label>
      <SkillPicker value={f.state.value} onChange={f.handleChange} />
    </div>
  )}
</form.Field>
```

**Verify:** `/cv/jobs` → edit a job → add 2 skills → Save → Network shows `PATCH …/jobs/<id>/` with
`skills:[…]` in the body → reopen the row → the badges are still there.

### 4.1. Project editor — the same, one line of import + one field

[projects.tsx](frontend/src/routes/_authenticated/cv/projects.tsx) is identical: `skills` is already
in the schema/initials; add the import and the same `<form.Field name="skills">` block after
`domains`. Verify the same way on `/cv/projects`.

---

## 5. The Skill editor — three additions

[skills.tsx](frontend/src/routes/_authenticated/cv/skills.tsx). Import `SkillPicker` +
`CertificationPicker`, extend the schema and **both** initial-value branches, then:

1. **`years_of_experience_override`** — schema `z.number().int().min(0).nullable()`; render a number
   input (placeholder `"auto"`, empty string → `null`) beside `first_used`, with a muted hint:
   "Leave blank to auto-compute from first use / job history (currently Ny)." reading
   `row?.years_of_experience`.
2. **`certification`** — replace the raw `<Input type="number">` with `<CertificationPicker>`.
3. **`related_skills`** — schema `z.array(z.number())`; render `<SkillPicker excludeId={row?.id}>`
   after `domains`.

The existing `onSubmit` already spreads `value` into the body, so the three new fields ride along
with no change to the submit handler.

**Verify:** `/cv/skills` → edit a skill → set override `2`, pick a certification, add a related skill →
Save → reopen → all three persist. Open the *related* skill → the first skill appears in **its**
Related skills (symmetry). Clear the override → the hint's "currently Ny" reflects the computed value
again.

---

## 6. End-to-end verification — the full loop

App running, logged in; have a handful of skills + at least one certification already created.

1. **Job ↔ skills.** `/cv/jobs` → edit → add 2 skills → Save → reopen → persisted; reload the page →
   still persisted.
2. **Project ↔ skills.** Same on `/cv/projects`.
3. **Skill certification.** `/cv/skills` → edit → pick a certification from the combobox (search works,
   "Clear" removes it) → Save → reopen → the right cert shows as `name — issuer`.
4. **Years override.** Set override to a number → the skills list/cell reflects it; clear it → falls
   back to the auto value.
5. **Related-skill symmetry.** Relate A→B on A; open B → A is listed. Remove it on B; open A → gone.
   A cannot list itself (it's not in its own options).
6. `npx tsc -b` → zero output.

All six pass → 3d is done.

---

## 7. What you should have at the end

```
frontend/src/
├── components/cv/skill-picker.tsx            # new — multi-select M2M, excludeId, no inline create
├── components/cv/certification-picker.tsx     # new — single-select FK, name — issuer
├── lib/queries/jac.ts                         # SkillRow += related_skills, years_of_experience_override
└── routes/_authenticated/cv/
    ├── jobs.tsx        # SkillPicker wired to the skills M2M
    ├── projects.tsx    # SkillPicker wired to the skills M2M
    └── skills.tsx      # related_skills + years override + CertificationPicker
```

No backend change, no migration. Re-run the type-check, then commit code + this guide:

```bash
npx tsc -b
git add frontend/src/components/cv/skill-picker.tsx \
        frontend/src/components/cv/certification-picker.tsx \
        frontend/src/lib/queries/jac.ts \
        frontend/src/routes/_authenticated/cv/jobs.tsx \
        frontend/src/routes/_authenticated/cv/projects.tsx \
        frontend/src/routes/_authenticated/cv/skills.tsx \
        .claude/plans/phase-3d-setup-guide.md CLAUDE.md
git commit -m "Phase 3d: SkillPicker + CertificationPicker, skills/relations/override in editors"
```

(Add `skill-picker.tsx` / `certification-picker.tsx` to the CLAUDE.md Frontend components row and a
Shipped bullet before committing.)

---

## 8. Known gaps to revisit

- **Inline skill / certification create from the pickers (next frontend slice).** Both pickers are
  select-existing only. A tiny inline mini-form (skill: name+category+proficiency; cert: name+issuer)
  is the natural follow-up — out of this slice.
- **"auto: N — override" dual display (small backend follow-up).** The editor hint shows the
  *effective* `years_of_experience`; to show the computed value *and* the override side by side, add a
  read-only computed-only field to `SkillSerializer` (e.g. `years_of_experience_computed`). Fold into
  3g hardening or a focused follow-up.
- **Selected items beyond page 1.** Badge/current resolution reads the first unsearched page (size 50);
  a user with >50 skills/certs could have a selected item off-page not resolve its name. Acceptable
  now; revisit if anyone hits it.
- **Pagination across the full skill list in the picker.** The dropdown shows the first page of search
  results; deep catalogues rely on search to narrow. Fine for personal-scale data.

---

## What's next

**3e — CV JSON export/import (backend management commands)**: a new `cv_export` that dumps a user's
whole CV to the exact by-name JSON shape `cv_import` consumes (now including `related_skills`,
`years_of_experience_override`, and `ResumeSnippet`), and an extended, per-user-scoped `cv_import` —
so the CV you can now author completely can be migrated onto the deployed server, with a round-trip
test pinning parity.
