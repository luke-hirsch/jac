---
name: update-claude
description: After a coding phase, refresh the Claude-meta docs to the new project state — CLAUDE.md (roadmap + current state), memory files, and the plans backlog. Distill, don't dump. Use once a chunk of work has actually landed.
---

# update-claude

After a coding phase lands, bring the durable docs back in sync with reality.

## Do

1. **CLAUDE.md** — update the `roadmap` (mark/retire finished items, re-order what's next) and the
   `current state` section. Keep stack/layout accurate too if it changed. Stay lean; no claim may
   contradict the code.
2. **Memory** — update the relevant memory files
   (`/Users/lukas/.claude/projects/-Users-lukas-Projects-jac/memory/`) and the `MEMORY.md` index.
3. **Plans** — move completed guides from `.claude/plans/to-do/` to `.claude/plans/done/`.

## Distill, don't dump (the rule that keeps this from re-bloating)

Memory and CLAUDE.md are **durable facts**, not a session log. Record only what stays true beyond
this task and isn't already in the repo/git history:

- ✅ validated findings, settled decisions + *why*, conventions, model/threshold choices.
- ❌ dated phase chatter, commit hashes, test counts, "shipped in guide X" framing, blow-by-blow of
  what happened this session. (Point-in-time snapshots are `/handover`'s job, not memory's.)

When updating an existing memory, **rewrite** it to the current truth — don't append layers of
history. Delete memories that the latest work made false.
