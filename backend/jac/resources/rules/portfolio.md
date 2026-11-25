# jac: the public portfolio

jac renders a public portfolio for the user at `home_url`: their career entries from the same
database as the CV, plus blocks they write for it. Visitors need no login. A recruiter opens a
link the user handed out; a stranger answers a few questions and gets a page picked for them.
Search engines are asked not to index it (noindex), but anyone with the address can open it and
pass it on. **Treat every word you put on it as public.**

You reach it through its tools: `get_portfolio` (these rules came with it), `save_portfolio_block`
and `save_portfolio_page`. The career's own text, which every figure and claim on the portfolio
rests on, comes from `get_career`; improving the entries themselves is `get_career_review`'s.

## What `get_portfolio` shows

- **`may_edit`:** whether this token may change the portfolio. When it's false, you read, plan
  and show the user the changes, and every write is refused (see the end of this file).
- **`published`:** whether strangers can browse the portfolio. When it's false, `home_url` and
  the standard page show them nothing; a link still works, with its own selection only.
  Publishing is the user's switch in jac, on the portfolio pages, not yours.
- **`standard`:** the standard page, what a visitor sees who skips the questions or answers
  without picks. Its `title`, its `intro` and its Highlights (`featured`, ids in order). Below the
  Highlights the page shows everything else: the career entries and the active blocks. Empty, it
  falls back to the user's display name, their bio and their favourites. When a visitor picks a
  topic, only the Highlights tagged with it stay.
- **`links`:** pages of their own, each at its own `url`, for one reader or one occasion. A
  link has a `title`, an `intro` and Highlights (`featured`); `domains` scope its explore-more
  part below the Highlights (empty: the favourites), and `hide_explore` drops that part.
  `is_default` marks the page a visit falls back to when its own selection comes out empty.
  `revoked` links are history: their address is dead. Application links (made for an
  application in jac, showing what it sent) aren't listed and aren't yours to change.
- **`blocks`:** what the career can't hold. Each has a `kind` (below), a `title`, a `body`, the
  kind's `data`, `domains` (the questionnaire topics it shows under), `links` (entries and
  blocks shown beneath it, one level deep), `favourite` (a Highlight while the standard page has
  none of its own), an `order` and `is_active`. `has_image` and `has_media` say whether it
  carries a file.
- **`domains`:** the topic names blocks and links may use. jac creates none here: the user adds
  a domain in jac.
- **`career`:** every career entry by its `id`, with the title and subtitle the page shows. Pick
  Highlights and block links from these ids and the blocks' ids; never from memory.

**The standard page or a link?** The standard page is for everyone: change it when the user wants
their whole portfolio to lead with something else. A link is for a reader: a company, an event,
a person, a QR code on a business card. Don't bend the standard page to one company; make a link.

## The block kinds

| kind | for | needs | may have |
|---|---|---|---|
| `text` | a story in markdown: a project write-up, how they work, what they're after | `body` | `title` |
| `quote` | what someone said about the user, in their words | `body` (the quote), `data.by` (who, up to 100 characters) | `title`, `data.role` (up to 150) |
| `figures` | two to four key numbers | `data.figures`: 2 to 4 rows of `value` (up to 12 characters: "40 %", "3×", "12k") and `label` (up to 60) | `title`, `body` (one lead line) |
| `cta` | one call to action: get in touch, book a call | `data.label` (up to 40), `data.url` (`https://…` or `mailto:…`) | `title`, `body` (the pitch) |
| `linklist` | links out: talks, articles, repositories | `data.items`: 1 to 12 rows of `title` (up to 120), `url` (`https://…` or `http://…`) and `note` (up to 200, optional) | `title`, `body` (one lead line) |
| `image` | a picture with a caption | an uploaded image | `title`, `body` (the caption), `alt_text` |
| `media` | a video or audio file that plays on the page | an uploaded file | `title`, `body` (the caption), `alt_text`, a poster image |

Text renders as markdown: headings, lists, links, bold and italics. No raw HTML.

**Images and media are uploaded in jac**, on the portfolio's blocks page. The connector carries no
files, and jac fetches none from a URL. Suggest the block and what it should say; the user adds it
in jac. After that you can change its title, caption and alt text.

## The flow

