# AGENT-CONTEXT

> Last updated: 2026-06-27 | Session: Phase 1 code-complete + verified keyless; Phase 2 next

---

## 🎯 CURRENT TASK

- Task: Phase 2 — Executor (deterministic)
- Goal: state sqlite (book/idempotency/HWM/decision log), intake + propose, risk gate + sizing, execute, breaker; deterministic + restart-safe
- Status: not started (Phase 1 ✅ code complete; live testnet order deferred)
- Next action: start 2.1 `state/` sqlite schema, then 2.3 gate pipeline (highest-risk code → test first)
- Blocked by: none. (Live testnet order from Phase 1 still pending a funded agent wallet.)

---

## 📍 LAST ACTION

- Did: Built all Phase 1 (accounts, keystore, marks-via-httpx, live backend, trade/markets/asset/exec commands, watch, monitor)
- Result: 44 tests pass; verified keyless — paper + live public reads + suite run with NO hyperliquid/eth_account installed
- File(s) touched: hlcli/accounts/*, hlcli/exchange/{marks,hyperliquid,base,paper,factory}.py, hlcli/cli/{context,stubs,watch,output,app}.py + cli/commands/*, hlcli/executor/monitor.py, tests/*, pyproject.toml

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
| `hlcli/cli/app.py` | Typer app + global callback; wires `cli/commands/*` |
| `hlcli/cli/context.py` | `GlobalState`, `build_for(state, for_write)` — resolves account/key, mainnet gate |
| `hlcli/cli/commands/` | account, trade, markets, asset, exec_, config command groups |
| `hlcli/cli/{stubs,watch,output}.py` | phase stubs · `-w` poll watch · table/json emit |
| `hlcli/accounts/{store,keystore}.py` | SQLite account metadata · `0600` agent-key files |
| `hlcli/exchange/marks.py` | public `/info` reads over **httpx** (keyless); `api_url` |
| `hlcli/exchange/hyperliquid.py` | live backend (SDK+eth_account lazy); reads keyless, writes need key |
| `hlcli/exchange/{base,paper,factory}.py` | Exchange protocol, paper stub, backend factory |
| `hlcli/executor/{runner,monitor}.py` | `run_once` no-op pass · `position_health` |

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
- Keep no top-level imports of anthropic / hyperliquid / eth_account in hot paths (breaks paper + tests). Verified by `/tmp/hlcore` venv (no exchange extra).
- Marks/book/reads go through **httpx** `/info` (core dep), NOT SDK `Info` — don't "simplify" onto the SDK or paper stops being keyless. SDK+eth_account are write-only (signing).
- Tunable values must never reach the order path unclamped.
- Python 3.12 is at `/opt/homebrew/bin/python3.12` (system default is 3.11; project needs ≥3.12).

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs (use for Phase 1 exchange + Phase 5 trigger orders)
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
