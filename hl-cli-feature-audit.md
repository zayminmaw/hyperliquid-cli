# hl-cli Executor Feature Audit — Vibe-Trading × thirdeye-core × hyperliquid-cli

**Date:** 2026-07-18
**Scope:** Which executor-side patterns from [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)
are worth porting into `hyperliquid-cli` (the *executor*), given that thirdeye-core (the *brain*) has
already absorbed the signal/strategy/reflection patterns and hl-cli already ships a deterministic gate,
sentry, and self-tuner.
**Method:** Full read of Vibe's `agent/src/live/` + `agent/backtest/` + `shadow_account/`; thirdeye-core's
output boundary + overlap grep; hl-cli's gate, tuner, sentry, config, and ledger. Sources are cited by
`file:line` so every claim is checkable.

---

## 1. The three-way boundary (why this audit is narrow)

**hl-cli consumes** thirdeye-core's `SuggestionSignal` (`thirdeye-core/src/schemas.py:287`):
`{direction, entry_price, stop_loss, take_profit, confidence(+llm/ml/gap), market_regime,
volatility_regime, regime_changed, wait/wait_source}`. Critically, the signal carries **no liquidity /
depth / market-cap / sizing / correlation data** — so any liquidity-aware executor feature must fetch
depth itself (hl-cli already reads the book via `hlcli/exchange/marks.py`); it cannot come from the signal.

**Already borrowed into thirdeye-core (do not re-recommend):** Vibe's `agent/backtest/validation.py`
random-control gate. But thirdeye-core's `validation.py:26` explicitly notes *"NOT Sharpe: the corpus is a
suggestion stream, not an equity curve."* → **equity-curve execution metrics are deliberately unclaimed
by the brain — they are pure executor territory.** No backtest engine, portfolio optimizer, or risk-parity
exists in thirdeye-core.

**Already present in hl-cli (do not re-recommend):** the `.env` hard-caps ↔ clampable tunable-surface split
(= Vibe's mandate consent model), the `exec breaker` kill switch (= Vibe's `halt.py`), per-trade
slippage-capped IOC entries, one-per-coin + max-concurrent, native exchange SL/TP, paper→shadow→testnet
validation ladder, an entry-decision self-tuner (`hlcli/tuner/`), and the sentry in-trade manager with a
6b shadow log.

---

## 2. What hl-cli's gate does *not* bound today

`hlcli/executor/gate.py` runs: skip/wait → breaker → daily-**loss** → freshness → allowed-coin → regime →
level coherence → R:R floor → mark-sanity → one-per-coin → max-concurrent **count** → per-trade sizing
(notional + per-order leverage) → min-notional. `gate.py:137-138` states plainly that aggregate exposure is
bounded **only** by `count × per-trade cap` — there is no account-wide ceiling. `Caps`
(`hlcli/core/config.py`) has no gross-exposure, no daily-**count**, and no liquidity fields. `exec report`
(`hlcli/tuner/stats.py`) computes PF / R-multiple / win-rate / conviction-calibration only — no
risk-adjusted or drawdown metrics, and no realized-slippage record.

---

## 3. Candidate features (ranked by ROI-to-effort, highest first)

ROI notation: **impact** (on execution quality / risk-adjusted return) over **effort** (S/M/L). Effort is
honest to *this* codebase — several items are cheap because `run_once` already gathers the inputs.

### 🥇 A — Account-wide gross-exposure + gross-leverage cap  ·  Impact High / Effort **S–M**
One-liner: a gate check that sums *current* open-position notional and rejects a fire that would push total
book exposure (or gross leverage = exposure ÷ equity) past a hard cap.
- **Source:** `enforcement.check_mandate` steps 5–6 (`agent/src/live/enforcement.py:544-578`) —
  `_post_trade_gross_exposure` + `max_total_exposure_usd` + gross `max_leverage`.
- **Why hl-cli specifically:** hl caps per-trade notional and per-order leverage and the *count* of
  positions, but nothing bounds the sum. With `max_concurrent_positions=3`, `max_notional_per_trade=1000`,
  `max_leverage=3`, three simultaneous fires can put 3× the intended leverage on the book — each trade legal,
  the portfolio over-exposed. This is the single clearest risk gap.