1. **Read** with `get_portfolio` before you suggest anything. Tell the user whether the portfolio
   is published and whether this token may change it.
2. **Plan with the user.** What should a visitor take away? Lead with what the user wants to be
   hired for; a few strong Highlights beat a long list.
3. **Show exactly what will change** before each write: the block's full new text and data, a
   page's Highlights as titles in order, what's switched off. For a change, show before → after.
4. **Write only after the user's yes**, one write per yes. A write goes live at once: there is no
   draft and no review step in jac. A yes covers the change you showed, not the next one.
5. **After each write, give the user the `url`** from the answer, so they look at it, and say in
   one line what changed. If the portfolio isn't published, say that strangers see only the links
   until the user publishes it in jac.

**Undo:** every write answers with `before`, the object as it was. To undo, save `before` back
with the same tool: a block with its `id`, a link with its `id`, the standard page with
`standard: true` and the before's `title`, `intro` and `featured`. `before` is null when the
write created the thing: then switch the block off, revoke the link, or save the standard page
with an empty title, intro and `featured`.

**Removing:** nothing is deleted over the connector. A block comes off every page with
`is_active: false`, and back with `is_active: true`. A link comes down with `revoke: true`, and
that's final: its address stays dead, and a new link gets a new address. Ask before you revoke,
and say so. The user deletes things for good in jac.

## Rules for the content

- **Invent nothing.** Every fact on the portfolio comes from the career (`get_career`) or from the
  user in this conversation: numbers, names, employers, titles, dates, results, tools. A figure
  needs its source: say which entry or which of the user's words it comes from. If the career
  has no number for it, ask, or leave the figure out.
- **Quotes are real or they're nothing.** A quote is what a real person said or wrote, in their
  words, and the user confirms they may be named with it. Never write a quote yourself, never
  polish one into something they didn't say, and never guess a name or a role.
- **Nothing private.** The page is public, so none of this goes on it: phone numbers, postal
  addresses, an email address the user didn't give you for the page, salary or salary
  expectations, notice period, health or family matters, a current employer's confidential work
  (clients, internal numbers, unreleased products), the user's applications, or other people's
  personal details without their consent.
- **Keep the user's language and voice** (`writing_sample` in `get_career` shows it). One language
  per page.
- **Ids only from `get_portfolio`;** jac refuses any other. **Domains by name** from its `domains`.
- **Slugs** are lower case, words joined by hyphens. A link meant for one company gets a short
  random part (`acme-k7m2`): a bare company name lets anyone guess the address and learn from it
  that the user applied there.
- **Short beats complete.** An intro is two or three sentences. A text block makes one point.

## Writing

`save_portfolio_block` creates a block (`kind` is `text`, `quote`, `figures`, `cta` or
`linklist`), or changes one by its `id`: then only what you send changes. A block keeps its kind;
for another kind, create a new block and switch the old one off. `data` is the kind's whole set
of fields: send all of them.

```json
{
  "kind": "figures",
  "title": "In numbers",
  "data": {
    "figures": [
      { "value": "40 %", "label": "faster builds after the CI rework" },
      { "value": "3", "label": "teams onboarded to the platform" }
    ]
  },
  "domains": ["Backend"],
  "links": ["job:3"]
}
```

```json
{ "id": "block:12", "body": "The new text, in full." }
```

`save_portfolio_page` writes the standard page with `standard: true`, or a link: create one with
a `slug`, change one by its `id` (only what you send changes; `featured`, `domains` and
`hide_explore` each keep their value until you send them), revoke one with its `id` and
`revoke: true`.

```json
{ "standard": true, "intro": "…", "featured": ["project:7", "block:12", "job:3"] }
```

```json
{
  "slug": "acme-k7m2",
  "title": "For Acme",
  "intro": "…",
  "featured": ["job:3", "project:7"],
  "domains": ["Backend"]
}
```

The answer is the saved object, its `before` and its `url`. A refusal names what's wrong, by
field, and nothing was saved; fix it and try again.

## If this token may not edit

Every write is refused with `may_edit: false`. Show the user what you would change, and tell them
how to allow it: in jac, Account → LLM, under Connectors, "May change your public portfolio" on
the token they use for this chat. Tell them what it means, too: with it, the token can change
what every visitor sees, so it's for a token they keep safe.
