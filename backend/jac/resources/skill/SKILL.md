---
name: jac
description: Tailor a CV and a cover letter to a job posting from the user's career database in jac, talk both through with them, and save the result to jac, where they export the PDF. Also fills that career database from their CV, reviews it and improves it with the user's consent, and shows and changes their public portfolio. Use when the user pastes or links a job posting, asks for a CV, Lebenslauf, cover letter, Anschreiben or Bewerbung, asks about their applications in jac, wants their CV turned into jac entries, wants their career data, CV entries or skills checked, completed or improved, or wants to see or change their portfolio.
---

# jac

Everything goes through the jac connector, and the rules come with it, so they are always jac's
current ones:

- For an application, call `get_career` first, once per conversation, and follow the rules it
  returns.
- To check or improve the career data, or to fill it from their CV, call `get_career_review`
  and follow its rules.
- To see or change the public portfolio, call `get_portfolio` and follow its rules.

If jac's tools aren't there, the connector is off in this chat or not added yet. Ask the user to
switch it on (+ → Connectors → jac), or to add it: in jac, Account → LLM, under Connectors. Don't
work from memory or files in the meantime.