- **Overlap check:** not in hl-cli (`gate.py:137-138` is the confession); not in thirdeye-core.
- **Effort is low because** `run_once` already holds `equity`, `positions`, and `marks`
  (`hlcli/executor/runner.py:126-128`); current gross notional is one `sum(|size|×mark)`. Add a
  `gross_notional`/positions field to `GateContext`, `HL_MAX_TOTAL_EXPOSURE_USD` (+ optional
  `HL_MAX_GROSS_LEVERAGE`) to `Caps`, and one short-circuit check after max-concurrent.
- **Dependencies:** none — reads hl-cli's own book. Nothing new from thirdeye-core.
- **Sentry tie-in:** the same cap should govern sentry **ADD** (risk-increasing) across the whole book,
  not just the per-position `sentry_max_adds_per_position`.

### 🥈 B — Daily new-entry count cap (overtrading breaker)  ·  Impact Med–High / Effort **S**
One-liner: a hard ceiling on *new entries per UTC day*, distinct from the daily-loss limit.
- **Source:** `agent/src/live/daily_count.py` (atomic per-UTC-day counter) + `check_mandate` step 7
  (`enforcement.py:580-588`, `max_trades_per_day`).
- **Why hl-cli specifically:** hl's only frequency-ish control is the daily-**loss** breaker — a run of
  small scratches or a misbehaving intake stream can fire many times without ever tripping it. A count cap
  is a cheap, independent circuit breaker against overtrading and runaway intake.
- **Overlap check:** not in hl-cli (sentry has `sentry_max_actions_per_position_per_day`, but that throttles
  *management* churn, not *new entries*); not in thirdeye-core.
- **Effort is low because** `hlcli/safety/breaker.py` already implements the exact day-rollover pattern
  (`meta _DAY_KEY`, `persist=` for dry-run). Reuse it: a `_COUNT_KEY` in meta, increment on fire, reset on
  rollover, `HL_MAX_TRADES_PER_DAY` cap, one gate check.
- **Dependencies:** none.

### 🥉 C — Execution equity-curve metrics in `exec report` (+ D folded in)  ·  Impact Med–High / Effort **M**
One-liner: add Sharpe / Sortino / Calmar / max-drawdown / avg-exposure / avg-slippage to the report, on top
of the existing PF/R/win-rate.
- **Source:** `agent/backtest/metrics.py::calc_metrics` — annualised return, vol, Sharpe, Sortino, max
  drawdown, turnover, benchmark info-ratio, with the small-sample (ddof=1) and total-wipeout guards already
  worked out (`metrics.py:262-312`).
- **Why hl-cli specifically:** the graduation gate and tuner promote decisions ride on `exec report`'s
  evidence. Win-rate + PF alone hide path risk — a book can have a good PF and a brutal drawdown. This is
  *the* thing thirdeye-core deliberately does **not** compute (`validation.py:26`: suggestion stream ≠ equity
  curve), so it belongs to the executor and to no one else.
- **Overlap check:** not in hl-cli (`stats.py` = cohorts + calibration + PF/R/win-rate); explicitly out of
  scope for thirdeye-core.
- **Effort:** the trades ledger already carries `entry, exit_price, realized, r_multiple, closed_at, status`
  (`hlcli/state/store.py:62-63`) and `resolved_trades()` returns closed rows → an equity/returns series is
  derivable with no schema change. Port the metric math from `metrics.py`.
- **Sub-item D (realized-slippage record) folds in here** · Effort S–M: hl *caps* entry slippage
  (`hlcli/exchange/hyperliquid.py`) but never *records* realized slip. Add one additive column
  (`fill_price`/`mark_at_fire`) set at fire, and surface `avg_slip_pct` in the report. `resolve.py:83`
  already flags wanting graduation "honest about slippage" — this closes it. Independent value, cheap when
  done alongside C.
