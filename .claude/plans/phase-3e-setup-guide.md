# Phase 3e setup guide — CV JSON export/import (backend management commands)

Goal: round-trip a user's entire CV as JSON so it can be migrated onto the deployed server. By the
end, `python manage.py cv_export --user <id>` writes the user's whole career DB to the **exact
by-name JSON shape** `cv_import` already consumes (now including `related_skills`,
`years_of_experience_override`, and `ResumeSnippet`), and `cv_import` reads it back losslessly into
a fresh user on another box — with domains and locations scoped to the *target* user instead of a
global namespace. A round-trip test pins parity.

This is **Phase 3e only** — two management commands + their test, no API, no model change, no
migration (every field already exists). It deliberately does **not**: add an HTTP export/import
endpoint (decided 2026-06-10 — the migration use case is a server-side `manage.py` run, not a UI
button), translate anything, or touch the frontend.

Run from `backend/`. If a step's "verify" check fails, stop and fix before moving on.

---

## 0. Preflight

Phase 3d committed; the suite green.

```bash
cd backend
git log --oneline -1            # expect Phase 3d (324bd31) or later
python manage.py makemigrations --check --dry-run   # "No changes detected" — 3e adds no fields
python manage.py test           # "Ran N tests … OK"
```

A latent bug you'll fix in passing: `cv_import`'s `_import_domains` / `_import_locations` create
`Domain` / `Location` rows **without a `user`** — but both models have a non-null `user` FK, so a
file containing a `domains` or `locations` section currently raises `IntegrityError`. 3e scopes them
to the target user, which also fixes this.

---

## 1. The contract — the round-trip JSON

The shape is `cv_import`'s existing schema ([cv_import.py](backend/jac/management/commands/cv_import.py)
docstring) plus three additions. All references are **by name**, resolved against earlier sections in
the same file or rows already in the DB:

```jsonc
{
  "domains":        [{"name", "description"}],
  "locations":      [{"city", "country", "street", "zip", "longitude", "latitude"}],
  "certifications": [{"name", "issuer", "issued_on", "expires_on", "credential_id", "url", "description"}],
  "skills":         [{"name", "proficiency", "category", "first_used", "domains": ["<name>"],
                      "certification": "<cert name>", "description",
                      "years_of_experience_override": 2,            // NEW — int | null
                      "related_skills": ["<skill name>", …]}],      // NEW — by-name, symmetric
  "jobs":           [{"title", "company", "location", "job_type", "started", "ended",
                      "url", "description", "skills": ["<name>"], "domains": ["<name>"]}],
  "projects":       [{"name", "location", "started", "ended", "url", "description",
                      "skills": ["<name>"], "domains": ["<name>"]}],
  "educations":     [{"institution", "location", "field_of_study", "degree", "grade",
                      "started", "ended", "description"}],
  "languages":      [{"name", "fluency", "certification": "<cert name>", "description"}],
  "resume_snippets":[{"title", "content", "kind", "is_active",      // NEW section
                      "domains": ["<name>"], "skills": ["<name>"]}]
}
```

Three things that shape the implementation:

- **`related_skills` is symmetric and forward-referencing.** A skill can relate to one defined later
  in the list, so import skills in **two passes**: create every skill first, then resolve
  `related_skills` by name and `.set()` them. (Django's `symmetrical=True` makes the reverse side
  automatic — set one direction only.) Export need only emit each pair once; re-importing the mirror
  is harmless (`.add` is idempotent).
- **Domains + locations are per-user.** `years_of_experience` is a read-only computed property — it is
  **not** exported (only the `_override` is); the importer never tries to set it.
- **`ResumeSnippet` carries no `description`** (it has `content`); its M2Ms are `domains` + `skills`.

---

## 2. Stack additions

**None.** `json`, `call_command`, and the ORM cover it.

---

## 3. Extend `cv_import` — [cv_import.py](backend/jac/management/commands/cv_import.py)

Four edits, each small:

