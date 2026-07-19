# AGENT-CONTEXT

> Last updated: 2026-07-19 | Session: full feature test (paper + FIRST real testnet drill) → found+fixed F2 unified-account equity bug; 514 pass

---

## 🎯 CURRENT TASK

- Task: Vibe-Trading executor-feature shortlist (`hl-cli-feature-audit.md` §4) + J follow-on + fresh-eyes review fixes — branch `feat/executor-audit-shortlist`
- Goal: A→B→C(+D)→F→J + sentry config proposer + all 7 review findings — **ALL DONE**; 512 tests pass; docs synced
- Status: complete, committed per-item (branch, NOT pushed). Review found no regressions; 7 findings fixed (Mode A gross wiring, abort-count consistency, liveness DRY, sortino dbl-call, perf-scope doc, DAY_SECONDS, .env placement)
- Next action: push / open PR when the user asks; then OPERATIONAL (testnet drill — see below)
- Blocked by: none

---

## 📍 LAST ACTION

- Did: **Full feature test across all hl command groups (paper + first real TESTNET drill).** Every group driven live: markets/asset/account reads, config, Mode A (leverage, market open/close, native SL/TP, limit, cancel, cancel-all, per-trade cap reject), Mode B (paper LLM skip + rule fire; **testnet live fire + native protection + oid-tracked ledger + vanished-position resolve**), sentry (paper shadow LLM + testnet adopt on a real book), tuner (sample-gate no-op + full seeded propose→diff→promote→history), agent (status/watchdog never+stale/run loop+daily), journal (digest/show/ls; reflection defers on open day by design), repl. **Found + fixed F2 (HIGH):** live `equity()` read only perp `accountValue`, ~0 under HL **unified accounts** (now testnet default) → testnet equity 0 → Mode B sizing/gate broken. Fix: detect `userAbstraction=="unifiedAccount"` → read spot USDC + Σ uPnL. Verified live (0→997.98). **Also fixed F1 (low):** implemented real `config set`/`config edit` (were stale "Phase 4" stubs) — set refuses hard caps + coerces + clamps on write, edit re-clamps on save; removed now-dead `stubs.py`. Synced docs (cli/modules/decisions.md) + ran round-2 regression. **523 pass** (+11). Working tree NOT committed.
- Result: Both fixes drive-verified + regression-smoked. Testnet `tn` (0x8D67…) FLAT + clean, ~998 unified USDC. Findings: scratchpad/test-findings.md.

### Wave 2 implementation (F1/F2/docs COMMITTED, then L+K COMMITTED)
- Committed F2/F1/docs (756b349, 724e4c3, 45f0660); now building the Wave-2 shortlist from `hl-cli-feature-audit.md` §8:
  - **L (39fcde7):** real exit fill for externally-closed positions. New `Fill` type + `Exchange.recent_fills(since_ms)` (live=`user_fills_by_time`, paper=`[]`); `resolve._real_exit_price` books the actual closing-fill px (not the mark) for vanished/unscaled trades. Fill fields verified live (dir/closedPnl-gross/fee). Drive-verified 64581.0.
  - **K (125dcd9):** realized P&L + R **net of taker fee**. `rmath.taker_fee`, `Caps.taker_fee_pct` (HL_TAKER_FEE_PCT 0.045), `trades.fee_paid` col; `resolve._pnl` nets it (fee=0 ⇒ pre-K gross; test caps pin 0). Report `total_fees`. Drive-verified paper. SCOPE: resolve path only — sentry-close/abort fee-netting + real per-fill fees + FUNDING (sign unverified, deliberately unshipped) are follow-ons.
  - **M (5b2b47b):** liquidation-proximity guard. `Position.liquidation_px` (live-mapped, null-safe — null=far), `_alert_liquidation_near` edge-triggered critical alert within `HL_MIN_LIQUIDATION_DISTANCE_PCT` (5%), `exec status` `liq_dist%` col. Drive-verified live (maps None, no crash). SCOPE: in-trade alert; pre-fire maintenance-margin gate (`crossMaintenanceMarginUsed`) is the follow-on.
  - **O+P (0c9db9e):** O — REPL header always shows `<network> · acct <alias> (0xshort…) · equity …` (`repl._account_label` via `resolve_account`). P — `account edit <alias> --address/--rekey` (in place, alias+default kept; new `AccountStore.set_address`) + `config reset` (rm active_config.json → defaults). Smoke-verified live.
  - **G (480ee9c):** reconciliation halt-on-divergence. `executor/reconcile.py` diffs exchange vs ledger → `safe`/`requires_halt` (unexpected_position / size_mismatch>1% / unprotected_position; vanished NOT flagged; protection checked only where native-required). `hl exec reconcile [--halt/--no-halt]` trips breaker + critical `reconcile_halt` alert. Drive-verified live.
  - **K-follow-A (d8bfa8d):** fee-netting extended to sentry scale-out+close (`apply._partial_pnl`, `taker_fee_pct` threaded via `manage_open_trades`/`manage_live`) + abort (`_abort_pnl`); `split_trade` records `fee_paid`. Net-negative sentry close now books `lost`. Drive-verified paper.
  - **M-follow (cd792a0):** pre-fire maintenance-margin gate — `Exchange.maintenance_margin()` (live=`crossMaintenanceMarginUsed`, paper 0), `Caps.max_maintenance_margin_frac` (0.5), `GateContext` + gate reject after equity check. Drive-verified live (0.403/997).
