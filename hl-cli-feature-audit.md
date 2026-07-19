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

---

# Wave 2 — Live-run findings (first testnet drill, 2026-07-19) + Hyperliquid unified accounts

**Date added:** 2026-07-19
**Provenance:** Wave 1 (§§1–6, items A–J) was *static analysis of the Vibe-Trading source, written before
hl-cli had ever placed a live order.* Wave 2 is grounded in the **first real testnet drill** — the first
time hl-cli actually signed, filled, protected, resolved, and adopted real orders — and in Hyperliquid's
**unified-account** rollout (now the default for new accounts). Where Wave 1 could only reason about code,
Wave 2 reports what the exchange **actually returned**. New items are lettered **K onward** to stay distinct
from A–J. Every claim cites a verified `file:line` or an HL doc; anything not yet verifiable against a live
position is marked **MUST-VERIFY**, never asserted.

> ## ⚠️ BINDING RULE FOR WAVE 2 — DO NOT GUESS. LOOK IT UP.
>
> Every item here touches money, the exchange wire, or the evidence the graduation/tuner decisions ride on.
> **Do not infer an API field, a fee formula, a funding cadence, a sign convention, or a response shape from
> memory or from another exchange.** Before writing code for any item:
>
> 1. **Read the Hyperliquid docs for the exact endpoint/field** — [Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint),
>    [Perpetuals info](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals),
>    [Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees),
>    [Funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding),
>    [Margining](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining),
>    [Account abstraction modes](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/account-abstraction-modes).
> 2. **Confirm the field on a *live* response** — capture the real JSON from a funded account with an open
>    position and a completed fill (testnet is fine) and assert the shape in a fixture, exactly as the
>    `order_status_by_cloid` recovery path already demands (`AGENT-CONTEXT.md` gotcha).
> 3. **Confirm sign conventions empirically** — is `cumFunding` a cost (positive = paid) or a credit? Is
>    `closedPnl` already net of `fee`? Do **not** assume; open a tiny position, hold it across a funding
>    hour, close it, and read back the ledger.
> 4. **Cite the doc URL + the live-capture fixture in the PR.** A number without a citation is a guess.
>
> The F2 unified-account equity bug (below) is the cautionary tale: it existed *because* an earlier version
> read a field (`marginSummary.accountValue`) that was correct on the old account model and silently wrong
> under unified accounts. The only reason it was caught is that the drill read the **live** response. Guessing
> would have shipped it to mainnet.

## 7. What the first live run exposed (context for Wave 2)

The drill surfaced one shipped bug and three structural gaps, all verified in code this session:

- **F2 (fixed) — `equity()` was unified-account-blind.** It read `clearinghouseState.marginSummary.accountValue`,
  which under a **unified account** is only *committed position margin* (~0 when flat) — the tradeable
  collateral lives in the spot clearinghouse. Live proof: 998 USDC present, `equity()` returned `0.0` →
  Mode-B sizing and the `equity>0` gate would have blocked every fire on testnet. Fixed to detect
  `userAbstraction=="unifiedAccount"` and read spot USDC + Σ open-position uPnL (`hlcli/exchange/hyperliquid.py`).
  **This proves the whole class of "field means something different under unified accounts" risk is real** —
  Wave 2's K/M items exist because the same class of gap remains in P&L and margin accounting.
- **Fees: absent.** `grep -rniE 'fee|taker|maker|commission' hlcli/` → nothing in any P&L path. → item **K**.
- **Funding: absent.** `grep -rniE 'funding' hlcli/` → nothing anywhere. → items **K** (accounting) + **N** (signal).
- **Externally-closed positions book the *mark*, not the fill.** `hlcli/executor/resolve.py:186`:
  `return "closed", mark` — a manual flatten / liquidation / any close hl-cli didn't place is valued at the
  current mark. Live proof: a flatten filled at 64699 but the ledger booked exit 64703.5. → item **L**.
