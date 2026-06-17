---
name: handover
description: Dump the current working context to a durable file so a later Claude session, or a human, can pick up cold. Use at session end, when context gets long, or when switching machine/person. This is a point-in-time snapshot — distinct from /update-claude, which refreshes the durable docs (CLAUDE.md, memory, roadmap).
---

# handover

Write a self-contained snapshot of where things stand right now, so work can resume without you in
the room. Optimise for a reader who has **zero** prior context from this session.

## When

- Ending a work session mid-task.
- The conversation is getting long / about to be summarised.
- Switching machine, or handing the thread to another person.

## Output

`.claude/handovers/YYYY-MM-DD-<slug>.md` (create the `handovers/` dir if it doesn't exist). Use
today's date; `<slug>` describes the task in a few kebab-case words.

## Contents

1. **Goal** — what we set out to do, in one or two sentences.
2. **Where it stands** — what's done, what's in flight, what's untouched. Name the real files.
3. **Decisions + why** — choices made this session and the reasoning, so they aren't relitigated.
4. **Open threads / risks** — unknowns, things that might be wrong, things to watch.
5. **Next action** — the single concrete thing to do next, specific enough to start immediately.

Keep it to what a reader needs to continue — no narration of the whole session.

## Not this

- `/handover` is a **snapshot**, not a docs update. It does not edit CLAUDE.md, memory, or the
  roadmap — that's `/update-claude`, run when a coding phase actually lands.
- If a fact is durable (true beyond this task), it belongs in memory or CLAUDE.md, not only here.