1. **Scope domains to the user.** `_import_domains(user, items)` → `Domain.objects.get_or_create(
   user=user, name=…, defaults={"description": …})`. `_resolve_domains(user, names)` resolves via
   `Domain.objects.for_user(user)` (own + system defaults); on a miss, `get_or_create(user=user,
   name=n)` rather than raising — a fresh server may not carry the same taxonomy, and auto-creating a
   user-owned domain is the migration-friendly behaviour. Thread `user` through the skills/jobs/
   projects/snippets calls.
2. **Scope locations to the user.** `_import_locations(user, items)` and `_resolve_location(user,
   name)` filter/create with `user=user`; resolve-miss auto-creates under the user (same rationale).
3. **Skills: two-pass + new fields.** Add `years_of_experience_override=item.get(...)` to the create;
   after creating all skills, loop again and `skill.related_skills.set(self._resolve_skills(user,
   item.get("related_skills", [])))`.
4. **New `resume_snippets` section.** `_import_snippets(user, items)` creates each `ResumeSnippet`
   (title/content/kind/is_active) then `.set()`s its `domains` + `skills`. Wire it into `handle()`'s
   `counts` and into `_wipe_user_entries` (add `ResumeSnippet` to the wipe set — currently
   `ENTRY_MODELS`; snippets + `Domain`/`Location` are user-owned too, decide explicitly what
   `--replace` clears: keep it to the CvEntry subclasses + ResumeSnippet, leave Domain/Location since
   they're shared-ish, and document that).

Keep the import order: domains → locations → certifications → skills (pass 1) → jobs → projects →
educations → languages → resume_snippets → skills (pass 2: related_skills).

## 4. New `cv_export` — `backend/jac/management/commands/cv_export.py`

Mirror `cv_import`'s argument surface (`--user` / `--username` mutually exclusive; `--file` optional,
default stdout). Build the dict in the same section order and dump with `json.dumps(…, indent=2,
ensure_ascii=False, default=str)` (so `date` serialises as `YYYY-MM-DD`).

Per section, emit by-name references (not ids):

- **domains:** the distinct domains attached to any of the user's entries **∪** the user's own
  `Domain` rows — so every entry reference resolves on import. `name` + `description`.
- **locations:** the user's `Location` rows referenced by their entries. Emit `city` as the reference
  key plus the rest.
- **skills:** `proficiency`, `category`, `first_used` (str), `years_of_experience_override`,
  `certification` → cert *name*, `domains` → names, `related_skills` → names (each pair once is fine,
  but emitting both directions is harmless). **Never** emit the computed `years_of_experience`.
- **jobs / projects:** `location` → city name, `skills`/`domains` → names.
- **certifications / educations / languages / resume_snippets:** straightforward field copies;
  `languages.certification` and `snippets.domains`/`skills` → names.

> Why a command, not an endpoint: the migration is a one-shot `manage.py cv_export … > cv.json`,
> `scp`, `manage.py cv_import …` on the box — no auth surface, no multipart, no UI. An API export can
> come later if a "download my data" button is ever wanted (GDPR export, Phase 5).

**Verify (manual round-trip):**

```bash
python manage.py cv_export --user 1 --file /tmp/cv.json   # writes the file
python manage.py cv_import --username someoneelse --file /tmp/cv.json --dry-run
# the printed counts match what user 1 actually has
```

---

## 5. Test — round-trip parity

[backend/jac/tests.py](backend/jac/tests.py), after the Phase 3b block, with a `# Phase 3e` banner.
Use `django.core.management.call_command` and a `tempfile`/`io.StringIO`. Mirror the file's idioms
(`User.objects.create_user`, `setUpTestData`).

