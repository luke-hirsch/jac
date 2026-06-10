# Follow-ups

Review applications that are due for a follow-up and update their status.

## Steps

1. **Fetch due applications**
   Query: `Application.objects.filter(follow_up_date__lte=today, status="sent").order_by("follow_up_date")`

   If none found: "No follow-ups due. All caught up."

2. **Display the list**
   For each application show:
   - Company + job title
   - Applied date
   - Follow-up due date
   - Days overdue (if past due)

3. **Action each one**
   For each application ask:
   "What's the status for [Company — Job Title]?"
   Options:
   - `followed-up` — sent a follow-up, set new follow-up date (+7 days by default, ask to adjust)
   - `rejected` — received a rejection
   - `accepted` — got the job, congratulations
   - `skip` — leave as-is for now

4. **Save updates**
   Update the `Application` record with the new status and (if followed-up) the new `follow_up_date`.
   Print a summary: "[n] applications updated."
