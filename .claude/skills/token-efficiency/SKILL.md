---
name: token-efficiency
description: >
  Enforces token-efficient agent behavior: surgical file reads, compact tool output,
  lean sub-agent prompts, minimal handoff summaries, and response length calibrated to
  the task. Use when the user asks to save tokens, reduce context usage, or be more
  efficient, when running long agentic loops or sub-agent chains where context is at a
  premium, and on the toggle commands token-saver:start (enable) and token-saver:stop
  (disable).
---

# Token Efficiency Skill

## Toggle Controls

The user can enable or disable this skill mid-session using these triggers:

| Trigger             | Action                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `token-saver:start` | Enable / resume all token-efficiency rules. Confirm with: `Token saver ON.`                                        |
| `token-saver:stop`  | Suspend all token-efficiency rules for this session. Revert to default verbosity. Confirm with: `Token saver OFF.` |

**On `token-saver:stop`**: Stop applying all rules in this skill. Respond normally — full verbosity, no restrictions on file reads, tool output, or response length. Do NOT silently keep applying rules.

**On `token-saver:start`**: Apply all rules immediately from the next response onward. Also use this to re-enable after a `token-saver:stop`.

---

## Core Philosophy

Save tokens without sacrificing correctness or readability. The goal is to eliminate
waste — not to produce terse, cryptic output. When in doubt, prefer clarity but cut
everything that doesn't serve it.

---

## Baseline Rules

These apply whenever this skill is active, regardless of task type.

> Note: skills load on demand, not automatically at session start. If you want these
> baseline rules enforced in every session unconditionally, copy them into your
> project's `CLAUDE.md` — that file is always in context.

### 1. Act First, Narrate Never

- Do NOT explain what you're about to do before doing it.
- Do NOT write "I'll now read the file..." or "Let me check..." — just use the tool.
- After a tool call, only comment if the result is surprising, ambiguous, or requires a decision.
- Summaries after straightforward tool calls are waste. Skip them.

### 2. Read Surgically, Not Wholesale

- Never read an entire file when a targeted query suffices.
- Prefer in order: `grep` → `sed`/line range → full file read.
- For large files (>200 lines), always grep or search for the relevant symbol/section first.
- If you need a function, search for its definition — don't read the whole module.

### 3. Compact Tool Output

- After each tool call, emit only what changes the plan or is directly actionable.
- Do NOT re-echo file contents back unless the user asked to see them.
- Do NOT list every file found in a search — filter to what's relevant.
- One-line status is enough for successful, expected operations.

### 4. Lean Sub-Agent Prompts

- Sub-agent task prompts should be specific and minimal — state the goal, the constraints, and the output format. Nothing else.
- Do NOT over-specify implementation details the sub-agent should figure out itself.
- Do NOT include background context the sub-agent doesn't need for its slice of the task.
- Avoid repeating instructions already in the sub-agent's system prompt or skill context.

### 5. Response Length Calibration

- Match response length to task complexity.
- One-liners for confirmations, status checks, and yes/no decisions.
- Full responses only for: plans requiring user sign-off, ambiguous errors, multi-option decisions.
- Never pad responses with "Hope that helps!", "Let me know if you need anything else", etc.

---

## Task-Specific Rules

Apply these on top of the baseline when the task type matches.

### Agentic Loops

- Before starting a loop, output a one-line plan max. No detailed step-by-step previews.
- Checkpoint summaries between loop iterations should be ≤3 lines: what changed, what's next, any blocker.
- If a loop iteration produces no meaningful change, say so in one line and move on.
- Avoid re-reading files that were already read earlier in the same loop unless they've changed.

### File Operations

- Batch related reads into a single context load where possible.
- When scanning a codebase for a pattern, use `grep -r` before opening any file.
- Prefer `wc -l` to gauge file size before committing to a full read.
- Cache mentally: if you've read a file this session, don't re-read it unless you have reason to believe it changed.

### Multi-Session Handoffs

- End-of-session summaries should be structured and minimal:
  ```
  STATUS: <one line>
  COMPLETED: <bulleted, max 5 items>
  NEXT: <bulleted, max 3 items>
  BLOCKERS: <bulleted or "none">
  KEY FILES: <only files the next session will need>
  ```
- Do NOT include reasoning, backstory, or explanations in handoff summaries — just state.
- Strip context that the next session can recover from the codebase itself.

### Sub-Agent Chains

- Decompose tasks so each sub-agent receives only the context slice it needs.
- Pass results between agents as structured data (JSON, filepath, symbol name) — not prose summaries.
- If a sub-agent result is "as expected", don't narrate it — just use it.
- Wind down agent teams when work is complete — don't keep spawning or messaging sub-agents that have nothing left to do.

---

## Anti-Patterns to Avoid

| Waste Pattern                                         | Instead                                      |
| ----------------------------------------------------- | -------------------------------------------- |
| "Let me read the file to understand the structure..." | Just read it.                                |
| Re-summarizing tool output in prose                   | Use the output directly.                     |
| Full file read to find one function                   | `grep -n "function_name"` first.             |
| Sub-agent prompt with 10 paragraphs of context        | 3-5 lines: goal, constraints, output format. |
| "I've completed X, now I'll move on to Y..."          | Just move on to Y.                           |
| Listing all 30 search results                         | Filter to the 2-3 relevant ones.             |

---

## Token Budget Awareness

When context is getting long (you'll feel this as the conversation grows):

- Proactively summarize and compress earlier reasoning before it crowds out working context.
- Prefer referencing file paths + line numbers over re-pasting code blocks.
- If asked to explain something already covered earlier in the session, give a one-liner and offer to expand if needed.