- **547 pass.** DONE + committed: L, K(+follow), M(+follow), O, P, G. NEXT Wave-2: Q (rate-limit/WS — lowest, verify current HL limits first). BLOCKED: N + K-funding (funding SIGN needs a position held across a funding hour — do not guess). Remaining follow-ons: K real per-fill fees for live (vs the model); G auto-run reconcile at run_once/agent start.

### Prior action (executor-audit shortlist review)
- Did: **Fresh-eyes review (074e58b..HEAD) → fixed all 7 findings + synced docs.** #1 Mode A now enforces the account-wide gross-exposure/leverage caps via shared `gate.gross_exposure_reason`/`book_gross_notional` (daily-count stays executor-only — ledger-derived); #2 `trades_today` increments next to `open_trade` so an aborted entry counts consistently with `count_trades_opened_since`; #3 `_liveness` helper de-dups the 3 agent call sites; #4 sortino computes `_downside_deviation` once + symmetric guards; #5 `performance()` docstring states whole-DB (real+shadow) scope; #6 `DAY_SECONDS` centralized in `core/types`; #7 `.env.example` liveness cap moved to the agent block. 6 new tests (gross helpers, abort-count, watchdog paging, promote-preserves-trail). Docs synced: CLAUDE.md, docs/{cli,setup,modules,architecture}.md, .env.example.
- Result: **512 pass**. Mode A gross reject + watchdog paging + promote-preserves-trail all drive-verified. Branch NOT pushed.
- File(s) touched: executor/{gate,runner}.py, core/types.py, sentry/shadow.py, tuner/{stats,config_tuner}.py, cli/commands/{trade,agent,exec_,sentry}.py, state/store.py, .env.example, CLAUDE.md, docs/*, tests/{test_gate,test_executor,test_tuner,test_cli}.py

### Prior action (audit shortlist A–J + J follow-on)
- Did: **Implemented the whole audit shortlist A–J** on branch `feat/executor-audit-shortlist`, one commit per item:
  - **A** gross-exposure/leverage cap (`HL_MAX_TOTAL_EXPOSURE_USD` 0=off, `HL_MAX_GROSS_LEVERAGE` 5.0) — gate check after sizing; running gross mutated per fire/shadow (mirrors `open_coins`); mark-priced, entry-price fallback (fail-closed).
  - **B** daily new-entry cap (`HL_MAX_TRADES_PER_DAY` 0=off) — count derived from the ledger (`opened_at ≥ UTC-midnight`), restart-safe; running count mutated per fire.
  - **C/D** `stats.performance()` in `exec report` — profit factor, max drawdown, trade-based Sharpe/Sortino (None on <2 or zero-dispersion), + realized entry slippage via new additive `mark_at_entry` column.
  - **F** liveness watchdog — `agent/liveness.py` (never/alive/stale, fail-closed); `agent status` gains `liveness`; new `agent watchdog` (cron) pages `agent_stale` when stale WITH open positions, exits nonzero; `agent run` warns on resume-after-stale-with-positions.
  - **J** sentry self-tuning *evidence* — `sentry_exit_attribution()` (delta-R over diverging close/reduce proposals: `r_now − final_r`) in the sentry scoreboard + `management_cohorts()` in `exec report`. (Auto-proposer = follow-on.)
  - Plus fixed a PRE-EXISTING `464f8f6` failure (stale `temperature` assertion in `test_sentry_shadow.py`).
- Result: **504 pass**. Each item drive-verified (intra-pass caps, DB round-trips for D & J, watchdog paging). Branch NOT pushed.
- File(s) touched: core/config.py, executor/{gate,runner}.py, state/store.py, tuner/stats.py, agent/liveness.py(new), cli/commands/{exec_,sentry,agent}.py, .env.example, tests/{test_gate,test_executor,test_tuner,test_liveness(new),test_cli,test_sentry_shadow}.py
- Feature audit deliverable: `hl-cli-feature-audit.md` (repo root) — ranked A–J plan driving this work.

### Prior session
- Did: **Fresh-eyes review of 074e58b..HEAD** (2 phases: static + end-to-end flows), then fixed every confirmed finding + synced docs (ACTION-ITEMS.md R.1–R.6). Headline fixes: `exec shadow` was dropping the O-2 alerter (reconciliation silently skipped through that CLI path); shadow ledger rows masked real unmanaged positions (`_alert_unmanaged` now `shadow=False`); a canceled-partial-fill IOC in cloid recovery read as "never booked" (key released, live position untracked) — now returns the partial as a fill, parser fixture-locked; graduation/`conviction_calibration` no longer graded by `aborted`/`abort_failed`/adopted rows (`assess` surfaces an `aborts` count); decision prompt's conviction section made truthful for calibration mode (flat sizing kept — deliberate L-1 default, prompt was the bug); injection heuristics de-noised ("price action:" / "should act as support" no longer page); `KeystoreError` → `DOMAIN_ERRORS` (replacing never-raised `NotImplementedError`); slippage default single-sourced (exchange ctor param required); `adopt_unmanaged` reuses pass positions; `trade order market` help documents the shared IOC cap.
- Result: 481 pass (14 new). Smoked: `exec shadow` e2e on paper, market help, injection probes. **Working tree uncommitted.**
- File(s) touched: executor/{decision,runner,execute,protect,intake}.py, exchange/hyperliquid.py, sentry/{adopt,decision}.py, tuner/stats.py, safety/graduation.py, cli/errors.py, cli/commands/{exec_,trade}.py, tests/{test_executor,test_protect,test_intake,test_tuner,test_graduation,test_cli,test_hyperliquid_reads}.py, .env.example, CLAUDE.md, docs/evidence-gate.md, ACTION-ITEMS.md

### Prior session (audit phases 1–6)
- Did: (1) **Evidence audit** of every money-touching component vs external research + current HL docs → `docs/audits/2026-07-hl-cli-evidence-audit/` (Inventory/Evidence/Verdicts/Improvement-Plan; committed a3051a1). Headline: state DBs are EMPTY (tool has never traded in any mode); two DANGEROUS defects. (2) **Phase 1 safety** (committed ac25150): D-1 emergency close must be *confirmed* (accepted+filled) or the row resolves `abort_failed` (no fabricated P&L) + critical `emergency_close_failed` alert — raised backend errors caught, never crash the pass; D-2 `place_reduce_only` retries transport/429 with bounded backoff on the reduce-only paths only (protection + emergency close); D-3 entries carry a deterministic `cloid` (`entry_cloid` = sha256(candidate_id)[:16]) and a transport-unknown submit resolves via `order_status_by_cloid` — fill → tracked+protected, never-booked → key released, no lookup → re-raise + key kept. (3) **Phase 2 constrain-LLM**: L-1 conviction→size scaling OFF by default (`ConvictionSizing.enabled=False` ⇒ fraction 1.0, pure fixed-fractional; conviction logged for calibration); L-2 `HL_DECISION_SOURCE=llm|rule` hard cap + `decide_rule` baseline (act on every gate-valid setup, no LLM/key) resolved inside `run_once` via `decider_for(caps)`; L-3 `sentry_max_adds_per_position` default 2→0 (ADD disabled until graduation); L-4 `conviction_calibration()` in tuner/stats surfaced in `exec report` (excludes scaled/aborted/abort_failed); L-5 `injection_flags()` in intake — advisory screen on reasoning/news, warning alert + `thesis_flags` in decision-log context, never auto-rejects.
- Then: **Phase 3 exec quality**: X-1 entries are slippage-capped IOC limits — `HL_MAX_ENTRY_SLIPPAGE_PCT` (default 0.3%) plumbed Caps→factory→`HyperliquidExchange`, passed as `slippage=` to SDK `market_open` (the SDK applies it to mid + wire-rounds; its own default is 5%); reduce-only closes stay wide on purpose — a flatten must fill. X-2 gate rejects notional < `HL_MIN_ORDER_NOTIONAL` ($10, verified vs HL error docs) before the exchange can. X-3 decision prompt: WAIT = "not yet valid at the mark", never fishing a better fill (entry always fills at the mark). X-4 verified both backends' equity is mark-to-market (paper = start+realized+unrealized; live = accountValue) ⇒ unrealized drawdown alone trips the daily-loss breaker — locked with a test + breaker docstring.
- Then: **Phase 4 ops + P5/6 docs**: O-1 keystore encrypt-at-rest — `HL_KEYSTORE_PASSPHRASE` (env, kept off Caps like the API key) ⇒ `hl account add` writes eth_account V3 keystore JSON (scrypt+AES, lazy eth_account); format detected per file so plaintext keys keep loading; perms + refuse-if-readable unchanged. O-2 reconciliation — the unmanaged-position alert now runs on EVERY non-dry pass (shadow included); `HL_RECONCILE_ACTION=alert|adopt` — adopt reuses `sentry/adopt` (stop-protected only, fire-enabled passes only; flatten stays manual, it could kill a deliberate manual position). P5/6: `docs/evidence-gate.md` (7-point order-path checklist + validation ladder + per-class success metrics) referenced from a new binding CLAUDE.md section. Also: `thesis_flags` added to test_keys' context allowlist (Phase-2 field the allowlist test would have flagged on first real flag).
- Result: 467 pass (31 new this session). Paper-smoked: rule source fires end-to-end against the live BTC mark; injected thesis → `thesis_flagged`; report carries `conviction_calibration`.
- File(s) touched: executor/{runner,protect,execute,gate,decision,enrich,intake}.py, exchange/hyperliquid.py, core/{types,config,config_schema}.py, state/store.py, tuner/stats.py, cli/commands/exec_.py, .env.example, tests/{test_protect,test_executor,test_gate,test_sentry_add,test_intake,test_tuner,test_hyperliquid_reads}.py, docs/audits/* (new)

### Prior session (hl repl)
- Built `hl repl` (typer `get_command` dispatch under `standalone_mode=False`, session flags, live-PnL header, watch, readline) + fresh-eyes fixes: exit-code returns consumed, `open_env` store-leak fixed, mainnet re-arms typed confirm, unified `_NET_STYLE`/error rendering, typed `PositionRow`. 436 pass. Files: cli/{repl,errors,context,app}.py, __main__.py, tests/test_repl.py.

---

## 🗺️ CODEBASE MAP

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |
| `hlcli/core/config.py` | Hard caps (`HL_*` env); `get_caps()`; relative `config_path` anchors to `data_dir` |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` (non-finite ⇒ field default) + `load_tunable`/`save_tunable` + `set_field`/`get_field`/`tunable_keys` (manual `hl config set/edit`, hard caps refused) |
| `hlcli/core/{network,types,llm}.py` | network gate · domain types (`OpenOrder.is_trigger`) · llm: the ONE lazy anthropic import; key from shell env or `.env`, never on Caps; `masked_api_key()` |
| `hlcli/cli/context.py` | `GlobalState`, `build_for(state, for_write)` — account/key resolution, mainnet gate; `open_env` (stateful paper book / keyless live reads) |
| `hlcli/cli/repl.py` | `hl repl` shell: dispatches via `get_command(app)` (callback keeps gate/resolution); stateful session flags injected per line; live-PnL header + `watch`; readline history/completion |
| `hlcli/cli/errors.py` | `DOMAIN_ERRORS` + `render_domain_error` — shared by `__main__` and the REPL |
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
- [2026-07-15] Review-pass rulings: L-1's min-conviction floor removal is DELIBERATE (fixed the prompt, not the gate — flat sizing stands until calibration); Mode A market orders share the slippage cap on purpose (it's a hard cap); only strategy outcomes grade graduation/calibration (aborts surfaced separately, adopted rows excluded)
- [2026-07-02] Non-finite numbers NEVER clamp: NaN slides through min/max as the UPPER bound, so conviction/recheck are dropped and tunables fall back to defaults (`math.isfinite` everywhere a clamp guards money)
- [2026-07-02] Gate mark-sanity: the entry is a MARKET order ⇒ mark must exist, sit strictly inside sl/tp, and R:R **at the mark** must clear the floor; sizing + notional/leverage caps priced at the mark, not the proposed entry
- [2026-07-02] Ledger-first fills: trades row written on fill BEFORE protection; failed protection ⇒ emergency close + cancel placed triggers + row resolved `aborted` (was: no ledger). Positions the ledger doesn't know raise an edge-triggered `unmanaged_position` alert
- [2026-07-02] Shadow books hypothetical trades (`trades.shadow=1`, entry at mark) resolved orderlessly — THIS is the tuner/graduation training data; shadow passes never touch real trades; hypothetical book honors one-per-coin
- [2026-07-05] Sentry (PLAN.md §14): deterministic mechanics FIRST (6a trail engine, all rules default off) → 6b LLM shadow judged vs that baseline → 6c gated live ↓risk → 6d ADD last; sentry never originates trades (user-confirmed: manages positions + enters deferred WAITs)
- [2026-07-05] R anchors to `initial_sl` once the stop ratchets; a profit-side stop-out books `won`; `scaled` partials count as wins; live stop replace = place-new-then-cancel-old (reject ⇒ old level kept everywhere); scale-out idempotent via `sentry:scale:<id>` recorded before the order
- [2026-07-06] 6b shadow-only: proposals logged PAIRED with the 6a baseline (baseline never in the model's context — no anchoring); `hl sentry once|run` = `run_once(include_intake=False)` watch pass (deferred re-entry shares attempts/idempotency with exec; intake stays exec's)
- [2026-07-07] Phase 7 (§15): repo stays producer-agnostic + OSS — signal handoff = watched JSON-batch intake dir, NO open port/HTTP; adoption never invents a stop (alert+skip); reflection inject bounded + own-outcomes-only; tuner auto-promote paper ONLY (testnet/mainnet propose→approve)
- [2026-07-14] Audit-driven defaults (evidence in docs/audits/2026-07-…): conviction sizing OFF (uncalibrated scalar — re-enable only when `exec report` calibration shows monotonic bucket→avg_r), sentry ADD cap 0 (risk-increasing, post-graduation only), `HL_DECISION_SOURCE` selects llm|rule arbiter (A/B via separate HL_DATA_DIRs in shadow), an unconfirmed emergency close books `abort_failed` never `aborted`

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
- FakeLiveExchange (test_protect) models positions/open_orders/canceled; `fail_triggers="tp"` = partial-protection case; `fail_close=True|"raise"` = the abort_failed cases. `protect._sleep` is monkeypatched in retry tests — keep it a module attribute.
- Add tests opt IN to a budget (`_add_caps`/`sentry_max_adds_per_position=2`) — the default is 0. Gate conviction tests opt IN to scaling (`_scaling_on()`) — the default is flat 1.0. Don't "fix" a failing new test by flipping the production default back.
- Account-wide caps (audit A/B): gross exposure/leverage (`gate.gross_exposure_reason`) is enforced on BOTH the Mode B gate AND Mode A `trade` entries (reduce-only closes skip it). Daily new-entry cap is EXECUTOR-ONLY (ledger-derived — Mode A doesn't write the trades ledger). `trades_today` increments next to `open_trade` so aborts count (matches `count_trades_opened_since`); `gross_notional` only on a confirmed-protected fire (an abort is flat). Don't move either increment back below the abort returns.
- Config tuner proposes `trail` too now; `_validate` MERGES the payload onto the current config so untuned nested fields (`agent`) survive promote — don't revert to `model_validate(payload)` (it resets them to defaults). `DAY_SECONDS` lives in `core/types` — don't reinline `86400`.
- `order_status_by_cloid`'s parse *logic* is fixture-locked (filled/resting/canceled/partial-cancel ⇒ fill for origSz−sz) — but the LIVE response shape still needs the first testnet drill before trusting the recovery path on mainnet. A resting recovered entry gets canceled by `_resolve_unknown`, never left live-untracked.
- Evidence hygiene (2026-07-15): `assess` + `conviction_calibration` exclude aborted/abort_failed/adopted rows; `_alert_unmanaged` counts only REAL rows (`shadow=False`). Don't "simplify" the filters away — CLAUDE.md's evidence-gate section is binding.
- **HL UNIFIED ACCOUNTS** (2026-07-19, now the testnet default): perp `clearinghouseState.marginSummary.accountValue` reflects ONLY committed position margin (~0 when flat), NOT tradeable equity — that lives in the unified spot USDC balance. `equity()` detects `userAbstraction=="unifiedAccount"` (cached) and returns spot USDC total + Σ open-position uPnL; standard accounts unchanged. Don't revert to the accountValue-only read. `get_positions()` still uses clearinghouseState.assetPositions (fine under unified). The spot→perp swap is DISABLED under unified — funds go straight to the unified balance; never tell a user to transfer spot→perp on a unified account. Faucet lands in SPOT; agent wallet is a SIGNER only (don't send funds to it).
- Native SL/TP cancels are now BY OID (`trades.sl_oid/tp_oid`): use `cancel_trade_triggers` for one row; `cancel_coin_triggers` is the last-row-only sweep — never call it while a sibling slice is open. Legacy/oid-less rows fall back to the type-match cancel (safe: they have no sibling). Entry path + adopt + `apply_add` all record oids.
- `record_fire` now returns bool (atomic claim). `fire()` and the sentry apply helpers claim-then-act; don't reintroduce a separate `already_fired` check before it.
- Graduation counts positions, not partials (`assess` drops `status='scaled'`); the tuner's `summary`/cohorts still COUNT scaled (banked profit is a real outcome). Don't unify them.
- CLOSE is exempt from the sentry churn caps + halted gate (ends all risk); the budget/cooldown tests probe with `tighten_stop`, not `close`.
- Typer 0.26.8 here **vendors click as `typer._click`** — there is NO standalone `click` installed. Import click exceptions from `typer._click.exceptions` (`ClickException`/`Abort`/`Exit`/`UsageError`); build any programmatic dispatch on `typer.main.get_command(app)` (returns a `TyperGroup`) called with `standalone_mode=False`. `click_repl` and other click-importing helpers won't work. Under `standalone_mode=False` click **returns** the exit code — even `typer.Exit(n)` and an in-command `KeyboardInterrupt` return, they don't propagate — so read `command.main(...)`'s return value to surface non-zero exits; an `except Exit`/`except KeyboardInterrupt` around it is dead code.
- REPL header/watch read the REAL paper book via `open_env` (stateful `PaperExchange(state=store)`), NOT `build_for(paper)` which is stateless (empty `_mem`). `account positions` on paper is empty for that same reason. Header opens+closes the store each prompt; `watch` keeps it open for the loop's duration. `open_env` closes the store if the exchange fails to build (mainnet gate / no account) — don't reintroduce the leak by opening the store after `build_for`.
- REPL mainnet safety: entering mainnet (via `use mainnet` or a launch-time `-y`) clears any session-wide `yes` and re-arms the typed confirmation (`_guard_mainnet_yes`); re-enable deliberately with `set yes on` while on mainnet. The gate itself is unchanged — it still lives in the callback via `build_for(for_write=True)`.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
