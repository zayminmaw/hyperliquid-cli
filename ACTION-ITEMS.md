# Action Items

> Generated from PLAN.md. Source of truth for project status.
> Work proceeds phase by phase; each phase ends at a review gate (PLAN.md §12).
> Do not build ahead of the current phase or skip a gate.

## Phase 0: Skeleton
Gate: `hl --help` works; paper `exec once` no-ops cleanly. ✅ passed

- [x] 0.1 Project scaffold: `pyproject.toml` (Python ≥3.12; deps: typer, rich, pydantic, pydantic-settings; extras: exchange/llm/dev; `hl` console script)
- [x] 0.2 Package skeleton: `hlcli/` with `cli/ core/ exchange/ accounts/ executor/ tuner/ state/ safety/ tests/` and `__main__.py`
- [x] 0.3 `core/config.py` — hard caps via pydantic-settings from `.env` (network gate, paths, STARTING_EQUITY, MAX_NOTIONAL_PER_TRADE, MAX_CONCURRENT_POSITIONS, DAILY_LOSS_LIMIT_PCT, MAX_LEVERAGE, RR_FLOOR, ALLOWED_COINS, MAX_SIGNAL_AGE_MINUTES, model names + token budgets)
- [x] 0.4 `core/config_schema.py` — tunable surface load + clamp; missing file → safe defaults; malformed → ConfigError
- [x] 0.5 `core/network.py` — network resolution (paper default) + mainnet gate (HL_ENABLE_MAINNET + --network mainnet + typed confirm)
- [x] 0.6 `core/types.py` — domain types (Network, Side, OrderType, Action, Timing, Candidate, Decision, Order, Position)
- [x] 0.7 `exchange/base.py` — Exchange protocol + OrderResult; `exchange/paper.py` — paper book stub; `exchange/factory.py`
- [x] 0.8 `cli/app.py` — typer app + command groups (account/markets/asset/trade/exec/tune/config); global flags; `cli/output.py` rich+json helper; `_lazy.py` lazy-import helper
- [x] 0.9 `exec once` paper path no-ops cleanly (`executor/runner.py`); bonus working `config show`
- [x] 0.10 Tests: config clamp, network/mainnet gate, CLI `--help`/`exec once`/stubs — 18 passing

✅ Phase 0 complete — `hl --help`, paper `exec once`, `config show` all work; lazy-dep constraint verified (no anthropic/hyperliquid/eth_account at import time)

