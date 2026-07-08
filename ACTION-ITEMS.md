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

- [x] 3.1 `executor/enrich.py` — marks, portfolio + equity + P&L, recent decisions, tunable surface, plus a candle tail + regime label (see 3.7)
- [x] 3.2 `executor/decision.py` — LLM decision (lazy `anthropic`), claude-sonnet-4-6, forced strict tool `submit_decision`, `decision_temperature`
- [x] 3.3 `validate_decision` validator + clamp; bad enum/missing/non-numeric → drop+tally, out-of-range conviction → clamp; never guesses
- [x] 3.4 Decision log carries enriched context + decision + gate + fill (resolved-outcome cohorting is Phase 4.1 — this is its substrate)
- [x] 3.5 `exec shadow` — `run_once(fire_enabled=False)`: decide + gate + log, fire nothing
- [x] 3.6 Tests: `test_decision.py` validator/clamp + mocked client; executor mechanics inject a deterministic `decide_fn` (LLM never hit in tests)
- [x] 3.7 Candle feed + deterministic regime (commit `fa803d6`): `MarksFeed.candles` + `Exchange.get_candles` on both backends (keyless `/info candleSnapshot`, 15m × 48-bar window, once per coin per pass, best-effort — feed failure degrades to `None`). New `executor/regime.py`: Kaufman efficiency-ratio `classify()` → trend/range/None (<20 bars ⇒ None; threshold 0.35) + a 12-bar OHLC `summarize()` for the model. Runner gathers per-coin context once and feeds `enrich(candles=, regime=)`; **regime now reaches the gate** (no longer always `None`). Prompt hardening: rationale-first tool order, temperature sent only to models that accept it, conviction anchor + execution-trader persona.
- [x] 3.8 WAIT → follow-up re-check loop (commits `5b691f0`, `71f2698`): decision tool gains `recheck_in_minutes` (validator clamps to [0,1440]; missing ⇒ None) + `Decision.recheck_in_minutes`. New hard cap `HL_FOLLOWUP_MAX_ATTEMPTS` (default 3, 0=disabled). New `deferred` table + `DeferredCandidate` + `defer_candidate`/`due_deferred`/`drop_deferred`/`deferred_count`. Runner refactored (`_evaluate` + `_fire_and_reconcile` shared by intake + re-check loops): an `act+wait` candidate is **deferred, not rejected** — intercepted BEFORE the gate (gate stays pure), parked, HWM still advances. Re-check scheduled WITHIN `max_signal_age` (clamped; no room/attempts ⇒ terminal reject); each re-check uses FRESH enrich/candles/regime. Due deferrals processed before new intake; skipped in `dry_run` AND while the breaker is tripped (frozen, attempts intact). `PassSummary` gains `rechecked`/`deferred`; `exec report.deferred` + an `exec status` note surface the parked count.
- [x] 3.9 Prompt/context review fixes (2026-07-03): decision rationale is 2-4 sentences reasoned first (the only reasoning space under a forced tool call; clamp 500→800 chars); system prompt now states the action×timing combinations, the min_conviction⇒zero-size mechanic, low/high conviction anchors + anti-clustering, newest-first ordering, and a balanced act criterion (skip-bias alone would starve the shadow book and the sample-gated tuners). User turn = task line + compact JSON in a `<context>` tag. Candles labeled `{interval, order, bars}` (fetched at the labeled 15m); recent rows carry `coin` + `minutes_ago`. No-mark candidates rejected before the LLM call. `stop_reason` on `DecisionResult` → dropped-decision log. `claude-sonnet-5` added to the no-sampling-params blocklist. Config-tool schema descriptions state units + clamp bounds; config-tuner prompt: evidence sets the step size. Prompt-tuner pairs include the rationale; current prompt sent in a tag; wrapping code fences stripped; anti-bloat instruction. 250 tests pass.

Gate verification (real LLM call on paper/testnet shadow) is deferred pending an `ANTHROPIC_API_KEY`, mirroring Phase 1's deferred live testnet order. Pipeline is code-complete + fully covered by mocked tests (177 pass).

## Phase 4: Self-tuning (out-of-path, propose→approve)
Gate: tuner proposes from logged outcomes; `promote` works; clamps hold.

