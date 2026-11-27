# Reviewing and improving the career

Use this when the user wants their career data checked or improved: thin descriptions, missing
details, skills that should be linked, anything that makes their CVs weaker than they could be.
jac prints every entry as the database holds it, so an improvement here improves every future CV,
and the public portfolio, which renders from the same entries. When the user hands you their CV to
put into jac, start with "Filling the career from a CV" below.

## The flow

1. **Read both:** `get_career` and the review these rules came with (`get_career_review`). The
   review is jac's machine pass: each finding has `ids` (the entries it's about, empty for the
   career as a whole), a `check`, a `level` (`fix`, then `improve`, then `consider`) and one
   sentence. Treat it as a checklist, not the verdict. Check each finding against the career, drop
   the ones that don't hold (a skill name matched by accident), and add what a machine pass can't
   see (below). Some entries it skips on purpose, and so do you: a study that ended without a
   degree (`completed: false`) gets no description or skills finding, since a CV prints it as
   one line; a school-leaving certificate (`degree_level` `secondary`) gets no skills finding.
2. **Report.** Open with a short verdict, two or three sentences: what's strong, and what holds the
   CVs back most. Then the findings grouped by theme, most costly first:
   1. data errors;
   2. thin or missing descriptions;
   3. links;
   4. skills;
   5. the timeline;
   6. the rest.

   Name entries by their title, with the id in brackets. If there are many findings, give the top
   ten and ask whether to go on.
3. **Ask for what only the user knows.** Missing details are facts: what they built, for whom, with
   what, how big, and what came of it. Ask concrete questions per entry, a few at a time: "At
   Acme (job:3): what did the team build, what was your part, and is there a number for it, such
   as users, speed, money or time saved?"
4. **Propose a batch.** Show each change as before → after: the full new text of a description,
   and what's added and removed for links. Give one line on why. Five to fifteen changes keep a
   batch reviewable.
5. **Apply only after the user says yes to that batch.** First check it (`apply_career_changes`
   with `dry_run: true`); fix what it refuses; then apply it (`apply_career_changes`). The check
   names a new entry `new:<ref>`, as the batch does; the real apply answers with its id. Tell the
   user what changed and that every batch can be undone, with `undo_career_changes` here or in
   jac (Account → LLM, under Connectors), until they keep it in jac. A kept batch is gone from
   `list_career_changes` and can't be undone.
6. Go on with the next theme, or stop when the user has had enough.

## Filling the career from a CV

When the user gives you their CV (a file, pasted text, a LinkedIn profile) to put into jac:

1. **Read `get_career` first.** Create only what isn't there yet. An entry jac already has gets an
   edit (a change with its `id`), never a second copy.
2. **Map the CV, then show the map.** By section, one line per entry: jobs, education, projects,
   certifications, languages, then the skills and which entries each one links. Name what you
   couldn't place and what the CV leaves open. What fits no type (hobbies, awards, publications)
   stays out unless the user says where it goes.
3. **Take only what the CV or the user says.** No entry to fill a gap, no skill the CV doesn't
   name, no number, level or date it doesn't give.
   - **Dates** as `YYYY-MM-DD`: a month and a year are the 1st of that month. For a year alone,
     ask for the month, or use January and say which dates you set that way. "Present" or "heute"
     leaves `ended` out.
   - **Jobs:** `job_type` by what the CV says (an internship is `in`, volunteering `vl`), else
     `ft`.
   - **Education:** `degree_level` by what it earned, so a study still running or left without a
     degree is `none`, with the degree it aims at in `degree`. A school-leaving certificate is
     `secondary`, an apprenticeship (Ausbildung) `vocational`.
   - **Languages:** `fluency` from the CV's word or level: a mother tongue is `native`;
     fließend, verhandlungssicher or C2 `fluent`; C1 `professional`; B1 or B2 `conversational`;
     A1 or A2 `basic`. Say which ones you mapped.
   - **Skills:** one per tool, method or field the CV names, linked to the entries that used it.
     A `proficiency` only when the CV gives one; jac's default is `intermediate`, so say so.
   - **Descriptions:** the CV's own wording and language, one `- ` line per bullet. Making them
     stronger is the review, afterwards.
   - **Favourites** stay off. Offer them at the end.
