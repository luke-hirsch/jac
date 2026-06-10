# New Job Application

Guide the user through creating a complete job application.

## Steps

1. **Get the job post**
   - Ask: "Paste the job URL or the full job description text."
   - If a URL is given, fetch the page and extract the job title, company, and description text.
   - If text is pasted, parse out the title and company name.

2. **Review career entries**
   - Query the database: `CareerEntry.objects.all()` — list all entries grouped by type (work experience, education, projects, skills).
   - Show the user the list and ask: "Which entries are most relevant? You can confirm the pre-selection or adjust."

3. **Collect cover letter bullet points**
   - Ask: "Give me 3–5 bullet points you want to highlight in the cover letter (achievements, motivations, fit)."

4. **Generate CV and cover letter**
   - Call `jac.llm.generate_cv(career_entries, job_description)` to produce a tailored CV.
   - Call `jac.llm.generate_cover_letter(career_entries, job_description, bullet_points)` to produce the letter.
   - Show both drafts to the user for review.

5. **Confirm and save**
   - Ask: "Happy with the drafts? (yes / regenerate / edit)"
   - On confirm, create an `Application` record:
     - `company`, `job_title`, `job_description`
     - `cv_text`, `letter_text`
     - `applied_at` = now
     - `follow_up_date` = now + 14 days (ask user if they want a different interval)
     - `status` = "sent"
   - Print the saved application ID and the follow-up date.
