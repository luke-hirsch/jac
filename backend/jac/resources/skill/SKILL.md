---
name: jac
description: Tailor a CV and a cover letter to a job posting from the user's career database in jac, talk both through with them, and save the result to jac, where they export the PDF. Use when the user pastes or links a job posting, asks for a CV, Lebenslauf, cover letter, Anschreiben or Bewerbung, or asks about their applications in jac.
---

# jac: applications from the career database

jac holds the user's career (jobs, projects, education, certifications, skills, languages) and
renders and exports the finished application as a PDF. In this conversation you do the
tailoring: you pick the CV entries, write the letter body and talk it through with the user.
Nothing reaches jac until the user wants it saved.

All calls go through `scripts/jac.py` in this skill's folder (Python 3, standard library only):

| command | what it does |
|---|---|
| `python3 scripts/jac.py career` | the career database as JSON |
| `python3 scripts/jac.py applications` | the latest applications (id, title, company, status, link) |
| `python3 scripts/jac.py show ID` | one application, in the shape `save` takes |
| `python3 scripts/jac.py save FILE` | creates the application in FILE; once FILE carries an `"id"` (written on the first save), saving it again updates the same application |

## The flow

1. **Get the posting.** Pasted text, or a link you fetch. Keep the full text: it is stored with
   the application.
2. **Read the career.** Run `career` once per conversation, before you suggest anything. If it
   reports `OFFLINE`, tell the user once that you're working from the snapshot taken on the date it
   names, and that saving will go through an import (see Saving).
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
6. **Save** when the user says so (see Saving). Give them the link; they review and export the
   PDF in jac.

## Picking the CV entries

- **Only entries from `career`, by their `id`** (`job:3`, `skill:12`, …). Never invent an entry,
  and never rewrite one: jac prints each entry's own text from the database. Here you decide
  *which* entries and in *what order*. If an entry's wording should change for good, tell the user
  to edit it in jac.
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
- `letter` may also carry `subject`, `salutation` and `closing`, only to override jac's own.
- The answer gives the application's `url` and any `warnings` (length, a salutation inside the
  body, a slipped German form, the highest degree added). Fix what's worth fixing, then save the
  same file again: it updates the application.
- To continue with an existing application: `applications`, then `show ID` into a file, edit it,
  keep its `"id"`, and `save` it.

**If `save` reports `OFFLINE`**, nothing was saved. Offer the user the JSON file to download; in
jac they import it under Applications → Import from Claude. To make the skill reach jac directly,
the user allows code execution to reach jac's host in Claude's settings (Settings → Capabilities,
network access).

**If a call reports that the token isn't accepted**, the token was revoked: the user downloads
the skill again in jac (Account → Claude) and uploads it to Claude in place of this one.