4. **Apply after the user's yes**, as in the flow: a dry run, then the batch. Here a batch can be
   a whole section or the whole CV, since the user checks the map, not each field. A batch takes
   at most 100 changes. For a longer CV, put each skill in the batch that first links it, and link
   it by the id the answer gave (`created`) after that.
5. **Then say what's left to the user in jac:** each entry's place (`location`), their contact
   details and photo (Account → Profile), and anything you set by guess. Offer a review
   (`get_career_review`) as the next step: it finds the thin descriptions and missing links.

## What to look for beyond the machine review

- **Descriptions** that:
  - list duties ("responsible for …") instead of results;
  - have no numbers where numbers exist;
  - claim buzzwords without evidence;
  - repeat the same sentence across entries;
  - never say what the company or the product was.
- **Tools and methods a text names that aren't skills yet** (Docker in three descriptions, but no
  Docker skill): propose the skill and link it.
- **Links a machine pass misses:**
  - a skill an entry clearly used, under another name ("Postgres" in the text, the skill is
    "PostgreSQL");
  - a project that was part of a job (the project's `job`);
  - a skill that comes from a certification (the certification's `skills`).
- **Skill hygiene:**
  - near-duplicates;
  - a level the record doesn't support. A skill's `years` count the time the jobs, projects
    and educations that link it ran, each from start to end (or today), overlaps once. Nothing
    before `first_used` counts, and the time from `first_used` to the first linked entry does.
    0 means under a year. When `years_override` is set, `years` is the number the user typed in
    jac: ask before calling it wrong;
  - soft skills claimed but backed by nothing;
  - the wrong category (a tool filed as `domain`).
- **Consistency:**
  - the entries' language: all German, all English, or a mix. Point out a mix and ask; don't
    translate unasked;
  - tense: past for jobs that ended, present for current ones.
- **Favourites:** what deserves to be one. Favourites get a nudge in every selection. The caps
  are 4 jobs, 3 projects, 2 educations, 3 certifications, 10 skills and 3 languages.

## Rules for changes

- **Invent nothing.** Rewording, tightening and restructuring what's there is fine. A new fact (a
  number, a tool, a result, a date) goes in only when the user said it in this conversation. If a
  description needs a fact you don't have, ask, or leave it out and say so.
- **Keep the entry's language** unless the user asks otherwise, and keep their voice
  (`writing_sample` in the career shows it).
- **Descriptions are markdown-lite.** One item per line, and a line starting with `- ` prints as a
  bullet. `**bold**` is stripped; nothing else is rendered. Two to five bullets suit a job. When
  a page runs full, jac drops the descriptions of the lowest-ranked entries first. So the first
  line matters most.
- **What you may change:** the fields `get_career` shows, except the read-only ones (see the batch
  format below).
- **What you may create:** any entry, from what the user gave you: their CV, or what they said
  in this conversation. Never one of your own to round off the career: a `gap` finding is a
  question for the user, not a job to write. A tool a description names may become a skill.
- **You can't delete.** For a duplicate, move its links to the one that stays and tell the user
  to delete the other in jac.
- **Which links count.** These change CVs:
  - entry → skill: `skills` on jobs, projects, educations and certifications. They feed an
    entry's ranking against a posting. On jobs, projects and educations they also give the
    skill its years of use: a certification is a date, not a span, and adds none;
  - project → job;
  - language → certification.

  Skill-to-skill links (`related_skills`, `builds_on`) are the user's own map in jac's skills
  page. jac's selection doesn't read them, so offer them as a tidy-up, not as an improvement to
  the CV.

## The batch

Pass the batch to `apply_career_changes` as `changes`:

```json
{
  "changes": [
    {
      "id": "job:3",
      "seen": "2026-11-02T09:14:03.512345+00:00",
      "why": "Says what was built and for whom; links Docker, which it used.",
      "set": { "description": "- Built …\n- Ran …" },
      "add": { "skills": ["skill:12", "new:docker"] },
      "remove": { "skills": ["skill:4"] }
    },
    {
      "new": "skill",
      "ref": "docker",
      "why": "Named in three job descriptions, missing as a skill.",
      "set": { "name": "Docker", "category": "technical", "proficiency": "intermediate" },
      "add": { "related_skills": ["skill:7"] }
    },
    {
      "new": "job",
      "ref": "beispiel",
      "why": "From the CV.",
      "set": {
        "title": "Backend Developer",
        "company": "Beispiel GmbH",
        "started": "2019-03-01",
        "ended": "2021-05-01",
        "description": "- Built …"
      },
      "add": { "skills": ["new:docker"] }
    },
    {
      "new": "project",
      "ref": "billing",
      "why": "From the CV: part of the job at Beispiel.",
      "set": { "name": "Billing platform", "job": "new:beispiel" }
    }
  ]
}
```

- **`id` + `seen`:** the entry, and its `updated_at` exactly as `get_career` shows it.
  - If the entry changed in jac since, the whole batch is refused, naming the ids. Call
    `get_career` again and redo those changes from the fresh text.
  - An applied batch gives the entries it touched a new `updated_at`. Call `get_career` again
    before changing one of them a second time.
- **`set`:** the fields to change, by the names `get_career` shows:
  - dates as `YYYY-MM-DD`;
  - `degree_level` by name (`bachelor`, `master`, …);
  - `job_type` by code (`ft`, `pt`, `ct`, `fl`, `in`, `vl`);
  - a single link as an id, `new:<ref>` or `null`: `"job": "job:3"` on a project,
    `"certification": "certification:2"` on a language;
  - read-only, so never in a change: `id`, `updated_at`, `location` (the user sets it in jac),
    a job's `projects` (set `job` on the project instead), an education's `completed` (it
    follows from `degree_level`) and a skill's `years` and `years_override` (see skill
    hygiene above). jac refuses any other name, and its answer lists the ones the entry takes.
