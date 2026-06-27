# Handover Document Template

Use this template for `docs/handover.md`. Fill every section — leave nothing as "TBD" at handover time.

---

```markdown
# Handover: [Project Name]

**Date:** [date]
**Author:** [who built it]
**Status:** [complete / partially complete — what's left]

---

## What This Project Does
[2–4 sentences. Plain English. No jargon.]

## How to Run It

### Prerequisites
- [dependency + version]
- [env vars needed]

### Setup
```bash
# step by step commands to get it running
```

### Running in Development
```bash
# command
```

### Running in Production
```bash
# command
```

---

## Architecture

### High-Level Structure
[Describe the main components and how they relate. Include a simple ASCII diagram if helpful.]

```
src/
├── [module]     # what it does
├── [module]     # what it does
└── [module]     # what it does
```

### Key Files
| File | Purpose |
|------|---------|
| `path/to/file` | What it does |

### Data Flow
[Describe how data moves through the system — from input to output.]

---

## Key Decisions & Why

| Decision | Alternatives Considered | Why We Chose This |
|----------|------------------------|-------------------|
| [decision] | [option A, option B] | [reasoning] |

---

## Known Issues & Limitations
- [issue]: [workaround or status]

## What's Not Finished (if partial handover)
- [ ] [item]: [context for whoever picks it up]

---

## External Dependencies
| Service/Library | Version | Purpose | Notes |
|----------------|---------|---------|-------|
| | | | |

---

## Contacts
- Original author: [name / handle]
- Related systems: [links or names]
```
