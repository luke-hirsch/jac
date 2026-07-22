# background

since the tool is not up and running i had to do a application by hand today. the good thing is, that we now have a gold standard on (a) how to achieve it and (b) to evaluate it.
lets focus on the cover letter, because with the cv i have the feeling with pinning and favourites we are on a good track

## achieving the good application

the cover letter was written by ai. to do that we had the job posting, the handpicked cv, all additional cv entries from the db, some ideas what is important to me. the first attempt was aweful, but then we implemented a matrix

|                | Soft-Skill-Fokus | ausgewogen | technischer Fokus |
| -------------- | ---------------- | ---------- | ----------------- |
| **persönlich** |
| **neutral**    |
| **förmlich**   |

and we landed on personal - neutral. but i guess that is a preference. we crafted up some variations for 2 of the paragrpahs and had a winner.

with that in mind we can rethink the @backend/jac/cover_letter.py cover letter pipeline. i still want to have a high grade of automation, so giving ideas for every application is not what i am after.
the snippets might also not be the best solution, because they feel stale. so i would like to extend the personal dossier by the matrix setting and probe of the writing style. so the prompt could say something like write a [matrix position} cover letter in the style of {writing style} for applicant with this personality to fit that role.

since then everything is ai written, we can loose the ai rating prompt, we only still need to verify the claims if they are rooted in the dossier or cv

## evaluating the application.

i believe tests should be stable. so if i change something in the code, that is still supposed to get reach the same goal, i can see if my tests still run. so if i optimise a funciton or refaktor it, in the end the test should still work if the goal is still the same. with the prompts that has been difficult. the goal of the prompt is a good cover letter. since we now have a good cover letter we can compare cover letters created by the prompt with the cover letter. when the prompts change, but not waht a good cover letter is, then the test should still pass or we have degrading prompts. since "good" is not a scientific metric we would need a an llm as judge here. and since they work staticstically we might need to run the test mutliple times and have a metric like "if the llm says its good 4 out of 5 times, it passes".

# to do

write two (or more) /setup-guides to achieve the new letter pipeline and letter evaluation. we are still in a phase where we can move fast and break things. do not go gentle. this is also more important than the portfolio guides. i will do them right after i did the @.claude/plans/to-do/[fullstack]-chat-assistant-rework.md guide. hopfully there is not so much reowkr that needs to be done for this.
