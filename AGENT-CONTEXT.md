# AGENT-CONTEXT

> Last updated: 2026-06-27 | Session: plan:start — generated ACTION-ITEMS.md, scaffolding pending

---

## 🎯 CURRENT TASK

- Task: Phase 0 — Skeleton
- Goal: CLI scaffold, config (hard caps) + tunable clamp, network resolution, paper stub
- Status: not started (awaiting user sign-off on ACTION-ITEMS.md)
- Next action: confirm ACTION-ITEMS.md, then build item 0.1 (pyproject.toml + scaffold)
- Blocked by: user confirmation of the plan breakdown

---

## 📍 LAST ACTION

- Did: Ran plan:start — read PLAN.md, generated ACTION-ITEMS.md, README.md, this file
- Result: Phases 0–5 broken into granular checklist items; no code yet
- File(s) touched: ACTION-ITEMS.md, README.md, AGENT-CONTEXT.md

---

## 🗺️ CODEBASE MAP

<!-- Greenfield — no code yet. Intended layout (PLAN.md §12): -->

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth for progress) |
| `CLAUDE.md` | Working rules: ask when unclear, think before code, verify before assume |
| `hlcli/cli/` | typer app + command groups (planned) |
| `hlcli/core/` | config hard caps, config_schema clamp, types, network gate (planned) |
| `hlcli/exchange/` | base protocol, paper book, hyperliquid, marks (planned) |
| `hlcli/executor/` | intake, enrich, decision(LLM), gate, execute, monitor (planned) |
| `hlcli/safety/` | breaker / kill switch / loss limits / mainnet gate (planned) |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety; LLM output is gate input, never a bypass
- [2026-06-27] paper is default network everywhere; mainnet gated (env flag + flag + confirm)
- [2026-06-27] hard caps in .env (off-limits to LLM/tuner); tunable surface in config/active_config.json, clamped on load
- [2026-06-27] anthropic + live-exchange deps lazy-imported so paper + tests run without keys/signing libs
- [2026-06-27] Order-path model claude-sonnet-4-6; daily tuner claude-opus-4-8
- [2026-06-27] Build phase by phase (0→5); never skip a review gate

---

## ⚠️ GOTCHAS

- §13 open questions have default choices (LLM scope, candidate source, news input, tuner autonomy, cadence, native SL/TP). Confirm with user before a task relies on one — don't assume.
- Keep no top-level imports of anthropic / hyperliquid / eth_account in hot paths (breaks paper + tests).
- Tunable values must never reach the order path unclamped.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
