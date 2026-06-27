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
Gate: place + manage a real **testnet** order end-to-end.

- [ ] 1.1 `accounts/store.py` — SQLite account store (`~/.hyperliquid-cli/accounts.db`): alias, address, network, type, key ref
- [ ] 1.2 `accounts/keystore.py` — agent ("API") wallet keys, locked file perms, never logged
- [ ] 1.3 `account` commands: add | ls | set-default | remove | positions | orders | balances | portfolio
- [ ] 1.4 `exchange/marks.py` — marks feed + cache (public mainnet marks for paper)
- [ ] 1.5 `exchange/hyperliquid.py` — testnet+mainnet via hyperliquid-python-sdk (lazy import) + eth-account signing
- [ ] 1.6 `trade` commands (Mode A: hard caps + exchange validation only): order limit|market|stop-loss|take-profit, cancel, cancel-all, set-leverage
- [ ] 1.7 `markets` ls|prices; `asset` price|book
- [ ] 1.8 `executor/monitor.py` — monitor SL/TP/expiry
- [ ] 1.9 Watch modes (`-w`) via websocket-client for positions/orders/book
- [ ] 1.10 `exec status` | `report`
- [ ] 1.11 Tests: order command validation, marks cache, monitor

## Phase 2: Executor — deterministic
Gate: candidates → paper fills; fully deterministic; restart-safe.

- [ ] 2.1 `state/` SQLite schema: book, idempotency, high-water mark, decision log
- [ ] 2.2 `executor/intake.py` — intake queue + `exec propose` (single + JSON/file batch); high-water mark
- [ ] 2.3 `executor/gate.py` — deterministic risk gate pipeline, first-failure wins (schema → kill switch → daily-loss → freshness → allowed-coin → regime → level sanity → R:R floor → one-per-coin → max-concurrent → sizing + caps → conviction clamp)
- [ ] 2.4 Sizing: fixed-fractional `risk_per_trade_pct × equity ÷ stop_distance`, clamped by notional + leverage caps
- [ ] 2.5 `executor/execute.py` — execute approved orders (idempotent)
- [ ] 2.6 `safety/breaker.py` — kill switch, daily-loss-limit, loss limits
- [ ] 2.7 `exec propose | once | run | breaker` wired (deterministic decision stub, no LLM yet)
- [ ] 2.8 Restart never double-fires (idempotency + high-water mark)
- [ ] 2.9 Tests: gate/sizing (highest-risk), idempotency + HWM, breaker, restart-safety

## Phase 3: LLM decision
Gate: shadow runs produce sane, fully-logged decisions on paper/testnet.

- [ ] 3.1 `executor/enrich.py` — marks, portfolio + equity + P&L, regime signal, rolling recent resolved outcomes, tunable config
- [ ] 3.2 `executor/decision.py` — LLM decision (lazy `anthropic`), claude-sonnet-4-6, structured JSON, low temp
- [ ] 3.3 LLM-output validator + clamp; failed schema → drop + tally, never guess
- [ ] 3.4 Decision log with full input context + fill + outcome
- [ ] 3.5 `exec shadow` — decide + log, fire nothing
- [ ] 3.6 Tests: validator/clamp with mocked LLM (deterministic fixtures)

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