- **Margin health: absent.** `grep -rniE 'liquidation|maintenanceMargin|withdrawable|buying_power' hlcli/` →
  nothing. No pre-fire or in-trade distance-to-liquidation check — newly consequential because unified margin
  lets a perp loss consume spot collateral. → item **M**.

## 8. Wave-2 candidate features

### 🥇 K — Fee- and funding-honest realized P&L  ·  Impact **High** / Effort **M**  ·  🎯 EVIDENCE
One-liner: make every realized-P&L number the ledger stores (`trades.realized`, `r_multiple`) net of the
**taker fees actually paid** and the **funding actually accrued**, so graduation expectancy and the tuner
cohorts stop being systematically optimistic.
- **Why hl-cli specifically:** graduation (`safety/graduation.assess`) and both tuners promote on `exec report`
  expectancy. Today that expectancy ignores two real costs:
  - **Fees:** HL perps are **0.015% maker / 0.045% taker** at the entry tier ([Fees docs](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)).
    hl-cli entries are slippage-capped **IOC takers** (`hlcli/exchange/hyperliquid.py`, audit X-1) → ~**0.045% in + 0.045% out ≈ 0.09% round-trip**, paid on notional. For a trade risking 0.5% of equity at 1R, that fee is a meaningful fraction of the edge and is currently invisible.
  - **Funding:** charged/paid **hourly** on open positions ([Funding docs](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)); a multi-hour hold silently accrues carry the ledger never sees.
- **Overlap check:** not in hl-cli (grep-verified empty); explicitly executor territory (thirdeye-core owns the
  *suggestion stream*, not the *equity curve* — `validation.py:26`). Complements Wave-1 D (realized slippage) —
  D, K together make "realized R" mean realized-net-of-costs.
- **The right data source (verify before coding):** HL already computes both for you — do **not** re-derive
  fees from a rate table if the exchange reports the actual charge:
  - **Per-fill fee + realized:** `Info.user_fills_by_time` / `user_fills` returns per-fill `fee` and
    `closedPnl`. **MUST-VERIFY:** confirm the exact field names and **whether `closedPnl` is already net of
    `fee`** (HL fills are documented to carry both; capture a real fill and check — do not assume the sign or
    the netting). Doc: [Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).
  - **Funding accrued on an open position:** `clearinghouseState` position object carries
    `cumFunding {allTime, sinceOpen, sinceChange}` (field names **confirmed** against the Perpetuals info doc
    this session). `sinceOpen` is the funding accrued since the position opened. **MUST-VERIFY:** the **sign
    convention** (is positive = paid-out cost or received-credit?) on a live position held across a funding hour.
  - **Closed-position funding ledger:** `Info.user_funding_history` / `userFunding` deltas carry
    `{coin, szi, fundingRate, usdc, nSamples}` (**confirmed** field names). `usdc` is the settled amount per event.
- **Effort:** additive ledger columns (`fee_paid`, `funding_paid`) set at resolve time from the fill/funding
  reads; fold into `_pnl` (`resolve.py:189`) and the report. Paper mode must **model** the taker fee (a
  constant from a `HL_TAKER_FEE_PCT`-style hard cap, or `Info.user_fees` for the live tier) so paper→testnet
  expectancy stays comparable — **MUST-VERIFY** the paper fee model matches how HL actually charges (notional × rate).
- **Dependencies:** none external beyond the reads above. Evidence-hygiene: keep excluding `aborted`/`abort_failed`/adopted.
- **MUST-VERIFY checklist:** fill `fee`/`closedPnl` field names + netting + sign; `cumFunding.sinceOpen` sign;
  `user_fees` shape for the live tier; whether HL charges fees on the **reduce-only close** too (it does — both legs).

### 🥈 L — Book the real exit fill for externally-closed positions  ·  Impact **Med–High** / Effort **S–M**  ·  🎯 EVIDENCE
One-liner: when a position hl-cli was tracking disappears from the book (manual flatten, native-stop hit that
the resolver only *infers*, liquidation), resolve it at the **actual exit fill price**, not the current mark.
- **Why hl-cli specifically:** `resolve.py:186` returns `("closed", mark)` for any externally-closed position,
  and `resolve.py:79` books an SL/TP hit at the **trigger level** (`level_price`) rather than the fill — both
  are estimates. Live proof from the drill: flatten filled 64699, ledger booked 64703.5 (a 4.5-point,
  ~0.007% error that compounds across the sample and biases graduation/tuner R). Only executor-*placed* closes
  use the true `result.avg_price` (`resolve.py:87`).
