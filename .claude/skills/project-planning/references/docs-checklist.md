# Docs Sync Checklist

Run this checklist before marking a phase complete or doing a handover.

---

## PLAN.md
- [ ] Phase statuses are accurate (which are done, which are in progress)
- [ ] Any deviations from original plan are noted
- [ ] Tech stack section reflects what's actually being used
- [ ] Key decisions section includes decisions made during execution

## ACTION-ITEMS.md
- [ ] All completed items are checked `[x]`
- [ ] Current in-progress item is marked `← IN PROGRESS`
- [ ] No items are silently skipped or missing
- [ ] Phase completion markers (`✅ Phase N complete`) are present for finished phases

## README.md
- [ ] Quick start instructions actually work (test them)
- [ ] All doc links are valid and point to real files
- [ ] Status section reflects current phase
- [ ] Last updated date is current

## docs/ folder
- [ ] Every major module or component has a corresponding doc or section
- [ ] Docs reflect the *current* code, not the originally planned code
- [ ] No references to files, functions, or APIs that no longer exist
- [ ] `handover.md` exists and is complete (if project is done or being handed over)

## Inline Comments
- [ ] Non-obvious logic has explanatory comments
- [ ] TODOs are either resolved or tracked in ACTION-ITEMS.md
- [ ] No commented-out dead code left in without explanation

---

## Quick Sync Test

Ask yourself: *If someone cloned this repo right now with zero context, could they:*
1. Understand what it does? → README.md
2. Get it running? → README.md quick start
3. Understand where we are in the project? → ACTION-ITEMS.md
4. Understand why key decisions were made? → PLAN.md + docs/handover.md
5. Pick up where we left off? → ACTION-ITEMS.md + docs/

If any answer is "no" — fix it before proceeding.