- [x] 4.0 Trade resolution (prerequisite): `executor/resolve.py` closes open trades on SL/TP/expiry → `trades` ledger (won/lost/expired, realized, R-multiple); wired into `run_once` (opens on fill, resolves at pass start). `max_hold_minutes` added to tunable surface.
- [x] 4.1 `tuner/stats.py` — cohorts (coin×side×conviction-bucket), win-rate + avg-R; sample-gated (`MIN_COHORT_SAMPLES=5`; no eligible cohort ⇒ empty ⇒ model not called)
- [x] 4.2 `tuner/config_tuner.py` — propose tunable-surface edits (claude-opus-4-8, forced strict `submit_config`), clamped on propose + on load → `proposed_config.json`
- [x] 4.3 `tuner/prompt_tuner.py` — refine decision prompt from decisions-vs-outcomes (claude-opus-4-8, text) → `proposed_prompt.md`; decision.py now loads `active_prompt.md` (fallback to built-in)
- [x] 4.4 `tuner/promote.py` — proposed → active (re-clamps config), `promotions.jsonl` audit, `diff`
- [x] 4.5 `tune run | diff | promote | history` (run no-ops keyless when gated; verified)
- [x] 4.6 Tests: cohort gating, model-not-called gate (Boom client), config clamp holds, promote flow + re-clamp, prompt tuner — all mocked. 123 pass.

§13 Q4 kept at default (propose→approve everywhere). Resolution chosen over deferral (Q from this session). Config tuner real-LLM run deferred pending a key; pipeline fully mocked-tested.

## Phase 5: Mainnet hardening
Gate: testnet/shadow expectancy clears → controlled mainnet at tiny caps.