- **Overlap check:** partial — the resolve path exists; what's missing is a lookup of the real exit. Composes
  with Wave-1 G (reconciliation): the same fill read that fixes the price also tells G whether the disappearance
  was expected.
- **The right data source (verify before coding):** `Info.user_fills_by_time(start=position_open_ts)` filtered
  to the coin + reduce/again the closing `dir`; the closing fill(s)' `px` (size-weighted if partial) is the
  true exit, and its `closedPnl` is HL's own realized number (ties into K). **MUST-VERIFY** the fill `dir`
  vocabulary (e.g. "Close Long" / "Close Short") and that a **liquidation** shows as a fill here (it should),
  so a liquidated position books its real liquidation price, not the mark.
- **Effort:** one info read in the vanished-position branch of `resolve.py`; fall back to the mark **only** if
  no matching fill is found (fail-honest: annotate the row as mark-estimated so evidence can down-weight it).
- **Dependencies:** none. **MUST-VERIFY:** fill dir vocabulary; partial-close aggregation; liquidation-as-fill.

### 🥉 M — Margin-health / liquidation-distance guard  ·  Impact **High** / Effort **M**  ·  🎯 SAFETY
One-liner: a pre-fire gate check **and** a sentry read that reject/flag when a position would sit too close to
its liquidation price or push cross-maintenance-margin past a safe fraction of equity.
- **Why hl-cli specifically:** hl has **no** liquidation or maintenance-margin awareness (grep-verified empty).
  Under **unified margin** the spot↔perp firewall is gone ([Account abstraction modes](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/account-abstraction-modes)) —
  "a severe loss on a leveraged perp can consume capital you intended to keep in spot." The existing per-order
  `max_leverage` cap bounds a single order's *nominal* leverage, not the **cross-margin liquidation proximity**
  of the resulting book. For an autonomous manager this is a real, unbounded tail.
- **Overlap check:** not in hl-cli; related to Wave-1 A (gross exposure) but distinct — A bounds *notional*, M
  bounds *distance to liquidation* (a 3× book can be safe with deep margin; a 2× book near maintenance is not).
- **The right data source (field names CONFIRMED this session):** `clearinghouseState` gives, per position,
  `liquidationPx`, `marginUsed`, `maxLeverage`, `positionValue`; and top-level `crossMaintenanceMarginUsed`
  and `withdrawable`. A pre-fire check can compute post-fire distance-to-liquidation from `liquidationPx` vs
  mark; an in-trade sentry read can page/flag when `(equity − crossMaintenanceMarginUsed)` drops below a hard
  buffer. **MUST-VERIFY:** how `liquidationPx` behaves for a *cross-margin* position under unified accounts
  (it depends on the whole book, not one position) — read it live before trusting a single-position formula;
  and confirm `crossMaintenanceMarginUsed` units (USDC) on a live position.
- **Effort:** a `Caps` hard cap (`HL_MIN_LIQUIDATION_DISTANCE_PCT` and/or `HL_MAX_MAINTENANCE_MARGIN_FRAC`);
  a gate check after sizing; a sentry status field + alert. Fail-closed on missing `liquidationPx`.
- **Dependencies:** none. This is a **hard-cap** surface item (off-limits to the LLM/tuner), like A/B.

### 4️⃣ N — Funding-rate awareness as a forward-looking signal  ·  Impact **Med** / Effort **M**
One-liner: surface the **current/predicted funding rate** in the decision context (so the LLM weighs carry in
act/skip/timing) and give sentry a rule to reconsider a position bleeding funding against a flat thesis.
- **Distinction from K:** K *accounts* for funding already paid (backward, evidence). N *anticipates* funding
  as a cost of the decision (forward, judgment). Both, but different surfaces.
