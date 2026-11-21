# Reviewing and improving the career

Use this when the user wants their career data checked or improved: thin descriptions, missing
details, skills that should be linked, anything that makes their CVs weaker than they could be.
jac prints every entry as the database holds it, so an improvement here improves every future CV,
and the public portfolio, which renders from the same entries.

## The flow

1. **Read both:** `career` and `review` (with the connector: `get_career` and
   `get_career_review`). `review` is jac's machine pass: each finding has `ids`
   (the entries it's about, empty for the career as a whole), a `check`, a `level` (`fix`, then
   `improve`, then `consider`) and one sentence. Treat it as a checklist, not the verdict. Check
   each finding against the career, drop the ones that don't hold (a skill name matched by
   accident), and add what a script can't see (below). In file mode both come from the career
   file the user attached: name the date it was taken, once.
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
5. **Apply only after the user says yes to that batch.**
   - **In file mode**, `apply FILE` checks the batch against the career file (ids, `seen`, links,
     the batch size) and hands the file over; fix what it refuses and run it again. Tell the user:
     in jac, Account → Claude → Import changes, pick the file. jac shows the batch there with
     before and after, and it can be undone in the same place.
   - **With the connector, and online**, first check the batch (`apply_career_changes` with
     `dry_run: true`, or `apply FILE --check`); fix what it refuses; then apply it
     (`apply_career_changes`, or `apply FILE`). The ids the check shows for new skills are
     placeholders: keep linking them as `new:<ref>` until the real apply answers with their ids.
     Tell the user what changed and that every batch can be undone, with `undo_career_changes`
     (`undo ID`) here or in jac under Account → Claude.
6. Go on with the next theme, or stop when the user has had enough.

## What to look for beyond the machine review

- **Descriptions** that:
  - list duties ("responsible for …") instead of results;
  - have no numbers where numbers exist;
  - claim buzzwords without evidence;
  - repeat the same sentence across entries;
  - never say what the company or the product was.
- **Tools and methods a text names that aren't skills yet** (Docker in three descriptions, but no
  Docker skill): propose the skill and link it.
- **Links a script misses:**
  - a skill an entry clearly used, under another name ("Postgres" in the text, the skill is
    "PostgreSQL");
  - a project that was part of a job (the project's `job`);
  - a skill that comes from a certification (the skill's `certification`).
- **Skill hygiene:**
  - near-duplicates;
  - a level the record doesn't support;
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
  (`writing_sample` in `career` shows it).
- **Descriptions are markdown-lite.** One item per line, and a line starting with `- ` prints as a
  bullet. `**bold**` is stripped; nothing else is rendered. Two to five bullets suit a job. When
  a page runs full, jac drops the descriptions of the lowest-ranked entries first. So the first
  line matters most.
- **What you may change:** the fields `career` shows, except the read-only ones (see the file
  format below). **You may create skills, nothing else:** a new job, project, degree,
  certification or language is the user's to add in jac. **You can't delete.** For a duplicate,
  move its links to the one that stays and tell the user to delete the other in jac.
- **Which links count.** These change CVs:
  - entry → skill: `skills` on jobs, projects, educations and certifications. They feed an
    entry's ranking against a posting and the skill's years of use;
  - project → job;
  - skill or language → certification.

  Skill-to-skill links (`related_skills`, `builds_on`) are the user's own map in jac's skills
  page. jac's selection doesn't read them, so offer them as a tidy-up, not as an improvement to
  the CV.

## The changes file

With the connector, pass the batch to `apply_career_changes` as `changes`. Otherwise write it
to a file, e.g. `career-changes.json`:

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
    }
  ]
}
```

- **`id` + `seen`:** the entry, and its `updated_at` exactly as `career` shows it.
  - If the entry changed in jac since, the whole batch is refused (409, naming the ids). Run
    `career` again (in file mode: from a fresh career file) and redo those changes from the
    fresh text.
  - An applied batch gives the entries it touched a new `updated_at`. Run `career` again before
    changing one of them a second time; in file mode that takes a fresh career file (above).
- **`set`:** the fields to change, by the names `career` shows:
  - dates as `YYYY-MM-DD`;
  - `degree_level` by name (`bachelor`, `master`, …);
  - `job_type` by code (`ft`, `pt`, `ct`, `fl`, `in`, `vl`);
  - a single link as an id or `null`: `"job": "job:3"` on a project, `"certification":
    "certification:2"` on a skill or language;
  - read-only, so never in a change: `id`, `updated_at`, `location` (the user sets it in jac),
    a job's `projects` (set `job` on the project instead), an education's `completed` (it
    follows from `degree_level`) and a skill's `years` (jac counts them from `first_used` and
    the entries that link the skill). The import refuses any other name, and its answer lists
    the ones the entry takes.
- **`add` / `remove`:** lists.
  - `skills`, `related_skills` and `builds_on` take skill ids, or `new:<ref>` for a skill this
    batch creates;
  - `domains` takes names, from the `domains` list in `career` (jac doesn't create domains).
- **`why`:** one line. The user sees it in jac's change log.
- **`new: "skill"`** with a `ref` that's unique in the batch creates a skill. Other changes link
  it as `new:<ref>`, and the answer maps each `new:<ref>` to the new skill's id.
- At most one change per entry in a batch, and at most 100 changes.

The answer lists every change with its `before` and `after`. A 400 names what's wrong, per change,
by its position in the list (from 0); then nothing was applied.

**In file mode, a batch is final until it's imported.** An imported batch gives the entries it
touched a new `updated_at`, and the skills it creates get their ids only then. So the next batch
can't touch those entries, or link those skills, from the same career file:
- put what the user agrees to into one batch where you can;
- after the user imports a batch, ask for a fresh career file before a later batch touches the
  same entries or links a skill the first one created;
- if an entry was edited in jac after the career file was taken, the import refuses the whole
  batch: that's the `seen` check doing its job. A fresh career file, and the changes redone from
  its text, fix it.

**Undo:** with the connector or online, `changes` (`list_career_changes`) lists the batches,
and `undo ID` (`undo_career_changes`) puts that batch's entries back and deletes the skills it
created. In file mode the same is in jac, Account → Claude → Career changes,
Undo on the batch. Either way it's refused once an entry in the batch has been edited again since.
