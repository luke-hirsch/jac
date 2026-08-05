we are getting close to the point where we would make this project production ready and push it to the server. before
that, i want to polish the pdf export and preview one more time. and make some last adjustments to the portfolio ai
filter and overall ui

## UI:

1.  pdf preview: its just css stuff. take a look at @.claude/pdf_preview1.png and @.claude/pdf_preview2.png . there is
    a ton of whitespace inside the overlay. and the overlay is very slim.
2.  pdf: the pdf itself is not the most beautiful cv i have ever seen.

- first i would remove the skill cloud. add it to the invisible ink. for the visible part just use the markdown and the headline
- dates on the left have weird line breaks. i dont really ahve a suggestion on how to make it better.
- if we gain some space because the skill cloud is removed from the entries, we can be a little more detailed in the skill section itself. or add more entries. either way, i would love to use the space.

3. could we add a dynamic colour to the cv. this could live in the user profile. defautl can be the boring blue.

4. overall: dark and high contrast mode are part of the profile settings, but for now have noe effect. we need to add them.

## CV Filter

- ties in with the last bulletin of 2. in the ui section. i have dropped out of univerity twice. but i have a bachelor of science degree. i was wondering if we could add a degree boolean to the model and then instruct the llm to favor education with a degree over drop out. the dop out expierience is not necessarly a bad one, but my highest degree should always be part of the cv. for public service jobs this is important money wise in germany.

- the certification part is very prominent. but this gives me an idea could we add a bool to the cv form in the frontend for the sections. this way entire sections can be removed with one click. removing section should result in more possible entries in the other sections

- see below: a pre render run to determine if everything fits and adjaust accordingly would be good.

## Cover letter:

- sometimes the text is too long. can we add a loop that renders the pdf in the background, and if doesn't fit on one page highlight the characters in the editor that are too long AND/OR start a new run with a instruction to shorten it by this. the paragraph rewriter with "shorten" is always too effective. after shortening only one paragraph remains. so maybe this is where we could start and then see if we need to automate this further.
- the date in the cover letter in the top right looks like shit. use the language setting to format it. by default iso/din/si not the shitty us format.
- ties in to the style matrix: the personal to formal tone is great. if set to personal avoid pronouns like "Du" and "Sie" in german. this is **only** in german. use "Ihr" or "Euch". in formal and neutral the "Sie" and "Ihren" is perfect.

# To do

1. lets elaborate on the remarks.

- what have i missed?
- what can be taken even further?

2. lets divide this in multiple tasks and create /setup-guides
