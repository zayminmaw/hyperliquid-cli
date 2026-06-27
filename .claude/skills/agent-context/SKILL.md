---
name: agent-context
description: >
  Creates and maintains AGENT-CONTEXT.md — a compact, always-current shared-memory file
  that lets any AI agent resume a multi-session project with zero ramp-up. Use at session
  start to orient, after meaningful steps to update, and at session end to do a full
  rewrite. Triggers on the commands ctx:read, ctx:init, ctx:update, ctx:end, ctx:auto,
  or phrases like "catch me up", "update context", "sync context", or "hand this off".
  Use alongside project-planning on any multi-session or multi-agent coding project.
---

# Agent Context Skill

A skill for maintaining `AGENT-CONTEXT.md` — a compact, always-current shared memory file that lets any AI agent pick up exactly where the last one left off, with zero ramp-up waste.

---

## Design Philosophy

**One file. Always fresh. Never fat.**

- Single flat file — cheap to read, works in any tool, any model
- Overwrite in place — no changelogs, no versioning, no history. Only current truth matters
- Strict section budgets — each section has a max line count; when you hit it, compress or drop the least useful entries
- Dense, not verbose — write for a machine reader, not a human one. Skip prose. Use short declarative statements
- Read first, always — every session starts by reading this file before doing anything else

---

## Trigger Commands

### `ctx:read` — Orient yourself

Load and parse `AGENT-CONTEXT.md` at the start of any session. Summarise it briefly to the user to confirm you're aligned: current task, last action, any live blockers.

If the file doesn't exist yet, create it using `ctx:init`.

### `ctx:init` — Create the file

Bootstrap a fresh `AGENT-CONTEXT.md` by scanning the codebase (if one exists) or from a blank template. Populate every section with whatever is currently known. Anything unknown → leave the placeholder.

### `ctx:update` — Patch after a step

After completing any meaningful step, overwrite only the sections that changed. Do not rewrite the whole file unless most sections changed. Surgical edits only.

### `ctx:end` — End-of-session write

At the end of a session (user says done, goodbye, stopping, etc.) — do a full review and overwrite of the entire file. This is the one time you rewrite everything to ensure maximum freshness for the next agent.

### `ctx:auto` (bare) — Smart update

Infer what's needed: if it's the start of a conversation with no prior context loaded → `ctx:read`. If work just finished → `ctx:update`. If the user is wrapping up → `ctx:end`.

---

## The File Format

`AGENT-CONTEXT.md` has fixed sections, always in this order, with hard line budgets. **Never add new top-level sections.** Use the existing ones.

```markdown
# AGENT-CONTEXT

> Last updated: [ISO date] | Session: [short description of what happened]

---

## 🎯 CURRENT TASK

<!-- MAX 5 lines -->

What is being worked on RIGHT NOW. One task only. If nothing active, write "none".

- Task: [name]
- Goal: [one sentence]
- Status: [not started | in progress | blocked | done]
- Next action: [the immediate next thing to do]
- Blocked by: [blocker or "none"]

---

## 📍 LAST ACTION

<!-- MAX 3 lines -->

The single most recent thing that was completed. Overwrite every update.

- Did: [what was done]
- Result: [what changed / what it produced]
- File(s) touched: [comma-separated list]

---

## 🗺️ CODEBASE MAP

<!-- MAX 20 lines total. One line per entry. Overwrite stale entries. -->

Key files and what they do. Only entries that help an agent navigate — skip obvious ones.

| Path           | Role         |
| -------------- | ------------ |
| `path/to/file` | what it does |

---

## 🧠 DECISIONS

<!-- MAX 10 entries. One line each. Drop oldest when full. -->

Choices that were made and must not be undone or second-guessed without reason.

- [YYYY-MM-DD] [decision]: [one-line reason]

---

## ⚠️ GOTCHAS

<!-- MAX 10 entries. One line each. Drop resolved ones immediately. -->

Landmines, quirks, non-obvious behaviours, or things that already caused bugs.

- [file or area]: [what to watch out for]

---

## 🔗 CONTEXT LINKS

<!-- MAX 5 lines -->

External references an agent might need: API docs, design specs, related repos, tickets.

- [label]: [url or path]
```

---

## Update Rules

### What triggers an update

| Event                              | Sections to update                               |
| ---------------------------------- | ------------------------------------------------ |
| Completed a step                   | LAST ACTION, CURRENT TASK (status + next action) |
| Made a significant decision        | DECISIONS                                        |
| Hit a bug or surprise              | GOTCHAS                                          |
| Created or heavily modified a file | CODEBASE MAP                                     |
| Task finished, new task starting   | CURRENT TASK (full overwrite)                    |
| End of session                     | All sections — full review and rewrite           |

### What does NOT trigger an update

- Trivial edits (fixing a typo, renaming a variable)
- Intermediate steps within a single task
- Reading files without changing anything

### How to overwrite sections

- Read the current section content
- Determine what is still true, what is now false, what is new
- **Delete stale entries entirely** — do not mark them "old" or comment them out
- Write the new version in place
- Never exceed the section's line budget — if at the limit, drop the least useful entry first

### Keeping it lean

- If `AGENT-CONTEXT.md` exceeds ~80 lines total, it's getting fat. Compress.
- CODEBASE MAP: only list files an agent would need to _find_ — not every file
- DECISIONS: only list decisions that could be accidentally reversed or repeated
- GOTCHAS: remove an entry the moment it's resolved

---

## Session Start Protocol

Every new session, before doing anything else:

1. Check if `AGENT-CONTEXT.md` exists
2. If yes → read it, then tell the user in one short paragraph: what the current task is, what was last done, and any active blockers
3. If no → ask the user if they want to init one, then run `ctx:init`
4. Proceed with work

This costs ~1–2 seconds and saves the entire ramp-up conversation.

---

## Integration with project-planning

If the project also uses the `project-planning` skill (`ACTION-ITEMS.md`, `PLAN.md`, etc.):

- `AGENT-CONTEXT.md` is the **agent's working memory** — fast, small, now
- `ACTION-ITEMS.md` is the **project's source of truth** — complete, persistent, for humans too
- On `ctx:update`: keep CURRENT TASK in sync with whatever is `← IN PROGRESS` in `ACTION-ITEMS.md`
- On `ctx:end`: ensure CURRENT TASK matches the last unchecked item in `ACTION-ITEMS.md`
- Never duplicate content — AGENT-CONTEXT.md points to ACTION-ITEMS.md, not replaces it

---

## Reference Files

- `references/template.md` — blank `AGENT-CONTEXT.md` to copy when running `ctx:init`

Read it when bootstrapping a new context file.