- **Dependencies:** none (benchmark info-ratio optional; a BTC-hold benchmark is available from marks).

### 4️⃣ F — Runner liveness watchdog / dead-loop reaper  ·  Impact High / Effort **M**  ·  🎯 SENTRY
One-liner: an externally-detectable heartbeat so a hard-killed sentry/agent loop is *noticed* and
reconciled, instead of silently leaving open positions unmanaged.
- **Source:** `agent/src/live/runtime/liveness.py` — atomic per-tick heartbeat file, `is_runner_alive`
  staleness read, `reap_stale`, and the discipline "a runner that looks dead is never re-spawned blindly —
  reconciliation runs first" (`liveness.py:11-13`).
- **Why hl-cli specifically:** hl's heartbeat is **self-reported** — `supervisor.py:118-120` emits an
  `agent_heartbeat` alert and marks itself alive **even on a failing tick** (`supervisor.py:151-153`). A
  SIGKILL / host crash emits *nothing*; `agent status` reads a stale `LAST_TICK` but **nothing acts on
  staleness**. For an autonomous in-trade manager this is the real hole: if the sentry loop dies, the
  trail/ratchet/scale-out stop and only the resting native SL/TP still protect the position. A separate
  reader (a reaper, or `agent status`) that treats a stale atomic heartbeat as *dead → alert →
  reconcile-before-respawn* closes it.
- **Overlap check:** partial — hl has a self-report heartbeat but no external staleness detection and no
  fail-closed reconcile-before-respawn; not in thirdeye-core.
- **Effort:** add atomic per-tick heartbeat writes (Vibe's temp-file + `os.replace` shape) and a staleness
  check wired into `agent status` / a reaper; hook the "dead" verdict to the existing alerter + reconciliation.
- **Dependencies:** none.

### 5️⃣ J — Sentry self-tuner, delta-PnL attributed  ·  Impact Med–High / Effort **M–L**  ·  🎯 SENTRY
One-liner: extend the propose→approve tuner to *trade management* — score the sentry-shadow log on money,
not just agreement, and propose bounded `TrailConfig` / sentry-prompt changes.
- **Source:** `agent/src/shadow_account/backtester.py` — arithmetic-only delta-PnL attribution:
  `delta_pnl = shadow_pnl − real_pnl` bucketed into `early_exit_pnl / late_exit_pnl / overtrading_pnl /
  missed_signals_pnl / noise_trades_pnl` + counterfactual trades (`backtester.py:1, 153-201`).
- **Why hl-cli specifically:** hl's tuner edits **entry only** — "the regime gate, risk-per-trade, the
  conviction→size mapping, decision-prompt parameters" (`config_tuner` docstring, `config_tuner.py:4`). Yet
  (1) sentry's management params (`TrailConfig`: style, `trail_start_r`, `breakeven_trigger_r`,
  `scale_out_r/fraction`) are **already in the clampable tunable surface** (`config_schema.py:55-70`,
  bounded in `clamp()`), and (2) the sentry-shadow pass **already logs every LLM management proposal paired
  with the 6a deterministic baseline** (`hlcli/sentry/shadow.py:104-107`). Nothing consumes either. Worse,
  the current 6b scoreboard is **agreement-only** (`agreed` = "did the LLM match the rules",
  `shadow.py:44,114`) — there is **no `delta_pnl` anywhere in hl-cli**, so you can see how often the LLM
  agrees but not whether *following* it would have banked more. Porting Vibe's attribution turns the existing
  log into a promotable signal, and a sentry tuner closes the loop with the same out-of-path,
  human-`promote` discipline as the entry tuners.
