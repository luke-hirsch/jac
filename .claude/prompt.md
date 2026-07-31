I skip addingresults to all guides of @.claude/plans/done/portfolio/ instead imoced them to done, to start fresh with improvement.
I clicked through the page and there have been several things, that we need to address

1.) The automatic portfolio building doe not work. If I click through the questionnaire I'll always land on a "ANYTHING SPECIFIC YOU'RE CURIOUS ABOUT?". and no matter what i type i get a 404. and feeling lucky does the same thing.

2.) the routing is still not alligned with the plan. we landed on portfolio per application per user + (ant i think this one is missing) a questionnair portfolio for just me as the host of the tool. when i create an application and a corresponding link the link is /portfolio/acme-5LuG/. this could be ok, i doubpt there will be base 64 ^ 4 applications to acme. problem is, as soon as i hit the escape hatch, i land on / with the questionnaire about me. one should rather land on /user/ and have a user questionnaire.
This could be "one size fits all" solution.

- list all domains connected to the user (what are you interested in)
- something like the cv matrix (technical - soft / personal - formal )
- a final question (like it is implemented today)

in the backend the domain shortlist, the style and the question go through a prompt that builds the portfolio. i am feeling lucky makes the picks randomly. for the prompt i dont have a defintiv answer yet.

for landing on "/" we need something more static. a link tree of sorts with a little bit of context and personal flair around it. could be django rendered. this way we could even look into some little seo stuff.

and the questionaire shouldl always have the "creat your own" type link as a possibility to signup and start using the jac tool.

3.) ui: the portfolio form is shit.

- on my macbook i cant see shit, because overflow in the mask is off and the mask itself is too tall for the screen.
- entries as a pure list is no fun. put them in their respective categories.
- on bigger screens it is a 2 column layout. that itself is not bad. but there are things like the favorites and the manual created blocks, that may span two columms. if we have edge cases like
  - personal block
  - cv item
  - personal block
  - cv item
  - cv item
  - ...

then the enclosed cv item should also be two columns

## The plan:

1. discuss the plan
2. write /setup-guide for the improvments. lets fit them on two branches. one "portfolio-flow-rework" and "portfolio-ui-rework"
