---
name: project-planning
description: >
  Plans, executes, and tracks software projects phase by phase, keeping all docs in sync
  with the codebase. Driven by three commands: plan:start (read the user's PLAN.md, generate
  ACTION-ITEMS.md, code phase by phase), plan:sync (audit the code and update ACTION-ITEMS.md
  to actual status), and plan:handover (generate a handover-quality /docs folder). Also use
  when the user asks to break a project into phases, track progress, sync or update docs,
  create an action plan, or asks "what's the status" or "document what we built".
---

# Project Planning Skill

A skill for planning software projects phase by phase, executing step by step, and keeping all documentation perfectly in sync with the current state of the codebase throughout.

---

## Overview

This skill governs a structured, doc-driven workflow. Every project has a fixed set of living documents that are **always up to date** — not written once and forgotten. After every meaningful action (completing a step, changing an approach, finishing a phase), the docs get updated before moving on.

### The Doc System

| File | Purpose |
|------|---------|
| `PLAN.md` | Master plan — phases, goals, tech decisions, architecture |
| `ACTION-ITEMS.md` | Phase-by-phase checklist — the ground truth of what's done, in progress, and next |
| `README.md` | Index and entry point — links to all docs, quick project summary |
| `docs/` | Folder of detailed explanations — handover-quality docs for each major component |
| Inline comments | Key decisions, non-obvious logic, and TODOs inside the code itself |

---

## Trigger Commands

There are three special commands that drive this workflow. Always watch for them.

Commands use a `plan:` prefix (e.g. `plan:start`, `plan:sync`, `plan:handover`). These are workflow triggers, not shell commands or file references.

---

### `plan:start` — Begin the project

The user provides a `PLAN.md`. Your job is to read it, generate `ACTION-ITEMS.md`, scaffold `README.md`, and begin coding phase by phase.

**Steps:**
1. **Read `PLAN.md`** — understand all phases, goals, tech stack, and deliverables
2. **Generate `ACTION-ITEMS.md`** — break every phase into numbered, granular checklist items (see format below). Show it to the user and confirm before proceeding
3. **Scaffold `README.md`** — project summary, quick start placeholder, links to `PLAN.md` and `ACTION-ITEMS.md`
4. **Begin Phase 1** — announce "Starting Phase 1: [name]", then work through items one at a time:
   - Announce each step before doing it
   - Write the code
   - Add inline comments to non-obvious logic
   - Check off the item in `ACTION-ITEMS.md` immediately after completion
   - Never batch-complete steps silently
5. **At end of each phase** — add `✅ Phase N complete` to `ACTION-ITEMS.md`, update `README.md` status, summarize to user, then proceed to next phase
6. **Never skip ahead** — complete and document each step before moving to the next

---

### `plan:sync` — Sync docs with current codebase

The user wants `ACTION-ITEMS.md` to reflect the *actual* state of the code right now — not what was planned, but what has genuinely been implemented.

**Steps:**
1. **Read `ACTION-ITEMS.md`** — load the current checklist
2. **Audit the codebase** — scan all relevant files, folders, and code to determine what actually exists and works
3. **Cross-reference** — for each action item, determine its real status:
   - Implemented and working → `[x]`
   - Partially done or broken → `[ ] ... ← PARTIAL: [note]`
   - Not started → `[ ]`
4. **Rewrite `ACTION-ITEMS.md`** with accurate statuses
5. **Add a sync note** at the top:
   ```
   > Last synced: [date] — reflects actual codebase state
   ```
6. **Report to the user** — summarize what was found: X items complete, Y partial, Z not started. Flag anything that diverges significantly from the plan

Do not assume items are done because they were previously checked. Re-verify against actual code.

---

### `plan:handover` — Generate the /docs folder

The user wants full handover-quality documentation of what has been built.

**Steps:**
1. **Read `references/handover-template.md`** for the required structure
2. **Audit the entire codebase** — understand every module, entry point, data flow, and dependency
3. **Create or update `docs/` with these files** (skip any that genuinely don't apply):
   - `docs/architecture.md` — system overview, component diagram (ASCII if needed), data flow
   - `docs/setup.md` — prerequisites, environment variables, how to run locally and in production
   - `docs/modules.md` — one section per major module: what it does, key functions, inputs/outputs
   - `docs/decisions.md` — key technical decisions and why they were made
   - `docs/handover.md` — full handover doc using the template in `references/handover-template.md`
4. **Update `README.md`** — add links to all new docs files
5. **Run the docs-sync checklist** from `references/docs-checklist.md` before finishing
6. **Report to the user** — list all files created/updated

Every doc must reflect the *actual current code*, not the original plan. Write as if handing to someone who has never seen this project.

---

## Document Formats

### PLAN.md (user-provided)

`PLAN.md` is always written by the user, not generated by the agent. Do not create or overwrite it. Only read it. If the user types `plan:start` without a `PLAN.md` present, ask them to provide one first.

Expected contents (flexible, but should cover):
- Project goal
- Tech stack
- Phases with goals and deliverables
- Key decisions or constraints
- Out of scope items

### ACTION-ITEMS.md

```markdown
# Action Items

## Phase 1: [Name]
- [x] 1.1 [Completed task]
- [x] 1.2 [Completed task]
- [ ] 1.3 [Current task] ← IN PROGRESS
- [ ] 1.4 [Upcoming task]

✅ Phase 1 complete  ← added when all items done

## Phase 2: [Name]
- [ ] 2.1 ...
```

Status markers:
- `- [ ]` = not started
- `- [ ] ... ← IN PROGRESS` = currently being worked on
- `- [x]` = complete

### README.md

```markdown
# [Project Name]

> One-line description.

## Quick Start
[How to run it]

## Docs
- [Plan](./PLAN.md) — goals, phases, architecture
- [Action Items](./ACTION-ITEMS.md) — progress checklist
- [Architecture](./docs/architecture.md)
- [Handover](./docs/handover.md) ← when available

## Status
Current phase: Phase X — [name]
Last updated: [date]
```

---

## Rules

1. **Docs are updated before moving to the next step** — never defer doc updates
2. **One step at a time** — complete, document, then proceed
3. **Deviations are recorded** — if the plan changes mid-execution, update `PLAN.md` to reflect reality
4. **`ACTION-ITEMS.md` is the source of truth** for project status at any moment
5. **`docs/` is for humans** — write as if handing over to someone who has never seen this codebase
6. **Inline comments explain *why*, not *what*** — the code shows what; comments show intent and tradeoffs

---

## Reference Files

- `references/handover-template.md` — full template for `docs/handover.md`
- `references/docs-checklist.md` — checklist for verifying docs are in sync before handover

Read these when creating handover docs or doing a final review pass.
