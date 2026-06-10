# Phase 3a-bis setup guide — career-model relation completion

A late addition to the **3a career-model evolution**, slotted in now (before the 3f validation gate)
because the modelling gaps are structural and far cheaper to close *before* a bulk CV-entry pass than
to retrofit after. Decided live with the user: capture the relations now, wire the pipeline to consume
them later (when the tool is revisited with real data).

## 1. Goal

Close the unmodeled relations between career entries so a CV can be authored *completely* and
*consistently* in the editor before bulk data entry:

1. **`Skill.builds_on`** — a **directed** prerequisite edge (`symmetrical=False`, reverse `enables`),
   distinct from the existing symmetric `Skill.related_skills`. `DRF builds_on {Django, Python}`;
   knowing DRF implies the prerequisites, not vice-versa. `related_skills` stays for undirected
   sibling clusters (Cubase ↔ Logic, bass ↔ guitar ↔ piano, HTML ↔ CSS ↔ JS).
2. **`Education.skills` + `Education.domains`** — Education previously carried only `location`, a
   filtering blind spot. Now a first-class filterable/taggable entry like Job/Project.
3. **`Certification.skills` + `Certification.domains`** — a cert as *evidence* for the skills/domains
   it proves (reverse `skill.certifications`, named to avoid clashing with the existing
   `Skill.certification` FK pointing the other way).
4. **`Project.job`** (nullable FK, reverse `job.projects`) — "built X while at company Y."
5. **`ResumeSnippet.job` / `ResumeSnippet.project`** (nullable FKs, reverse `resume_snippets`) —
   attribute a hand-written achievement to its source entry. Backend/admin/round-trip only (no
   snippet editor exists in the frontend yet).

Everything round-trips through `cv_export`/`cv_import` (references by name; a project/snippet names its
source job by **title**, a snippet names its project by **name**).

**Explicitly *not* in this slice:**

- **Pipeline consumption.** The deterministic filter doesn't traverse `related_skills` today and
  doesn't traverse any of these new edges either ([jac/cv.py](../../backend/jac/cv.py)
  `deterministic_filter` matches a skill by name/description/domains, reaching it via a *job's*
  `skills` M2M). Upward inference ("posting wants DRF → also surface Django + Python") is the real
  payoff but is a pipeline change for the 3f validation gate. Logged as a gap.
- **Cycle detection** on `builds_on` (A→B→A). Only direct self-reference is rejected.
- **A frontend `ResumeSnippet` editor.** Snippet source links are admin/import only for now.

## 2. What shipped — file by file

### Backend

- **[models.py](../../backend/jac/models.py)** — the five additions above. `Certification.skills`
  needs `related_name="certifications"` (reverse-name clash with `Skill.certification`); the rest use
  Django defaults. Migration `0009_certification_domains_certification_skills_and_more`.
- **[serializers.py](../../backend/jac/serializers.py)**
  - `SkillSerializer`: `builds_on` writable + user-scoped (added to `user_scoped_fields`) + a
    `validate_builds_on` self-reference guard mirroring `validate_related_skills`; `enables`
    read-only.
  - `EducationSerializer` + `CertificationSerializer`: switched base to `ScopeDomainsToUserMixin`,
    added scoped `skills` (user) + `domains` (domain-scoped).
  - `ProjectSerializer`: scoped `job` FK. `ResumeSnippetSerializer`: scoped `job` + `project` FKs.
  - Scoping is automatic via [lukehirsch/mixin.py](../../backend/lukehirsch/mixin.py)
    `ScopeRelatedToUserMixin` (handles `many=True` via `child_relation`) — no per-field code.
