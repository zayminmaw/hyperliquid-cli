# AGENT-CONTEXT

> Last updated: 2026-07-05 | Session: Sentry planned (PLAN.md §14) + Phase 6a trail engine built; 283 tests pass; next = 6b sentry shadow

---

## 🎯 CURRENT TASK

- Task: "Sentry" — in-trade manager (Phase 6, PLAN.md §14); scope user-confirmed: manages open positions + enters deferred WAITs, never originates
- Goal: 6a deterministic mechanics ✅ → 6b LLM shadow vs baseline → 6c gated live ↓risk actions → 6d ADD
- Status: 6a complete (gate passed: paper trail/scale-out verified live, ratchet-only, restart-safe, 283 tests)
- Next action: 6b — management decision prompt + strict tool, position-context enrich, shadow logging next to the 6a baseline, deferred re-entry on sentry cadence
- Blocked by: none

---

## 📍 LAST ACTION

- Did: built Phase 6a — `TunableConfig.trail` (clamped, default off), `sentry/engine.py` (pure R-anchored rules) + `sentry/apply.py` (idempotent scale-out, live stop place-new-then-cancel-old, shadow orderless), `sentry_log` + `initial_sl`/`scaled_out` migrations, `hl sentry once|run|status|log`, runner integration (`PassSummary.managed`), resolver R/win fixes, 33 new tests
- Result: 283 pass, keyless; verified end-to-end on paper vs real marks (scale-out @+1R, trail ratchet, churn-guard no-op, dry-run clean); PLAN.md §14 + ACTION-ITEMS + docs synced
- File(s) touched: hlcli/sentry/* (new), core/config_schema.py, state/store.py, executor/{runner,resolve}.py, tuner/stats.py, cli/{app.py,commands/sentry.py,commands/exec_.py}, tests/test_sentry.py (new), PLAN.md, ACTION-ITEMS.md, CLAUDE.md, docs/{modules,decisions}.md

---

## 🗺️ CODEBASE MAP

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |
| `hlcli/core/config.py` | Hard caps (`HL_*` env); `get_caps()`; relative `config_path` anchors to `data_dir` |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` (non-finite ⇒ field default) + `load_tunable()` |
| `hlcli/core/{network,types,llm}.py` | network gate · domain types (`OpenOrder.is_trigger`) · llm: the ONE lazy anthropic import; key from shell env or `.env`, never on Caps; `masked_api_key()` |
| `hlcli/cli/context.py` | `GlobalState`, `build_for(state, for_write)` — account/key resolution, mainnet gate |
| `hlcli/cli/commands/` | account/trade/markets/asset/exec_/config/tune · exec run has failure backoff + per-pass tunable reload |
| `hlcli/accounts/{store,keystore}.py` | SQLite metadata (resolve is network-checked; alias globally unique) · `0600` keys (perms enforced on load too) |
| `hlcli/exchange/marks.py` | keyless httpx `/info`: marks/book/candles/`sz_decimals` (meta) |
| `hlcli/exchange/rounding.py` | pure wire rounding: size floors to szDecimals; px 5 sig figs / 6−szDecimals |
| `hlcli/exchange/hyperliquid.py` | live backend; writes rounded on the wire; `frontendOpenOrders` incl. triggers |
| `hlcli/exchange/{base,paper,factory}.py` | protocol · paper (rejects triggers; flips overfill unless reduce-only) · factory |
| `hlcli/state/store.py` | sqlite: intake/HWM/idempotency/decision_log/trades(+`shadow`, additive migrations)/deferred/paper book |
| `hlcli/executor/gate.py` | first-failure gate incl. mark sanity; `_size` priced at mark; `infer_side` |
| `hlcli/executor/{enrich,decision,regime}.py` | context (+resolved outcomes, `followup`) · `decide` + NaN-safe `validate_decision` · ER regime |
| `hlcli/executor/{intake,execute,runner,resolve,protect}.py` | content-hash batch ids · idempotent fire · `run_once` (ledger-first, shadow book, unmanaged alert) · resolver (vanished-position reconciliation, shadow orderless, trigger cleanup) · protection + `cancel_placed`/`cancel_coin_triggers` |
| `hlcli/sentry/{engine,apply}.py` | 6a in-trade manager: pure R-anchored rules (ratchet/trail/scale-out) · apply (idempotent partials, live stop place-new-then-cancel-old, shadow orderless) |
| `hlcli/tuner/{stats,config_tuner,prompt_tuner,promote}.py` | cohorts (`scaled`=win) · tuners · promote consumes proposals, audit records content |
| `hlcli/safety/{breaker,alerts,graduation}.py` | kill switch + loss-limit (`persist=` for dry-run) · JSONL alerts · graduation verdict |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety; LLM output is gate input, never a bypass
- [2026-06-27] hard caps in .env; tunable surface clamped on load; anthropic + exchange deps lazy; sonnet-4-6 order path / opus-4-8 tuner; idempotency key recorded BEFORE fire
- [2026-07-01] wait→follow-up: act+wait DEFERRED not rejected; re-check inside freshness, `HL_FOLLOWUP_MAX_ATTEMPTS`; frozen while breaker tripped; re-checks labeled via `followup` in context
- [2026-07-02] Non-finite numbers NEVER clamp: NaN slides through min/max as the UPPER bound, so conviction/recheck are dropped and tunables fall back to defaults (`math.isfinite` everywhere a clamp guards money)
- [2026-07-02] Gate mark-sanity: the entry is a MARKET order ⇒ mark must exist, sit strictly inside sl/tp, and R:R **at the mark** must clear the floor; sizing + notional/leverage caps priced at the mark, not the proposed entry
- [2026-07-02] Ledger-first fills: trades row written on fill BEFORE protection; failed protection ⇒ emergency close + cancel placed triggers + row resolved `aborted` (was: no ledger). Positions the ledger doesn't know raise an edge-triggered `unmanaged_position` alert
- [2026-07-02] Live resolver reconciles against get_positions(): a vanished position (native trigger on a wick / manual close) books won/lost from candle extremes (SL checked first — pessimistic) else `closed` at mark; every live close cancels the coin's surviving reduce-only triggers
- [2026-07-02] Shadow books hypothetical trades (`trades.shadow=1`, entry at mark) resolved orderlessly — THIS is the tuner/graduation training data; shadow passes never touch real trades; hypothetical book honors one-per-coin
- [2026-07-05] Sentry (PLAN.md §14): deterministic mechanics FIRST (6a trail engine, all rules default off) → 6b LLM shadow judged vs that baseline → 6c gated live ↓risk → 6d ADD last; sentry never originates trades (user-confirmed: manages positions + enters deferred WAITs)
- [2026-07-05] R anchors to `initial_sl` once the stop ratchets; a profit-side stop-out books `won`; `scaled` partials count as wins; live stop replace = place-new-then-cancel-old (reject ⇒ old level kept everywhere); scale-out idempotent via `sentry:scale:<id>` recorded before the order

---

## ⚠️ GOTCHAS

- §13 open questions have default choices — confirm with user before a task relies on one.
- Keep no top-level imports of anthropic / hyperliquid / eth_account in hot paths. Verified 2026-07-02 in a fresh core-only venv (scratchpad `hlcore`; old `/tmp/hlcore` is PEP-668-locked, rebuild if needed).
- Marks/book/candles/meta go through **httpx** `/info`, NOT SDK `Info` — don't "simplify" onto the SDK or paper stops being keyless.
- PassSummary counters are disjoint: `rejected` = gate said no; `failed` = gate-approved but died at the exchange (reject/unfilled/aborted). Don't fold them back together.
- Executor entry is a MARKET order; ledger + protection size from `OrderResult.filled_size`/`avg_price`. Don't revert to GTC limit entry (review finding H1).
- test helpers' `caps()` pins `config_path=/nonexistent/...` so prompt/config reads never touch a dev's real `~/.hyperliquid-cli`; tuner tests still pass their own tmp `config_path`.
- Run tests with `.venv/bin/pytest` (bare python3.12 has no pytest). Python 3.12 at `/opt/homebrew/bin/python3.12`.
- Sentry 6a is inert until the tunable `trail` rules are switched on (all default off); `hl sentry once` tells you when nothing is active.
- Executor tests inject `run_once(..., decide_fn=...)`; real `decide`/tuners tested via fakes. `exec`/`tune run` need ANTHROPIC_API_KEY.
- `resolved_trades(limit=N)` = most recent N (newest-closed first) — don't assume oldest-first.
- FakeLiveExchange (test_protect) models positions/open_orders/canceled; `fail_triggers="tp"` = partial-protection case.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