- **Why hl-cli specifically:** `enrich` (`hlcli/executor/enrich.py`) assembles mark, candles, regime, book,
  outcomes — but **no funding**. A setup that's marginal on price can be clearly negative once an adverse
  hourly funding is priced in, especially for a WAIT that intends to hold.
- **The right data source (field names CONFIRMED):** `Info.funding_history(coin)` → `{fundingRate, premium, time}`
  per hour; `meta_and_asset_ctxs` carries the current `funding` per asset in the asset context. **MUST-VERIFY:**
  which field is the *predicted next* vs *last realized* funding, and the rate's period (HL funding is **hourly**
  and rate is per-hour, cap 4%/hr in extremis — [Funding docs](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)); do not annualize or hourly-ize by guess.
- **Overlap check:** not in hl-cli; sits on the **tunable/context** surface (advisory to the LLM, clamped),
  not a hard gate — funding is a cost input, not a safety limit.
- **Effort:** add a funding field to `EnrichedContext`, one line in the decision prompt, and an optional sentry
  consideration. Keep it advisory (the LLM weighs it); the gate stays deterministic.
- **Dependencies:** none. **MUST-VERIFY:** predicted-vs-realized field; period; sign (long pays positive funding).

### 5️⃣ O — REPL header: network + active account + balance  ·  Impact **Low (UX/safety)** / Effort **S**
One-liner: show the **active account alias + short address** alongside the network and live equity in the REPL
header, so the operator always sees *which wallet on which network with what balance* before any order.
- **Why hl-cli specifically:** the prompt already colors the network (`hl(testnet)>`) and the header shows
  equity/uPnL (`hlcli/cli/repl.py`), but **not which account** is resolved — the exact thing that determines
  the address you trade. On a machine with multiple testnet/mainnet accounts this is a wrong-wallet foot-gun.
- **Overlap check:** pure hl-cli UX; not order-path. Cheap, high day-to-day clarity, zero risk.
- **Effort:** the REPL already resolves the account for the header's `open_env`; add alias + `masked` address
  to the header line. **MUST-VERIFY:** nothing exchange-side — but keep the key masked (reuse the existing
  address-truncation, never print the key).

### 6️⃣ P — CRUD completeness: `account edit`/re-key + `config reset`  ·  Impact **Low** / Effort **S**
One-liner: close the two CRUD gaps testing found — no in-place account edit (address/alias/re-key: today you
`remove`+`add`) and no `config reset/unset` (today you delete `active_config.json` by hand).
- **Why hl-cli specifically:** `account` supports add/ls/set-default/remove but no edit; `config` now has
  show/set/edit (built this session) but no reset-to-defaults. Both are small ergonomic completions.
- **Overlap check:** pure hl-cli; `config reset` must go through the same clamp-on-write path as `set`/`edit`
  (delete-or-rewrite `active_config.json`); `account edit --rekey` must reuse the hidden-prompt keystore write
  and re-check perms. **MUST-VERIFY:** nothing exchange-side; keep the key handling identical to `account add`.
- **Dependencies:** none.

