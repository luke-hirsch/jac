---
name: jac
description: Tailor a CV and a cover letter to a job posting from the user's career database in jac, talk both through with them, and save the result to jac, where they export the PDF. Also reviews that career database and improves it with the user's consent. Use when the user pastes or links a job posting, asks for a CV, Lebenslauf, cover letter, Anschreiben or Bewerbung, asks about their applications in jac, or wants their career data, CV entries or skills checked, completed or improved.
---

# jac: applications from the career database

jac holds the user's career (jobs, projects, education, certifications, skills, languages) and
renders and exports the finished application as a PDF. In this conversation you do the
tailoring: you pick the CV entries, write the letter body and talk it through with the user.
Nothing reaches jac until the user wants it saved.

You can also review the career itself and, once the user agrees, improve the entries. **When
the user wants their career data checked or improved, read `REVIEW.md` in this folder first**
and follow it; the rest of this file is about applications.

Everything goes through `scripts/jac.py` in this skill's folder (Python 3, standard library only).
It works in one of two modes, and picks it itself:

- **File mode**, in the Claude app. Its sandbox can't reach jac, so the career comes from a file
  the user attaches to the chat (in jac: Account → Claude → Download career for Claude), and what
  you write goes back as a file the user imports in jac.
- **Online mode**, in Claude Code, when `JAC_URL` and `JAC_TOKEN` are set: the script talks to
  jac directly, and saving saves.

| command | file mode | online mode |
|---|---|---|
| `python3 scripts/jac.py career` | the career from the attached file | the career from jac |
| `python3 scripts/jac.py review` | jac's machine review, from the same file (see `REVIEW.md`) | the review from jac |
| `python3 scripts/jac.py save FILE` | checks the application in FILE the way jac's import will, and hands it to the user | saves it; once FILE carries an `"id"` (written on the first save), saving again updates that application |
| `python3 scripts/jac.py apply FILE [--check]` | checks the career changes in FILE against the career file, and hands them over (see `REVIEW.md`) | applies them; `--check` only validates |
| `applications`, `show ID`, `changes`, `undo ID` | not available: they need jac itself | the latest applications, one application in the shape `save` takes, the batches of career changes, undoing a batch |

## The flow

1. **Get the posting.** Pasted text, or a link you fetch. Keep the full text: it is stored with
   the application.
2. **Read the career.** Run `career` once per conversation, before you suggest anything.
   - In file mode it names the file and the date it was taken. If it finds no career file, ask
     the user to attach one (in jac: Account → Claude → Download career for Claude) and wait for
     it; never work from memory or invent a career. If the file is a few days old, ask once
     whether they've changed their career in jac since; if so, ask for a fresh one.
   - If the user attached the file and `career` still finds none, look for it among the chat's
     files yourself and run the command again with `JAC_CAREER_FILE=<its path>` in front.
