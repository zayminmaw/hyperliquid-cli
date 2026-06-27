# AGENT-CONTEXT

> Last updated: 2026-06-27 | Session: Phase 2 (deterministic executor) complete + verified; Phase 3 next

---

## 🎯 CURRENT TASK

- Task: Phase 3 — LLM decision
- Goal: enrich context, replace deterministic `decide` stub with claude-sonnet-4-6 structured output, validate+clamp, decision log already wired, `exec shadow`
- Status: not started (Phase 2 ✅ complete)
- Next action: confirm §13 Q1 (choose-among-supplied vs generate) + Q5 (cadence); build `executor/enrich.py` then real `executor/decision.py`
- Blocked by: none. (Phase 1 live testnet order still pending a funded agent wallet.)

---

## 📍 LAST ACTION

- Did: Built Phase 2 deterministic executor — state sqlite, intake/propose, gate+sizing, execute (idempotent), breaker, runner pass
- Result: 86 tests pass; e2e on paper: propose→once fires→persistent book; HWM blocks reprocess; breaker halts. Decision stub at `executor/decision.py` (Phase 3 swaps the body)
- File(s) touched: hlcli/state/store.py, hlcli/executor/{gate,intake,execute,decision,runner}.py, hlcli/safety/breaker.py, hlcli/exchange/paper.py, hlcli/cli/commands/exec_.py, tests/*

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
| `hlcli/exchange/{base,paper,factory}.py` | protocol · paper (state-backed book + fills) · factory |
| `hlcli/state/store.py` | sqlite: intake/HWM/idempotency/decision_log/paper_positions; `open_state` |
| `hlcli/executor/gate.py` | `evaluate` (first-failure gate) + `_size` (fixed-fractional) + `infer_side` |
| `hlcli/executor/{intake,decision,execute,runner,monitor}.py` | propose · stub decide · idempotent fire · `run_once` · position_health |
| `hlcli/safety/breaker.py` | kill switch + daily-loss-limit |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety; LLM output is gate input, never a bypass
- [2026-06-27] paper is default network everywhere; mainnet gated (env flag + flag + confirm)
- [2026-06-27] hard caps in .env (off-limits to LLM/tuner); tunable surface in config/active_config.json, clamped on load
- [2026-06-27] anthropic + live-exchange deps lazy-imported so paper + tests run without keys/signing libs
- [2026-06-27] Order-path model claude-sonnet-4-6; daily tuner claude-opus-4-8
- [2026-06-27] Build phase by phase (0→5); never skip a review gate
- [2026-06-27] Idempotency key (candidate id) recorded BEFORE fire → crash skips (missed trade), never double-fires
- [2026-06-27] Candidate side inferred from level geometry (long: sl<entry<tp); incoherent → rejected at intake + gate
- [2026-06-27] Paper book persists in state-<network>.db; manual paper (no state) stays in-memory

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