- [x] 5.1 `executor/protect.py` — native exchange-side SL/TP reduce-only triggers placed at entry (reuses the live backend's trigger path). `requires_native_protection()` = True for testnet+mainnet (§13 Q6 confirmed: hard prereq). Runner places entry → protection; on a live backend the resolver now closes via reduce-only MARKET (`native_protected`), paper unchanged.
- [x] 5.2 Mainnet prereq has teeth: a live entry that can't be protected is emergency market-closed (never left naked), no ledger entry, status `aborted` — idempotency key already spent so it won't re-fire. Mainnet env gate + typed confirm already shipped (Phase 0/1).
- [x] 5.3 `safety/graduation.py` — N resolved trades / N days / positive expectancy vs new hard caps (`GRADUATION_MIN_TRADES=20`, `_DAYS=7`, `_EXPECTANCY=0.0`); surfaced in `exec report`.
- [x] 5.4 Key handling review — keys live only in `HyperliquidExchange._agent_key` (signing); `EnrichedContext`/decision-log context are keyless by construction. Regression test asserts no key-ish field + log context ⊆ {coin, equity, open_coins, regime}. Keystore 0600 already tested.
- [x] 5.5 `safety/alerts.py` — structured JSONL (`alerts-<network>.log`) + stderr (§ confirmed: log+stderr, no deps/keys). Runner hooks: fire, reject, halted (breaker/loss-limit), protection_failed. Injected into `exec once`/`run`; None in shadow/tests = silent.
- [x] 5.6 Tests: protection (builder/place/abort), graduation thresholds, alert emission, key redaction, report CLI. 143 pass, 1 skip; keyless-import invariant re-verified.

§13 Q6 = native SL/TP is a hard mainnet prerequisite (user-confirmed). Native triggers scoped to testnet+mainnet so the safety path is exercised before real money (user-confirmed over mainnet-only). Live testnet/mainnet runs + real graduation deferred pending keys (as with Phase 1/3/4).

Pre-mainnet self-review fixes (all applied): H1 executor entry is now MARKET so accepted⇒filled (a resting GTC limit would track a phantom position); runner reconciles open_trade + protection size/entry against the *actual* fill (`OrderResult.filled_size`/`avg_price`), unfilled⇒no trade. M3 live resolver books the real close-fill price, not the idealized level. M4 `fire()` releases the idempotency key on a definitive reject (keeps crash-safety; transport errors still raise→keep key). L5 `halted` alert is edge-triggered (meta `alert_halt_last`), not per-pass. L6 clamp filters `allowed_regimes` to a known vocabulary. L7 gate rejects non-positive equity explicitly. 152 tests pass.

## Phase 6: Sentry — in-trade manager (PLAN.md §14)
Scope confirmed 2026-07-05: manages open positions + enters deferred WAIT candidates; never originates trades (§13 Q1 unchanged).

### 6a — Deterministic trail engine (no LLM)
Gate: trades trail + scale out on paper, ratchet-only, restart-safe. ✅ passed

- [x] 6a.1 Tunable surface: `trail` sub-model (style atr|percent|off, atr_multiple, trail_start_r, breakeven_trigger_r, breakeven_buffer_r, scale_out_r, scale_out_fraction, min_move_r) + clamps; all rules default OFF
- [x] 6a.2 `sentry/engine.py` — pure rule evaluation: breakeven ratchet, ATR(14)/percent trail, one-shot scale-out; SL only moves toward profit, never at/past the mark; dust suppression (`min_move_r`); everything in R vs `initial_sl`
- [x] 6a.3 State: `sentry_log` table; trades gain `initial_sl` (backfilled) + `scaled_out` (additive migrations); `update_trade_sl` / `split_trade` (partial → resolved `scaled` child row)
- [x] 6a.4 Apply layer (`sentry/apply.py`): paper scale-out = reduce-only LIMIT at the ladder; live = reduce-only MARKET booking the real fill; live stop sync place-new-then-cancel-old (reject ⇒ old level kept everywhere); idempotent via `sentry:scale:<id>`; shadow rows managed orderlessly. Resolver follow-through: R vs `initial_sl`, profit-side stop-out books `won`, stats count `scaled` as a win
- [x] 6a.5 `hl sentry once | run | status | log` CLI; `run_once` runs the manager just before resolve (`PassSummary.managed`)
- [x] 6a.6 Tests (33): ratchet/dust/mark-guard invariants, breakeven + trail math (long/short), ATR, scale-out split + book, crash idempotency, shadow isolation, dry-run, live trigger sync + rejection, resolver interplay, runner integration — 283 total pass, keyless

✅ Phase 6a complete — verified live on paper (real mainnet marks): scale-out banked at the +1R ladder, percent trail ratcheted the stop, second pass a clean no-op (churn guards), dry-run side-effect-free.

### 6b — Sentry shadow (LLM proposes, logs only)
Gate: shadow log shows sane actions; value-add vs 6a baseline measurable. ✅ passed (live-verified; evidence accumulates operationally)

- [x] 6b.1 `sentry/decision.py` — management prompt (HOLD default, anti-churn, thesis-first) + forced strict `submit_management` (hold/tighten_stop/reduce 25·50·75/close/extend_tp; NO ADD until 6d); structural validate/clamp drops-never-guesses (bad action, non-finite confidence, unusable param); `supports_temperature` moved to core/llm.py (shared with entry decision)
- [x] 6b.2 `sentry/context.py` — `ManagementContext`: position state in R + age, original thesis (intake reasoning/news + entry verdict via new `intake_candidate`/`decision_for`), 15m + 1h candle frames, regime, prior sentry actions (`sentry_for_trade`), trail surface; keyless by construction
- [x] 6b.3 `sentry/shadow.py` — propose-and-log paired with the 6a baseline at the same instant (baseline never shown to the model), `agrees` alignment, drops → `shadow_dropped`; `hl sentry shadow` + `run --shadow`; scoreboard in `sentry status`
- [x] 6b.4 Deferred WAIT re-entry on sentry cadence: `run_once(include_intake=False)` = the watch pass (manage + resolve + due deferrals through the normal decision/entry gate; intake untouched, HWM unchanged, shared attempts/idempotency); `hl sentry once|run` use it

✅ Phase 6b complete — 312 tests pass (29 new). Live-verified on paper: real sonnet call produced a sane, thesis-aware tighten_stop proposal at +0.2R with an idle baseline (judgment-beyond-rules, logged, fired nothing); watch pass managed+resolved and left intake untouched.

### 6c — Sentry live, risk-reducing only
Gate: gated actions fire on paper/testnet; churn caps hold. ✅ passed (live-verified on paper)

- [x] 6c.1 `sentry/gate.py` — `evaluate_management` (first-failure): breaker/loss-limit ⇒ ↓risk only → per-position daily budget → cooldown → extend↔bank opposing window → action checks (tighten must ratchet + clear min_move_r + sit off the mark; one partial per trade; close always; extend_tp needs breakeven-or-better and ≤1R per move). Churn clocks read from the sentry log (restart-proof)
- [x] 6c.2 Hard caps in `.env` (`HL_SENTRY_*`): eval interval (bounds LLM spend), min action interval, actions/position/day, LLM calls/day (rolling 24h), opposing window — documented in .env.example
- [x] 6c.3 `sentry/live.py` `manage_live` + apply layer grows `apply_close` (books won/lost by realized sign, cancels orphaned triggers) and `apply_move_tp` (place-new-then-cancel-old); real trades only; every evaluation logged (`managed_hold/rejected/dropped` or applied `managed_<action>` with confidence+rationale); `hl sentry manage` + `run --manage` (exclusive with `--shadow`), **mainnet refused until 6d graduation**. Stats: `scaled` counts as a win only when realized > 0. Tests: 24 new (gate matrix, paper apply paths incl. partial-loss banking, eval spacing, budgets, cooldown, shadow-book skip, live TP replace/reject, close trigger cleanup)

✅ Phase 6c complete — 336 tests pass. Live-verified on paper: real LLM pass evaluated the open position and HELD with a sane thesis-aware rationale (logged `managed_hold`); immediate rerun was eval-spaced with zero LLM calls; prior_actions shadow-leak found live and fixed (shadow rows excluded from history).

### 6d — Pyramiding (ADD)
Gate: ADDs pass full entry caps; add-risk covered by unrealized P&L; testnet until graduation. ✅ passed

- [x] 6d.1 ADD action (`gate._check_add` + `apply.apply_add`): winners only (≥ `HL_SENTRY_ADD_MIN_R`, default 1.0); the model nominates add + a raised stop, the CODE sizes it — min(profit-covered risk, ½ the coin's total size, notional-cap room, leverage-cap room), rejected when halted or no room; lifetime `HL_SENTRY_MAX_ADDS_PER_POSITION` per coin. Apply order: raise the whole position's stop FIRST (refused raise ⇒ no add) → idempotent MARKET add (key = coin's add ordinal, crash-safe) → ledger child row (entry at fill, initial_sl at the raised stop — honest R) → live slice protection (own reduce-only SL/TP pair; failure ⇒ emergency close + `aborted` row, like a failed entry)
- [x] 6d.2 Graduation before mainnet management: `graduation_for_management(caps)` assesses the TESTNET book (real + shadow) via `safety/graduation.assess`; `hl sentry manage`/`run --manage` on mainnet refuse with the failing checks named until it clears

✅ Phase 6d complete — 351 tests pass (15 new: validation, gate matrix incl. sizing clamps + sibling-row coin_size, paper add + blended book, crash idempotency, live raise-fire-protect ordering, raise-rejection abort, slice-protection emergency close, graduation verdict). Live-verified: mainnet `sentry manage`/`run --manage` refused with named failing graduation checks on an empty testnet book.

**Phase 6 (sentry) complete** — remaining work is operational: accumulate `sentry run --manage` evidence on paper/testnet, clear graduation, then mainnet management at tiny caps.

## Phase 7: Agent mode — autonomous operation (PLAN.md §15)

Design decided 2026-07-07 (user-confirmed): VPS runtime · watched intake dir (JSON batches, producer-agnostic — repo stays independent of any signal engine) · Mode A adoption alert+skip when stopless · reflection injected bounded · tuner auto-promote paper-only.

### 7a — Supervisor + intake channel
Gate: batch file dropped → paper trades end-to-end; `kill -9` mid-pass + restart ⇒ no double-fire, file not reprocessed. ✅ passed (live-verified on paper)

- [x] 7a.1 `hlcli/agent/` supervisor: intake-dir poll (new file → parse → queue → immediate exec pass; `processed/` · `failed/` + alert, nothing deleted; 2s settle window; enqueue-before-move so a crash re-parses into dedupe, never double-queue), exec cadence, sentry cadence (`--shadow`/`--manage` pass-through, graduation unchanged), daily job at `HL_AGENT_DAILY_UTC` (meta-persisted: restart-proof + misfire recovery), hourly heartbeat, exponential failure backoff. Cadences on the tunable surface (`TunableConfig.agent`, clamped); passes injected for tests
- [x] 7a.2 `hl agent run|status` (status reads state meta cross-process: running/pass ages, breaker, book, realized-today, deferred, pending tuner proposals, intake dir); shared `open_env`/`network_alerter` extracted (exec/sentry/agent no longer triplicate them)
- [x] 7a.3 `deploy/`: systemd unit (Restart=always) + Dockerfile (`[exchange,llm]`, `/data` volume) + VPS ops doc incl. the producer file-drop contract (atomic rename)

✅ Phase 7a complete — 368 tests pass (17 new). Live-verified on paper: dropped batch → detected in ≤5s → enqueued → immediate exec pass → real sonnet decision (skip, thesis-aware on live candles) → archived; `kill -9` + restart re-processed nothing; identical re-drop deduped (`enqueued=0 duplicates=1`); `agent status` live from a second process.

### 7b — Daily journal
Gate: a paper-trading day yields a journal reconciling with `exec report`; opus narrative present + logged. ✅ passed (live-verified on paper)

- [x] 7b.1 Deterministic digest from state store (per-verdict lines incl. the model's rationale, gate-reason tally, resolves + R/expectancy/PF, sentry actions, warning+ alert events, pending proposals, `exec report`-reconciling snapshot) → `journal/<network>/YYYY-MM-DD.md`; `hl journal write|show|ls`; `decisions_between`/`sentry_between` range queries added to the store; agent daily job writes yesterday's journal
- [x] 7b.2 Opus narrative — one call/day (`HL_JOURNAL_MODEL`/`HL_JOURNAL_MAX_TOKENS` caps, `agent.journal_narrative` tunable switch), cached per-date in state meta (re-writes never re-roll/re-bill), failure ⇒ placeholder + `journal_narrative_failed` alert, digest always writes

✅ Phase 7b complete — 377 tests pass (9 new). Live-verified on paper: journal reconciled with `exec report` (2 decisions, 2 skips, flat book); real opus reflection landed and immediately flagged that skips lacked rationale — digest enriched with per-verdict rationale lines in response; re-write reused the cached narrative.

### 7c — Reflection memory + scheduled tuners
Gate: capped inject visible in decision_log; nightly tuner: paper auto-promotes, testnet/mainnet wait. ✅ passed (live-verified on paper)

- [x] 7c.1 `reflections` table (upsert per date) + journal narrative now a forced `submit_journal` tool call returning reflection + ONE distilled lesson (no tool call ⇒ dropped, never guessed); `journal/lessons.py` bounded inject (`HL_AGENT_REFLECT_INJECT_MAX`/`_MAX_CHARS` hard caps, `agent.reflection_inject` tunable switch) into `EnrichedContext.recent_lessons` + `ManagementContext.recent_lessons` (exec runner, sentry shadow + live passes); both system prompts describe the block as advisory-never-override; decision_log context records injected lesson dates
- [x] 7c.2 `agent/daily.py` `run_daily`: journal yesterday → both sample-gated tuners → auto-promote pending proposals on PAPER only (testnet/mainnet wait for human `tune promote`) → `agent_daily_report` alert; agent daily_pass wired to it

✅ Phase 7c complete — 386 tests pass (18 new incl. rewritten narrative tests). Live-verified on paper: real opus `submit_journal` call distilled a lesson into the reflections table; next `exec once` carried it — decision_log context shows `lessons: ["2026-07-08"]` with a thesis-aware verdict; paper auto-promote/testnet-wait covered by tests.

### 7d — Mode A adoption
Gate: manual testnet order with a stop gets adopted + trailed; stopless position alerts and stays untouched.

- [ ] 7d.1 Adopt unmanaged position when an exchange stop trigger exists (entry = avg price, initial_sl = trigger, row flagged `adopted`); no stop ⇒ alert + skip, never invent one; `hl sentry adopt`