3. **Read the posting.** The role title, the company, a contact person and address if the ad
   names them, the language (write in the posting's language), and in a German ad whether it
   says Sie, ihr or du.
4. **Propose the CV and the letter**, following the rules below. Show the CV as a short list per
   section: entry titles, with a few words on why each pick fits, and name anything notable you
   left out. Then the letter body.
5. **Talk it through.** This is the point of working here: be a sparring partner, not a
   generator. Say plainly where the fit is thin and which requirements the career doesn't cover;
   don't paper over a gap with wording. Suggest what to lead with. Keep every correction the user
   makes for the rest of the conversation.
6. **Save** when the user says so (see Saving). In file mode, hand them the file and tell them
   where to import it; online, give them the link. Either way they review and export the PDF in
   jac.

## Picking the CV entries

- **Only entries from `career`, by their `id`** (`job:3`, `skill:12`, …). Never invent an entry,
  and never rewrite one: jac prints each entry's own text from the database. Here you decide
  *which* entries and in *what order*. If an entry's wording should change for good, offer to
  improve it as a career change (`REVIEW.md`), separately from the application.
- **Most relevant first within each section.** The order is the rank: when the page is full, jac
  drops entries from the end of a section, favourites after everything else.
- **The highest completed degree is always on the CV** (`highest_degree` in the career data).
  German public employers pay by it, so it stays whatever the posting says; jac adds it if it's
  missing. A study period that ended without a degree (`completed: false`) is ordinary content.
- **Favourites** (`favourite: true`) are the user's own nudge: keep them when they're at all
  relevant.
- **Keep the CV credible:** at least three jobs, two education entries and five skills when the
  career has them. Beyond that, include what this posting makes relevant and leave out what it
  doesn't; jac fits the page itself, so there is no need to count lines.
- **Skills:** the ones the posting asks for that the user really has, plus the ones the chosen
  jobs and projects rest on. A requirement the user doesn't have is a gap to discuss, not a skill
  to add.
- An entry the user insists on goes into `pinned` as well: pins are never dropped by the fit.
- A whole section that doesn't help (say, certifications for an unrelated field) can go into
  `sections_off`. Suggest it; don't do it silently.

## Writing the letter body

- **Only the body paragraphs.** No date, no addresses, no subject line, no salutation, no closing
  or signature, no markdown, no placeholders. jac adds the frame from the posting and the
  profile: the subject ("Bewerbung als …" / "Application for …"), the salutation (by name when
  there's a contact person), the closing and the sender block.
- **170 to 230 words** in three or four paragraphs: that is one A4 page in jac's layout.
- **In the posting's language.**
- **Every fact about the candidate comes from the career data.** Employers, titles, dates,
  numbers, skills, achievements: only what `career` states. Invent nothing, and use each
  experience once. The posting is context, never evidence: a requirement in the ad is not a fact
  about the candidate. Facts about the company come from the posting, or from a source you looked
  up and can name; without one, keep the company line general.
- **Shape:** why the candidate fits this role, then the strongest evidence, then a short close with
  a call to action and thanks.
- **Tone** comes from `letter_defaults.tone` unless the user says otherwise: `personal` is a warm,
  direct first-person voice; `neutral` is professional with measured warmth; `formal` is reserved
  and traditional. **Focus** (`letter_defaults.focus`): `soft_skill` leads with working style and
  motivation, `technical` with concrete achievements and tools, `balanced` gives both equal weight.
- **German "you":** `formal` always writes Sie. `personal` writes ihr, never du: a company isn't
  one person. `neutral` follows the posting's Sie or ihr, and writes Sie when there is no signal or
  when the ad says du. Never mix forms within a letter.
- **Voice:** `personality` holds the user's own answers about how they work and what drives
  them; let them shape emphasis and framing, but they aren't hard facts. If there is a
  `writing_sample`, write in that voice. It carries no facts.

## Saving

Write the application to a JSON file, e.g. `application.json`, and run
`python3 scripts/jac.py save application.json`:

```json
{
  "posting": {
    "text": "<the full posting text>",
    "title": "Senior Backend Engineer",
    "language": "de",
    "source_url": "https://…",
    "company": "Beispiel GmbH",
    "contact_name": "Frau Muster",
    "street": "Musterstraße 1",
    "zip": "10115",
    "city": "Berlin",
    "country": "Deutschland",
    "email": "",
    "phone": ""
  },
  "entries": ["job:3", "job:1", "project:7", "education:4", "skill:12", "skill:5", "language:2"],
  "pinned": [],
  "sections_off": [],
  "letter": { "body": "<the letter body>", "tone": "neutral" },
  "notes": ""
}
```

- `posting.text`, `entries` and `letter.body` are required; leave the other posting fields out or
  empty when the ad doesn't give them. `language` is a two-letter code.
- The ids are the career's own (`job:3`): use only ids `career` showed you.
- `letter` may also carry `subject`, `salutation` and `closing`, only to override jac's own.

**In file mode** nothing is saved from here. `save` checks the file the way jac's import will:
- what the import would refuse (a missing field, an id that isn't in the career file) is an
  error: fix the file and run `save` again;
- the `warnings` (the letter's length, the highest degree jac adds) are worth a look before you
  hand it over.

Then it answers with the `file` to give the user (in the Claude app it lands among the chat's
downloads). Tell them: in jac, Applications → Import from Claude, pick the file. jac opens the new
application and shows its own warnings, such as a salutation inside the body or a slipped German
form. Every import creates a new application, so for a correction after the user has imported,
tell them to edit it in jac, or write a fresh file for a fresh import.

**Online**, the answer gives the application's `url` and any `warnings` (length, a salutation
inside the body, a slipped German form, the highest degree added). Fix what's worth fixing, then
save the same file again: it updates the application. To continue with an existing application:
`applications`, then `show ID` into a file, edit it, keep its `"id"`, and `save` it.

**If a command answers `NOT HERE`**, it needs jac itself: in file mode that's `applications`,
`show`, `changes` and `undo`, and the message says where the same thing is in jac. Online, it means
jac can't be reached at `JAC_URL`.

**Online, if jac doesn't accept the token**, it was revoked: the user creates a new one in jac
(Account → Claude → Claude Code) and sets `JAC_TOKEN` to it.
