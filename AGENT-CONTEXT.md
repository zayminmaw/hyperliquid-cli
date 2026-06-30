# AGENT-CONTEXT

> Last updated: 2026-06-30 | Session: Phase 4 (resolution + self-tuning) code-complete + verified; Phase 5 next

---

## 🎯 CURRENT TASK

- Task: Phase 5 — Mainnet hardening
- Goal: native exchange-side SL/TP trigger orders at entry (reuse trade trigger path), mainnet env gate + typed confirm, graduation checklist (N days / N resolved trades positive expectancy → surfaced in report), key-handling review, alerting on fires/rejects/breaker/loss-limit
- Status: not started (Phase 4 ✅ code-complete)
- Next action: confirm §13 Q6 (native SL/TP as a hard mainnet prerequisite — strongly recommended); inspect `trade` trigger path (stop-loss/take-profit) to reuse for native triggers; the executor-side resolver (Phase 4) stays for paper/testnet
- Blocked by: none. (Phase 1 live testnet order, Phase 3 shadow, Phase 4 config-tuner LLM runs all deferred pending keys.)

---

## 📍 LAST ACTION

- Did: Built Phase 4 — `executor/resolve.py` (SL/TP/expiry close-out → `trades` ledger) wired into runner; tuner stack (`stats` cohorts, `config_tuner` opus-4-8 strict tool, `prompt_tuner` text, `promote`/diff/history); `tune` CLI; decision.py loads `active_prompt.md`; lazy anthropic centralized in `core/llm.py`
- Result: 123 tests pass; tune run no-ops keyless when gated (verified); clamps hold on proposed + promoted config; anthropic stays out of import path. §13 Q4 = propose→approve everywhere
- File(s) touched: hlcli/core/{config_schema,llm(new)}.py, hlcli/state/store.py, hlcli/executor/{resolve(new),runner,decision}.py, hlcli/tuner/{stats,config_tuner,prompt_tuner,promote}.py(new), hlcli/cli/{app.py,commands/tune.py(new)}, tests/{test_resolve,test_tuner}(new)+test_cli

---

## 🗺️ CODEBASE MAP

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |
| `hlcli/core/config.py` | Hard caps (pydantic-settings, `HL_*` env); `get_caps()` |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` + `load_tunable()` (incl. `max_hold_minutes`) |
| `hlcli/core/{network,types,llm}.py` | network gate · domain types · `make_client()` (the ONE lazy anthropic import) |
| `hlcli/cli/app.py` | Typer app + global callback; wires `cli/commands/*` |
| `hlcli/cli/context.py` | `GlobalState`, `build_for(state, for_write)` — resolves account/key, mainnet gate |
| `hlcli/cli/commands/` | account/trade/markets/asset/exec_/config/**tune** · `cli/{stubs,watch,output}.py` |
| `hlcli/accounts/{store,keystore}.py` | SQLite account metadata · `0600` agent-key files |
| `hlcli/exchange/marks.py` | public `/info` reads over **httpx** (keyless); `api_url` |
| `hlcli/exchange/hyperliquid.py` | live backend (SDK+eth_account lazy); reads keyless, writes need key |
| `hlcli/exchange/{base,paper,factory}.py` | protocol · paper (state-backed book + fills) · factory |
| `hlcli/state/store.py` | sqlite: intake/HWM/idempotency/decision_log/**trades**/paper_positions; `open_state` |
| `hlcli/executor/gate.py` | `evaluate` (first-failure gate) + `_size` (fixed-fractional) + `infer_side` |
| `hlcli/executor/{enrich,decision}.py` | `EnrichedContext` (regime=None) · `decide`(sonnet-4-6, strict tool)+`validate_decision`+`load_decision_prompt` |
| `hlcli/executor/{intake,execute,runner,resolve,monitor}.py` | propose · fire · `run_once`(decide_fn/fire_enabled) · SL/TP/expiry close-out → trades · health |
| `hlcli/tuner/{stats,config_tuner,prompt_tuner,promote}.py` | cohorts(sample-gated) · opus-4-8 config(strict)+prompt tuners · proposed→active+diff/history |
| `hlcli/safety/breaker.py` | kill switch + daily-loss-limit |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety; LLM output is gate input, never a bypass
- [2026-06-27] paper is default network everywhere; mainnet gated (env flag + flag + confirm)
- [2026-06-27] hard caps in .env (off-limits to LLM/tuner); tunable surface in config/active_config.json, clamped on load
- [2026-06-27] anthropic + live-exchange deps lazy-imported so paper + tests run without keys/signing libs
- [2026-06-27] Order-path model claude-sonnet-4-6; daily tuner claude-opus-4-8
- [2026-06-27] Idempotency key (candidate id) recorded BEFORE fire → crash skips (missed trade), never double-fires
- [2026-06-27] Candidate side inferred from level geometry (long: sl<entry<tp); incoherent → rejected at intake + gate
- [2026-06-27] Paper book persists in state-<network>.db; manual paper (no state) stays in-memory
- [2026-06-30] Decision: out-of-range conviction is CLAMPED; bad enum / missing / non-numeric is DROPPED (never guessed). regime left None (no price-history feed); shadow=`fire_enabled=False` (logs+advances HWM) vs dry_run (mutates nothing)
- [2026-06-30] Phase 4: executor-side resolver writes trade outcomes (SL/TP at trigger price, expiry at mark) — native exchange triggers stay Phase 5. Tuners clamp on propose+promote+load, sample-gated (no cohort ⇒ no model call); propose→approve everywhere (§13 Q4)

---

## ⚠️ GOTCHAS

- §13 open questions have default choices (LLM scope, candidate source, news input, tuner autonomy, cadence, native SL/TP). Confirm with user before a task relies on one — don't assume.
- Keep no top-level imports of anthropic / hyperliquid / eth_account in hot paths (breaks paper + tests). Verified by `/tmp/hlcore` venv (no exchange extra).
- Marks/book/reads go through **httpx** `/info` (core dep), NOT SDK `Info` — don't "simplify" onto the SDK or paper stops being keyless. SDK+eth_account are write-only (signing).
- Tunable values must never reach the order path unclamped.
- Python 3.12 is at `/opt/homebrew/bin/python3.12` (system default is 3.11; project needs ≥3.12).
- Executor tests inject `run_once(..., decide_fn=...)` (act_now/drop in _helpers) so the LLM is never hit; real `decide`/tuners tested via FakeClient/FakeTool/FakeText. `exec`/`tune run` (with eligible cohort) need ANTHROPIC_API_KEY; empty stream / gated tuner skip the call.
- Tuner artifacts live beside `config_path`: proposed_/active_ config.json + prompt.md + promotions.jsonl. Tests MUST pass an isolated `caps(config_path=tmp/...)` or they write into repo `config/`. `tune` uses network-scoped state db for resolved trades.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs (use for Phase 1 exchange + Phase 5 trigger orders)
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
