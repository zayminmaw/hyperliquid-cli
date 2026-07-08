# AGENT-CONTEXT

> Last updated: 2026-07-08 | Session: fresh-eyes review of 154ee47..HEAD (phase 6+7) — all findings fixed; 408 tests pass; uncommitted

---

## 🎯 CURRENT TASK

- Task: Phase 7 — Agent mode (PLAN.md §15) — CODE-COMPLETE (7a–7d)
- Goal: 7a supervisor ✅ → 7b `hl journal` ✅ → 7c reflection memory + tuners ✅ → 7d Mode A adoption ✅ (mechanics)
- Status: 7d complete + test-verified, uncommitted (7a–7c committed by user). 7d gate's live half pending: `hl account ls` is empty on this machine — needs a testnet account, a manual order + stop, then `hl --network testnet sentry once`
- Next action: commit 7d. Then operational: VPS deploy (`deploy/`), `hl agent run` on paper/testnet to accumulate graduation evidence; close 7d live check when a testnet account exists
- Blocked by: none (live 7d check waits on a testnet account)

---

## 📍 LAST ACTION

- Did: fixed all fresh-eyes review findings on 154ee47..HEAD. Big ones: (1) native SL/TP triggers now carry per-row oids (`trades.sl_oid/tp_oid`), so a post-ADD coin's sibling slice keeps its protection — every cancel is slice-scoped (`cancel_trade_triggers`), coin-wide sweep only when no open row remains; (2) `shadow_pass` now throttled by eval spacing + `sentry_max_llm_calls_per_day`; (3) ADD budget is per-position (counts since the coin's current position opened) + idempotency key is trade-id-scoped with an alert on crash-replay skip; (4) CLOSE bypasses churn caps + halted; (5) journal excludes `scaled` children from opened tally + graduation excludes them from `n`; (6) adopt anchors R at the loss-side extreme, not abs-distance; records the anchor stop's oid. Plus: atomic `record_fire` claim (kills exec/sentry double-fire race), deferred re-check drops already-fired, first-class `outcome` in decision log, tuner stage isolated in daily job, journal defers narrative for an incomplete day, `prior_actions` excludes holds, supervisor stamps LAST_TICK on failing ticks. New shared modules `executor/rmath.py` + `core/backoff.py`; centralized `alerts_path`, `DECISION_INTERVAL`, `require_exclusive_modes`.
- Result: 408 pass (12 new). CLI smoke + legacy-DB migration verified.
- File(s) touched: state/store.py, executor/{rmath(new),protect,resolve,execute,runner,regime}.py, sentry/{apply,gate,live,shadow,context,adopt,engine}.py, journal/{digest,writer,narrative}.py, agent/{daily,supervisor}.py, safety/{alerts,graduation}.py, core/{backoff(new),config}.py, cli/commands/{exec_,sentry,agent,journal}.py, tests/*

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
| `hlcli/sentry/{engine,apply}.py` | 6a: pure R-anchored rules (ratchet/trail/scale-out) · apply (idempotent partials, live stop place-new-then-cancel-old, shadow orderless) |
| `hlcli/sentry/{decision,context,shadow}.py` | 6b: strict `submit_management` (no ADD) · thesis+2-frame context (prior_actions excludes shadow rows) · shadow pass pairing proposal with the 6a baseline (never shown to model) |
| `hlcli/sentry/{gate,live}.py` | 6c/6d: management gate (churn clocks FROM sentry_log; ↓risk-only when halted; ADD = winners-only, code-sized, raise-stop-first) · live pass (eval spacing, 24h budgets, real book only) · `graduation_for_management` gates mainnet on the TESTNET book |
| `hlcli/sentry/adopt.py` | 7d: Mode A adoption — loss-side R anchor, records anchor stop's oid; never invents a stop |
| `hlcli/executor/rmath.py` | ONE home for initial-risk anchoring: `initial_risk/r_now/initial_stop/favorable_move` (was duplicated ~7 sites) |
| `hlcli/core/backoff.py` | `backoff_delay(base, failures, max)` — shared by exec/sentry/agent retry loops |
| `hlcli/agent/{intake_watch,supervisor}.py` | 7a: watched intake dir (enqueue-before-move, settle window) · tick loop (cadences, daily job, heartbeat, backoff); `cli/context.open_env` + `alerts.network_alerter` shared by exec/sentry/agent |
| `hlcli/journal/{digest,narrative,writer,lessons}.py` | 7b/7c: day digest (verdict rationales, R/PF) · opus `submit_journal` tool (reflection + lesson) · writer (meta-cached; failure degrades) · bounded lessons inject |
| `hlcli/agent/daily.py` | 7c: run_daily — journal yesterday → tuners → PAPER-only auto-promote → report alert |
| `hlcli/tuner/{stats,config_tuner,prompt_tuner,promote}.py` | cohorts (`scaled`=win) · tuners · promote consumes proposals, audit records content |
| `hlcli/safety/{breaker,alerts,graduation}.py` | kill switch + loss-limit (`persist=` for dry-run) · JSONL alerts · graduation verdict |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety (full statement lives in CLAUDE.md); hard caps in .env; tunable surface clamped on load; anthropic + exchange deps lazy; sonnet-4-6 order path / opus-4-8 tuner; idempotency key recorded BEFORE fire
- [2026-07-01] wait→follow-up: act+wait DEFERRED not rejected; re-check inside freshness, `HL_FOLLOWUP_MAX_ATTEMPTS`; frozen while breaker tripped; re-checks labeled via `followup` in context
- [2026-07-02] Non-finite numbers NEVER clamp: NaN slides through min/max as the UPPER bound, so conviction/recheck are dropped and tunables fall back to defaults (`math.isfinite` everywhere a clamp guards money)
- [2026-07-02] Gate mark-sanity: the entry is a MARKET order ⇒ mark must exist, sit strictly inside sl/tp, and R:R **at the mark** must clear the floor; sizing + notional/leverage caps priced at the mark, not the proposed entry
- [2026-07-02] Ledger-first fills: trades row written on fill BEFORE protection; failed protection ⇒ emergency close + cancel placed triggers + row resolved `aborted` (was: no ledger). Positions the ledger doesn't know raise an edge-triggered `unmanaged_position` alert
- [2026-07-02] Shadow books hypothetical trades (`trades.shadow=1`, entry at mark) resolved orderlessly — THIS is the tuner/graduation training data; shadow passes never touch real trades; hypothetical book honors one-per-coin
- [2026-07-05] Sentry (PLAN.md §14): deterministic mechanics FIRST (6a trail engine, all rules default off) → 6b LLM shadow judged vs that baseline → 6c gated live ↓risk → 6d ADD last; sentry never originates trades (user-confirmed: manages positions + enters deferred WAITs)
- [2026-07-05] R anchors to `initial_sl` once the stop ratchets; a profit-side stop-out books `won`; `scaled` partials count as wins; live stop replace = place-new-then-cancel-old (reject ⇒ old level kept everywhere); scale-out idempotent via `sentry:scale:<id>` recorded before the order
- [2026-07-06] 6b shadow-only: proposals logged PAIRED with the 6a baseline (baseline never in the model's context — no anchoring); `hl sentry once|run` = `run_once(include_intake=False)` watch pass (deferred re-entry shares attempts/idempotency with exec; intake stays exec's)
- [2026-07-07] Phase 7 (§15): repo stays producer-agnostic + OSS — signal handoff = watched JSON-batch intake dir, NO open port/HTTP; adoption never invents a stop (alert+skip); reflection inject bounded + own-outcomes-only; tuner auto-promote paper ONLY (testnet/mainnet propose→approve)

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
- Native SL/TP cancels are now BY OID (`trades.sl_oid/tp_oid`): use `cancel_trade_triggers` for one row; `cancel_coin_triggers` is the last-row-only sweep — never call it while a sibling slice is open. Legacy/oid-less rows fall back to the type-match cancel (safe: they have no sibling). Entry path + adopt + `apply_add` all record oids.
- `record_fire` now returns bool (atomic claim). `fire()` and the sentry apply helpers claim-then-act; don't reintroduce a separate `already_fired` check before it.
- Graduation counts positions, not partials (`assess` drops `status='scaled'`); the tuner's `summary`/cohorts still COUNT scaled (banked profit is a real outcome). Don't unify them.
- CLOSE is exempt from the sentry churn caps + halted gate (ends all risk); the budget/cooldown tests probe with `tighten_stop`, not `close`.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