### 7️⃣ Q — Read-side rate-limit handling + WebSocket marks  ·  Impact **Med (autonomy)** / Effort **M–L**  ·  ✅ DONE (read-backoff) / ⏸ WS deferred
**Shipped (2026-07-19):** bounded, jittered read-side retry in `MarksFeed._info` — retries 429 / 5xx / transient
transport drops (`_RETRYABLE_STATUS`), honors a `Retry-After` header (clamped to `retry_max_delay` so a hot-loop read
can't stall), fails fast on non-429 4xx, and re-raises after `max_retries` for the caller's own loop. Reuses
`hlcli/core/backoff.py`; keeps the httpx `/info` path (paper stays keyless). Defaults `max_retries=3, base=0.5,
max_delay=4.0` → worst-case ~7s bounded wait. **Verified against the official rate-limits doc (2026-07):** IP aggregate
weight **1200/min**, `allMids`/`l2Book` weight **2**, over-limit → **HTTP 429** (may carry `Retry-After`); 5xx retryable.
**WS marks feed deliberately deferred** — marks already carry a 2s TTL cache so poll load is low, and a persistent
`Info.subscribe` connection adds a background thread + reconnect/staleness handling that needs its own evidence pass;
the rate-limit hardening is the high-value slice. Revisit if a live loop actually hits limits.

One-liner: make continuous `exec run` / `sentry run` / `agent run` resilient to HL info rate limits — bounded
backoff on read 429s, and (optionally) a WebSocket marks feed to replace per-tick polling.
- **Why hl-cli specifically:** marks/book/candles go through httpx `/info` polling (`hlcli/exchange/marks.py`);
  the agent polls on tunable cadences. HL info endpoints are rate-limited; the reduce-only *write* paths already
  back off (audit D-2), but the **read** paths don't, and a tight autonomous loop can hit limits. Wave-1 already
  flagged the WS upgrade as a deferred refinement (H / "watch modes are poll-based").
- **Overlap check:** partial — write backoff exists (`hlcli/core/backoff.py`), read backoff + WS do not.
- **The right facts (verify before coding):** **MUST-VERIFY** the current HL info rate-limit numbers and the
  429 response shape against [the docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
  (limits change — do not hard-code a number from a blog); confirm the SDK `Info.subscribe` WS message shape
  before replacing the poll. Keep the httpx `/info` path (paper stays keyless — Wave-1 gotcha).
- **Dependencies:** none external. Lower priority than K/L/M; it's an autonomy-hardening item.

## 9. Wave-2 ranked shortlist (build in this order)

1. **K — fee/funding-honest P&L.** The highest-leverage evidence fix: every graduation and tuner-promote
   decision is currently made on cost-blind expectancy. Do first if mainnet is the goal. Pairs with Wave-1 D.
2. **L — real exit fill for externally-closed positions.** Cheap, same `user_fills` read as K, removes a
   standing bias in the ledger. Ship alongside K.
3. **M — margin-health / liquidation guard.** The top *new* safety item under unified margin; a hard cap the
   LLM/tuner can't move. Do before any mainnet size increase.
4. **N — funding as a forward signal.** After K (same funding plumbing), advisory-only, improves judgment.
5. **G (Wave-1) — reconciliation halt-on-divergence.** Elevated by the live run: now that hl-cli demonstrably
   fills/adopts/resolves real orders, a formal safe/requires-halt verdict on restart is a real mainnet backstop.
6. **O, P — REPL header + CRUD completion.** Cheap UX wins; do whenever, no exchange risk.
7. **Q — read rate-limit/WS.** ✅ read-backoff shipped; WS feed deferred (see Q entry). Autonomy hardening.

## 10. Wave-2 cross-cutting notes

- **The binding rule at the top of this wave is not optional.** K/L/M/N read fields that behave differently
  under unified vs standard accounts and whose sign/units/netting are easy to get subtly wrong — exactly the
  shape of the F2 bug. Cite the doc + a live-capture fixture for every field, per `docs/evidence-gate.md`.
- **Surface placement:** K, L extend the **evidence** surface (ledger/report); M extends the **hard-cap** gate
  surface (`.env`/`Caps`, off-limits to LLM+tuner); N extends the **tunable/context** surface (advisory);
  O, P, Q are **CLI/runtime** only. Keep the split intact.
- **Evidence hygiene:** K/L change what `realized`/`r_multiple` *mean* — re-baseline any graduation thresholds
  and re-check `conviction_calibration` after landing them, and keep excluding `aborted`/`abort_failed`/adopted.
- **Paper parity:** K's paper fee/funding model must mirror how HL actually charges, or paper→testnet→mainnet
  expectancy stops being comparable and the validation ladder loses its meaning. Verify the model, don't assume it.
- **None of Wave 2 needs anything from thirdeye-core** — all of it is exchange-side accounting/safety the
  executor owns. The signal boundary (§1) is still sufficient.