- **[admin.py](../../backend/jac/admin.py)** — `filter_horizontal` for the new M2Ms;
  `autocomplete_fields` for the new FKs (relies on the target admins' `search_fields`).
- **[cv_export.py](../../backend/jac/management/commands/cv_export.py)** — emit `builds_on`,
  education/cert `skills`+`domains`, project/snippet source `job` (by title), snippet `project` (by
  name). `enables` is **not** exported (it's the inverse of other rows' `builds_on`).
- **[cv_import.py](../../backend/jac/management/commands/cv_import.py)** — resolve all of the above by
  name. **Ordering wrinkle:** certs import *before* skills, so `Certification.skills` can't resolve
  yet at cert-creation time — it's wired in a deferred `_wire_certification_skills` pass called right
  after `_import_skills`. `builds_on` rides the existing skills second pass. New strict resolvers
  `_resolve_job` (by title) / `_resolve_project` (by name).

### Frontend

- **[lib/queries/jac.ts](../../frontend/src/lib/queries/jac.ts)** — row types extended:
  `SkillRow += builds_on, enables`; `EducationRow`/`CertificationRow += skills, domains`;
  `ProjectRow += job`.
- **[components/cv/job-picker.tsx](../../frontend/src/components/cv/job-picker.tsx)** — new single-FK
  picker over jobs (labelled `title — company`), modelled on `CertificationPicker`. The existing
  `SkillPicker` (multi, `excludeId`) and `DomainPicker` (multi, inline-create) are reused as-is.
- **Editors:** `skills.tsx` (+ "Builds on" `SkillPicker`, beside the existing "Related skills"),
  `education.tsx` (+ Domains + Skills), `certifications.tsx` (+ Domains + "Skills it evidences"),
  `projects.tsx` (+ "Done at (job)" `JobPicker`). Each extends the Zod schema + **both** initial
  branches; the existing spread-`value` submit handlers carry the new fields with no change.

## 3. Verification

Backend:

```bash
cd backend
python manage.py check                  # 0 issues
python manage.py test jac               # Ran 113 tests … OK
```

New tests (in [tests.py](../../backend/jac/tests.py)): `SkillBuildsOnAPITests` (directionality,
self-reject, cross-user scoping, `enables` read-only) + round-trip assertions
(`test_builds_on_direction_survives`, `test_education_certification_project_relations_survive`) folded
into `CvExportImportRoundTripTests` (its fixture now carries a DRF→Django→Python chain, education
skills/domains, a job-linked project, and a job/project-linked snippet).

Frontend:

```bash
cd frontend && npx tsc -b               # zero output
npm run lint                            # 0 errors (pre-existing fast-refresh warnings only)
```

Manual loop (app running, logged in, with a few skills + ≥1 job/cert):

1. `/cv/skills` → edit DRF → **Builds on** += Django, Python → Save → reopen → persists. Open Python
   → its Builds on is empty (asymmetry); `GET /api/jac/skills/<python>/` → `enables` lists them.
2. `/cv/education` → edit → add Domains + Skills → Save → reopen → persist.
3. `/cv/certifications` → edit → add Domains + "Skills it evidences" → Save → reopen → persist.
4. `/cv/projects` → edit → **Done at (job)** → pick a job (search + Clear work) → Save → reopen →
   persists.
5. Round-trip: `cv_export --user <id> --file /tmp/cv.json` shows the new keys; `cv_import … --replace`
   into a fresh user reproduces every edge with direction intact.

## 4. Commit

```bash
git add backend/jac/ frontend/src/lib/queries/jac.ts \
        frontend/src/components/cv/job-picker.tsx \
        frontend/src/routes/_authenticated/cv/{skills,education,certifications,projects}.tsx \
        .claude/plans/phase-3a-bis-setup-guide.md CLAUDE.md
git commit -m "Phase 3a-bis: career-model relation completion (builds_on + entry cross-links)"
```

## 5. Known gaps to revisit

- **Pipeline upward inference (3f).** Wire `builds_on` (and `related_skills`) into tailoring so a leaf
  match surfaces prerequisites at a discounted relevance. The first experiment for the validation gate.
- **Cycle detection** on `builds_on`.
- **Job-title ambiguity in export/import.** A project/snippet names its source job by title; duplicate
  titles for one user resolve to the first match. Fine at personal scale.
- **ResumeSnippet editor.** Snippet source links (`job`/`project`) are admin/import-only until a
  snippet editor lands (Phase 6/7).
- **`years_of_experience` from prerequisites.** A prerequisite is ≥ as old as anything building on it;
  could tighten the computed property. Out of scope.

## 6. What's next

Back to the planned order: **3f — JAC-core validation gate** (dogfood `cv_test` /
`ai_tailor_with_fallback` over real postings; Phase 4 go/no-go). The prerequisite-inference gap is the
natural first experiment to run there.
