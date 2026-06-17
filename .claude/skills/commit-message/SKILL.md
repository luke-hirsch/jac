---
name: commit-message
description: Write a short commit message for the current changes and commit them. Use when the user asks to commit, e.g. "commit this" / "/commit-message".
---

# commit-message

Stage the relevant changes, write a **short** commit message, and commit.

## Steps

Write a concise message:

- One imperative subject line (~50 chars, lowercase is fine — match this repo's history).
- Add a short body only if the change isn't self-explanatory; otherwise subject-only.
- Describe _what changed and why_, not a file list.

## Rules

- Commit only — do **not** push unless the user explicitly asks.
- Don't commit unrelated changes; if the working tree mixes concerns, ask or commit just the relevant subset.
- Don't `git add -A` blindly when there are clearly unrelated edits in the tree.
