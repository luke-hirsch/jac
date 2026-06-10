# Career Entry

Add or update an entry in the career database. These entries are the raw material used to generate tailored CVs and cover letters.

## Steps

1. **Choose entry type**
   Ask: "What type of entry? (work / education / project / skill)"

2. **Collect details by type**

   **Work experience:**
   - Company name
   - Job title
   - Start date, end date (or "present")
   - Location (optional)
   - 3–8 bullet points describing responsibilities and achievements
   - Key technologies / tools used

   **Education:**
   - Institution name
   - Degree / qualification
   - Field of study
   - Start date, end date
   - Notable achievements or thesis (optional)

   **Project:**
   - Project name
   - Short description (1–2 sentences)
   - Your role
   - Technologies used
   - Outcome / impact
   - URL or repo link (optional)

   **Skill:**
   - Skill name
   - Category (e.g. language, framework, tool, soft skill)
   - Proficiency level (beginner / intermediate / advanced / expert)

3. **Create or update**
   - Check if an entry with the same name/company already exists.
   - If yes, ask: "An entry for [name] already exists. Update it or create a new one?"
   - Save via Django ORM and confirm: "Saved CareerEntry #[id]: [title]."