- **`add` / `remove`:** lists.
  - `skills`, `related_skills` and `builds_on` take skill ids, or `new:<ref>` for a skill this
    batch creates;
  - `domains` takes names, from the `domains` list in the career (jac doesn't create domains).
    A misspelt or duplicate domain is the user's to rename or merge in jac (Manage domains, in
    the domain filter on the CV pages); meanwhile a batch can re-tag entries.
- **`why`:** one line. The user sees it in jac's change log.
- **`new`** with an entry type (`job`, `project`, `education`, `certification`, `skill` or
  `language`) and a `ref` that's unique in the batch creates that entry from its `set` and links
  its `add`; it takes no `id` or `seen`. `set` needs what the type requires: a job's `title`,
  `company` and `started`, an education's `institution` and `started`, a certification's `name`
  and `issuer`, and the `name` of a project, a skill or a language. Other changes link it as
  `new:<ref>` where they link that type: a skill in `skills`, `related_skills` or `builds_on`, a
  job as a project's `job`, a certification as a language's `certification`. The order in the
  batch doesn't matter. The answer maps each `new:<ref>` to the new entry's id.
- At most one change per entry in a batch, and at most 100 changes.

The answer lists every change with its `before` and `after`. A refusal names what's wrong, per
change, by its position in the list (from 0); then nothing was applied.

**Undo:** `list_career_changes` lists the batches, and `undo_career_changes` puts a batch's
entries back and deletes the entries it created. The user can do the same in jac (Account → LLM,
under Connectors → Career changes). Either way it's refused once an entry in the batch has been
edited again since. In jac the user can also keep a batch: its edits stay, it's gone from
`list_career_changes`, and it can't be undone any more. When `undo_career_changes` finds no
such batch, it was kept or is too old: say so, and offer a new batch that changes the entries
back.