- **Ownership boundary:** *strategy/signal* self-improvement is brain-side (thirdeye-core owns it, excluded).
  *Trade-management* self-improvement (managing a position that's already open) is executor-side and
  **unclaimed** — this is the sentry slice.
- **Overlap check:** not in hl-cli (entry-only tuner; agreement-only sentry scoreboard; TrailConfig clampable
  but untouched by any tuner); the strategy-side loop is thirdeye-core's, this is the management-side.
- **Effort:** `sentry_log(ts,trade_id,coin,action,details)` + `sentry_log_for_trade` already join
  proposals→trade outcome (`store.py:71,344`), and trades carry the eventual `realized/r_multiple/status`
  (incl. `scaled`). Build the attribution math, a sentry cohort, and a `TrailConfig`/prompt proposer;
  reuse the `promote` pipeline.
- **Dependencies:** none external.

### 6️⃣ G — Formal reconciliation report + halt-on-divergence  ·  Impact Med–High / Effort **M** (partial overlap)
One-liner: consolidate the piecemeal reconciliation into a single order+position diff that produces
`is_safe` / `requires_halt`, and trip the breaker on unsafe divergence.
- **Source:** `agent/src/live/runtime/reconcile.py` — `ReconcileReport` with `is_safe`/`requires_halt`
  (`reconcile.py:140-190`), diffing recorded vs broker orders and positions.
- **Why hl-cli specifically:** hl already reconciles piecemeal — `resolve_open_trades` handles
  vanished-position reconciliation + trigger cleanup, and `run_once` runs an unmanaged-position
  alert/adopt (`runner.py:138-147`) — but there is no structured diff that can *force a halt* when the book
  and the ledger disagree beyond a safe delta (e.g. an unexpected position, or a size mismatch). On mainnet
  restart, a formal safe/requires-halt verdict is a meaningful backstop.
- **Overlap check:** partial — the pieces exist (`resolve.py`, `adopt.py`, `_alert_unmanaged`); missing is
  the unified report + halt-on-divergence. Scope as consolidation, not a rebuild.
- **Dependencies:** none.

### 7️⃣ E — Liquidity / depth floor  ·  Impact Low–Med / Effort **M** (conditional)
One-liner: reject (or downsize) a fire whose notional is large relative to current book depth / OI / 24h
volume.
- **Source:** `enforcement._check_universe_floors` (`enforcement.py:614-648`) — ADV + market-cap floors,
  fail-closed on missing data.
- **Why hl-cli specifically:** hl's only size floor is the $10 exchange minimum; there is no *ceiling* tied
  to how much the market can absorb. Low value while `ALLOWED_COINS` is BTC/ETH majors; rises sharply if the
  allowed universe ever includes thin alts.
- **Overlap check:** not in hl-cli; the "is this coin tradeable" *selection* is brain-side — hl-cli's slice
  is only "is my *size* too big for the book right now."
- **Dependencies:** hl-cli fetches depth itself (`marks.py` already reads the book); **not** available from
  the signal.
- **Recommendation:** defer until the allowed universe broadens.

### 8️⃣ H — Event-driven sentry re-evaluation  ·  Impact Low–Med / Effort **M**  ·  🎯 SENTRY (optimization)
One-liner: wake sentry when the mark crosses an R-threshold / nears the stop, instead of only on the fixed
`sentry_eval_interval_minutes` clock.
- **Source:** `agent/src/live/runtime/triggers.py::Trigger.event(predicate)` (`triggers.py:195`). Note
  `triggers.py`'s market-hours logic is dead weight for 24/7 crypto — only the event-predicate concept ports.
- **Why hl-cli specifically:** sentry evaluates "at most every `sentry_eval_interval_minutes`"
  (`hlcli/sentry/live.py:111`); a fast adverse move between ticks isn't acted on by the *dynamic* manager.
- **Overlap check:** not in hl-cli — but **largely backstopped already**: hl places native exchange SL/TP,
  so between-tick *protection* exists at the exchange. This would tighten *ratchet/scale-out* latency, an
  optimization rather than a safety fix.
- **Dependencies:** none.
- **Recommendation:** lowest of the sentry items; only after F and J.

---

## 4. Ranked shortlist (build in this order)

1. **A — gross-exposure/leverage cap.** The one unambiguous risk hole (`gate.py:137-138`), and cheap because
   `run_once` already has equity+positions+marks. Highest impact-to-effort. Also the prerequisite for a
   book-wide sentry-ADD bound.
2. **B — daily new-entry count cap.** Near-free (reuses `breaker.py`'s rollover), independent circuit breaker
   against overtrading/intake runaway. Ship alongside A.
3. **C (+D) — execution metrics + realized slippage in `exec report`.** Makes the graduation/promote evidence
   honest (drawdown + risk-adjusted return + slippage); the ledger already holds the data and thirdeye-core
   deliberately won't compute it. Directly improves every downstream go/no-go decision.
4. **F — liveness watchdog.** The top *safety* item for autonomous operation and the strongest sentry answer:
   today a hard-killed sentry silently stops managing positions. Medium effort, high consequence.
5. **J — sentry self-tuner (delta-PnL attributed).** The best *sentry self-improvement* fit: the shadow log
   and clampable TrailConfig already exist and are unused for tuning; port Vibe's delta-PnL attribution to
   score them on money and close the propose→approve loop. Do after C (shares the metrics/attribution math)
   and F (a manager you tune should first be reliably alive).

*(G and E are worthwhile but lower — G is a consolidation with partial overlap; E is conditional on a wider
allowed universe. H is an optimization behind the native-stop backstop.)*

### Sentry lens (the two questions that drove this audit)
- **"Can Vibe help sentry?"** → **F (liveness)** first — stop a dead manager from going unnoticed. **H
  (event-eval)** later — tighter ratchet/scale-out latency, but native stops already cover core protection.
  **A** also bounds sentry ADD book-wide.
- **"Can the self-improving thing help sentry?"** → **J**. Strategy self-improvement stays brain-side, but
  *trade-management* self-improvement is executor-side and unclaimed; hl already logs the training data and
  exposes the clampable knobs — it just never scores them on PnL or feeds a proposal.

---

## 5. Do NOT build (tempting, low value for a solo Hyperliquid executor CLI)

- **Portfolio optimizers** (`agent/backtest/optimizers/`: risk-parity, equal-vol, max-diversification,
  mean-variance, turnover-aware). Portfolio *construction* is brain-side; hl acts on discrete, one-per-coin
  candidates. A gross-exposure cap (A) is the right amount of portfolio awareness here.
- **Multi-market backtest engine + ~40 data loaders** (`agent/backtest/`). hl is Hyperliquid-only and its
  paper→shadow→testnet ladder is the forward-test equivalent. *Ambiguous sub-case:* a **narrow, deterministic
  replay of the gate+sizing+monitor against historical candles** would be a legitimate executor regression
  harness — but it's L effort for a solo CLI and the shadow ladder already covers the intent; skip unless a
  gate/sizing regression bug ever justifies it.
- **Market-hours triggers** (`triggers.py` market logic). Crypto perps are 24/7.
- **Mandate consent state machine** (`agent/src/live/mandate/`). hl already has the equivalent: `.env`
  hard-caps ↔ clampable tunable surface + `tune promote`.
- **Correlation-aware exposure / cross-asset benchmark suite** (`agent/backtest/correlation.py`). Ambiguous
  ownership, leaning brain-side; not worth it for a one-per-coin executor.
- **Preemptive flatten-all** (`agent/src/live/runtime/flatten.py`). Conflicts with hl's deliberate breaker
  design (the breaker halts *new* fires but keeps managing open positions). At most an opt-in
  `exec breaker --flatten`, never the default.

---

## 6. Cross-cutting notes for whoever implements this

- Everything in the shortlist is **order-path or evidence-path**, so per `CLAUDE.md` each must clear the
  7-point `docs/evidence-gate.md` checklist (A/B/E especially — they are new gate checks and must be
  first-failure, fail-closed, and clamped).
- A, B, E extend the **hard-cap** surface (`.env` / `Caps`), not the tunable surface — a risk ceiling the
  LLM and tuner can never move.
- C/D/J extend the **evidence/tuning** surface; keep C's metrics excluding `aborted`/`abort_failed`/adopted
  rows, consistent with the existing evidence-hygiene rules.
- None of the shortlist needs anything new from thirdeye-core's output — the signal boundary is already
  sufficient.