## Phase 1: Manual trade (Mode A)
Gate: place + manage a real **testnet** order end-to-end. ⏳ deferred — code complete; awaiting a funded testnet agent wallet (user's choice: build on paper + mocks).

- [x] 1.1 `accounts/store.py` — SQLite account store (`~/.hyperliquid-cli/accounts.db`): alias, address, network, type, key_ref; per-network default
- [x] 1.2 `accounts/keystore.py` — agent key per-account `0600` file, never logged; `agent_address` derivation lazy via eth_account
- [x] 1.3 `account` commands: add | ls | set-default | remove | positions | orders | balances | portfolio
- [x] 1.4 `exchange/marks.py` — marks + book via public `/info` over **httpx** (no SDK), TTL cache. *Deviation: reads use httpx not the SDK Info, so paper stays keyless/SDK-free (httpx moved to core deps).*
- [x] 1.5 `exchange/hyperliquid.py` — live testnet+mainnet backend; SDK + eth_account lazy-imported; writes blocked on read-only accounts
- [x] 1.6 `trade` commands (Mode A: allowed-coin + notional + leverage caps + exchange validation): order limit|market|stop-loss|take-profit, cancel, cancel-all, set-leverage
- [x] 1.7 `markets` ls|prices; `asset` price|book
- [x] 1.8 `executor/monitor.py` — `position_health` (read-only view; automated SL/TP action deferred to Phase 2/5)
- [x] 1.9 Watch modes (`-w`) for positions/orders/asset book/price. *Deviation: poll-based `rich.Live` refresh, not native websocket; SDK `Info.subscribe` is a later refinement, call sites unchanged.*
- [x] 1.10 `exec status` | `report`
- [x] 1.11 Tests: accounts/keystore, marks (mocked httpx), live-read + order-response parsing, CLI (account/trade/caps) — 44 passing, keyless-safe

✅ Phase 1 code complete (gate deferred) — verified keyless: full suite + paper + live public reads run with **no** hyperliquid/eth_account installed. Live testnet order pending a funded agent wallet.

## Phase 2: Executor — deterministic
Gate: candidates → paper fills; fully deterministic; restart-safe. ✅ passed

- [x] 2.1 `state/store.py` — network-scoped SQLite: intake stream, meta (HWM, realized), idempotency, decision_log, paper_positions
- [x] 2.2 `executor/intake.py` — `make_candidate`/`parse_batch` (side inferred from levels, pair/reason aliases) + `exec propose` single + `--file` batch; HWM via `pull_new`
- [x] 2.3 `executor/gate.py` — deterministic gate, first-failure wins (decision → breaker → daily-loss → freshness → allowed-coin → regime → level sanity → R:R → one-per-coin → max-concurrent → sizing+caps → conviction clamp)
- [x] 2.4 Sizing: fixed-fractional `risk_per_trade_pct × equity ÷ stop_distance`, conviction-scaled within [floor,ceil], clamped by notional + leverage caps
- [x] 2.5 `executor/execute.py` — `fire` records idempotency key **before** placing (crash → skip, not double-fire)
- [x] 2.6 `safety/breaker.py` — kill switch (persisted) + daily-loss-limit (day-start equity drawdown, resets on date rollover)
- [x] 2.7 `exec propose | once | run | breaker` wired; deterministic decision stub (`executor/decision.py`, act/now/conv=1.0); `runner.run_once` full pass; dry-run is side-effect-free
- [x] 2.8 Restart never double-fires — HWM advances per processed candidate + idempotency key; paper book persists across instances
- [x] 2.9 Tests: gate/sizing (20), state/HWM/idempotency, paper fills + equity, breaker, end-to-end (restart, dry-run, one-per-coin, max-concurrent, breaker) — 86 passing

✅ Phase 2 complete — verified end-to-end on paper: propose (single+batch) → `exec once` fires → persistent book + equity/uPnL; re-run sees nothing (HWM); breaker halts fires. Still keyless-safe.

## Phase 3: LLM decision
Gate: shadow runs produce sane, fully-logged decisions on paper/testnet.

- [x] 3.1 `executor/enrich.py` — marks, portfolio + equity + P&L, recent decisions, tunable surface. `regime=None` (no price-history feed yet; gate skips when None — chosen over fabricating a signal)
- [x] 3.2 `executor/decision.py` — LLM decision (lazy `anthropic`), claude-sonnet-4-6, forced strict tool `submit_decision`, `decision_temperature`
- [x] 3.3 `validate_decision` validator + clamp; bad enum/missing/non-numeric → drop+tally, out-of-range conviction → clamp; never guesses
- [x] 3.4 Decision log carries enriched context + decision + gate + fill (resolved-outcome cohorting is Phase 4.1 — this is its substrate)
- [x] 3.5 `exec shadow` — `run_once(fire_enabled=False)`: decide + gate + log, fire nothing
- [x] 3.6 Tests: `test_decision.py` validator/clamp + mocked client; executor mechanics inject a deterministic `decide_fn` (LLM never hit in tests)

Gate verification (real LLM call on paper/testnet shadow) is deferred pending an `ANTHROPIC_API_KEY`, mirroring Phase 1's deferred live testnet order. Pipeline is code-complete + fully covered by mocked tests (104 pass).

## Phase 4: Self-tuning (out-of-path, propose→approve)
Gate: tuner proposes from logged outcomes; `promote` works; clamps hold.

- [ ] 4.1 `tuner/stats.py` — resolved-trade cohorts; sample-gated (no cohort ⇒ model not called)
- [ ] 4.2 `tuner/config_tuner.py` — propose tunable-surface edits (claude-opus-4-8) → `proposed_config.json`
- [ ] 4.3 `tuner/prompt_tuner.py` — propose decision-prompt refinements → `proposed_prompt.md`
- [ ] 4.4 `tuner/promote.py` — proposed → active; promotion history/audit trail
- [ ] 4.5 `tune run | diff | promote | history`
- [ ] 4.6 Tests: cohort gating, promote flow, clamps hold on promoted config

## Phase 5: Mainnet hardening
Gate: testnet/shadow expectancy clears → controlled mainnet at tiny caps.

- [ ] 5.1 Native exchange-side SL/TP trigger orders placed at entry time (reuse trade trigger path)
- [ ] 5.2 Mainnet env gate + typed confirmation (HL_ENABLE_MAINNET=1 + --network mainnet + confirm; -y skips prompt but still needs env flag)
- [ ] 5.3 Graduation checklist (N days / N resolved trades positive expectancy, surfaced in report)
- [ ] 5.4 Key handling review — agent wallets default, no keys in logs or decision context
- [ ] 5.5 Alerting on fires, rejects, breaker trips, loss-limit hits
- [ ] 5.6 Tests: mainnet gate (all three conditions required)
