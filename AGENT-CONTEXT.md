# AGENT-CONTEXT

> Last updated: 2026-06-27 | Session: Phase 0 built + verified; Phase 1 next

---

## 🎯 CURRENT TASK

- Task: Phase 1 — Manual trade (Mode A)
- Goal: account store + keystore, marks feed, live hyperliquid backend, trade order commands, monitor, watch modes
- Status: not started (Phase 0 ✅ complete)
- Next action: confirm §13 Q2/Q3 not needed yet; start 1.1 accounts/store.py (SQLite at ~/.hyperliquid-cli/accounts.db)
- Blocked by: none

---

## 📍 LAST ACTION

- Did: Built + verified Phase 0 skeleton (venv py3.12, `pip install -e .[dev]`)
- Result: `hl --help`, paper `exec once`, `config show` work; 18 tests pass; no heavy deps at import time
- File(s) touched: pyproject.toml, hlcli/** (core, exchange, cli, executor), tests, .env.example, .gitignore

---

## 🗺️ CODEBASE MAP

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |
| `hlcli/core/config.py` | Hard caps (pydantic-settings, `HL_*` env); `get_caps()` |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` + `load_tunable()` |
| `hlcli/core/network.py` | `resolve_network` + `enforce_mainnet_gate` (I/O-free) |
| `hlcli/core/types.py` | Network/Side/OrderType/Action/Timing, Candidate/Decision/Order/Position |
| `hlcli/cli/app.py` | Typer app, global flags, stub factory, `exec once` + `config show` |
| `hlcli/exchange/{base,paper,factory}.py` | Exchange protocol, paper stub, backend factory |
| `hlcli/executor/runner.py` | `run_once` pass (no-op skeleton) |
| `hlcli/_lazy.py` | `require()` lazy-import helper for optional deps |

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
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs (use for Phase 1 exchange + Phase 5 trigger orders)
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