```python
class CvExportImportRoundTripTests(TestCase):
    """cv_export of user A, imported into fresh user B, reproduces the CV —
    including related_skills symmetry, the years override, and per-user domains.
    """

    @classmethod
    def setUpTestData(cls):
        cls.a = User.objects.create_user(username="rt_a", password="pass")
        cls.b = User.objects.create_user(username="rt_b", password="pass")
        d = Domain.objects.create(user=cls.a, name="Backend")
        py = Skill.objects.create(
            user=cls.a, name="Python", proficiency="expert",
            years_of_experience_override=5,
        )
        sev = Skill.objects.create(user=cls.a, name="SevDesk")
        py.related_skills.add(sev)
        py.domains.add(d)
        job = Job.objects.create(
            user=cls.a, title="Eng", company="Co", started=date(2021, 1, 1)
        )
        job.skills.add(py)
        job.domains.add(d)

    def _export_a(self) -> str:
        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        return buf.getvalue()

    def test_round_trip_into_fresh_user(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(self._export_a())
            path = fh.name
        call_command("cv_import", "--username", "rt_b", "--file", path)

        b_py = Skill.objects.get(user=self.b, name="Python")
        self.assertEqual(b_py.years_of_experience_override, 5)
        self.assertEqual(b_py.proficiency, "expert")
        # symmetric relation survived (by name)
        self.assertIn("SevDesk", b_py.related_skills.values_list("name", flat=True))
        # domains were created under B, not cross-linked to A
        b_domain = Domain.objects.get(user=self.b, name="Backend")
        self.assertIn(b_domain, b_py.domains.all())
        self.assertFalse(Domain.objects.filter(user=self.a, name="Backend").count() > 1)
        b_job = Job.objects.get(user=self.b, title="Eng")
        self.assertIn(b_py, b_job.skills.all())
```

(Add a small `resume_snippets` + `certification`-reference case too if you want the section coverage;
the skeleton above is the load-bearing assertion set.) Imports needed at the top of the file:
`import io`, `import tempfile`, `from django.core.management import call_command`.

**Verify:**

```bash
python manage.py test jac.tests.CvExportImportRoundTripTests
python manage.py test          # full suite, count above the 3d total, all OK
```

---

## 6. End-to-end verification — the full loop

1. **Real export.** `python manage.py cv_export --user <you> --file /tmp/cv.json` → open it: by-name
   references everywhere, no raw ids, dates as `YYYY-MM-DD`, your snippets present, no
   `years_of_experience` (only the override).
2. **Dry-run import elsewhere.** `cv_import --username <other> --file /tmp/cv.json --dry-run` → counts
   match; nothing written.
3. **Real import + spot check.** Drop the dry-run → log in as the other user in the SPA → `/cv/skills`,
   `/cv/jobs` show the migrated rows; a skill's related skills + override survived; domains belong to
   the importing user.
4. **Suite.** `python manage.py test` green.

All four pass → 3e is done.

---

## 7. What you should have at the end

```
backend/jac/management/commands/
├── cv_export.py     # new — dumps a user's CV to the by-name JSON cv_import reads
└── cv_import.py     # per-user domains/locations; related_skills (2-pass) + override; resume_snippets
backend/jac/tests.py # CvExportImportRoundTripTests
```

No migration. Re-run the suite, then commit code + this guide + the CLAUDE.md command-table update:

```bash
python manage.py test
git add backend/jac/management/commands/cv_export.py \
        backend/jac/management/commands/cv_import.py \
        backend/jac/tests.py .claude/plans/phase-3e-setup-guide.md CLAUDE.md
git commit -m "Phase 3e: cv_export + import round-trip (related_skills, override, snippets)"
```

(Add `cv_export` to the CLAUDE.md Common Commands + a Shipped bullet before committing.)

---

## 8. Known gaps to revisit

- **HTTP export/import endpoint (later / Phase 5 GDPR).** A "download my data" / "import" button is
  out of scope; the command pair covers migration.
- **`--replace` scope.** It clears the CvEntry subclasses (+ ResumeSnippet); shared-ish Domain/Location
  rows are left. Documented in the command help; revisit if a full per-user wipe is ever needed.
- **Off-page references.** N/A here (the command reads the whole DB, not a paginated API).

---

## What's next

**3f — JAC-core validation gate**: with the editors complete (3c/3d) and data portability in place
(3e), author/import a full personal CV and run `cv_test` + `ai_tailor_with_fallback` across several
real postings, logging findings in `.claude/plans/phase-3f-jac-core-findings.md` — the go/no-go on
whether the JAC core needs a fix sub-phase before Phase 4.
