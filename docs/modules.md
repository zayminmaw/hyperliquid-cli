# Modules

One section per package. Signatures are the public surface as of this handover;
read the module docstrings for the "why."

---

## `core/` — config, types, network, LLM client

| File | What it does | Key surface |
|------|--------------|-------------|
| `config.py` | Hard caps from `HL_*` env / `.env` via pydantic-settings. Off-limits to the LLM/tuner. | `Caps` (settings model), `Caps.coins`, `get_caps()` (lru-cached) |
| `config_schema.py` | The tunable surface + the clamp that bounds every field before it can reach the order path. Missing file → defaults; malformed → `ConfigError`. | `TunableConfig`, `RegimeGate`, `ConvictionSizing`, `clamp()`, `load_tunable()` |
| `types.py` | Domain model — all pydantic / `StrEnum`. | `Network`, `Side`, `OrderType`, `Action`, `Timing`, `Candidate`, `Decision`, `Order`, `Position`, `OpenOrder`, `OrderResult` |
| `network.py` | Network resolution (paper default) + the mainnet gate. | `resolve_network()`, `enforce_mainnet_gate()`, `MainnetGateError` |
| `llm.py` | The **one** lazy `anthropic` import; also the model-capability knowledge (which families reject sampling params). | `make_client()`, `supports_temperature()` |

`OrderResult` carries `filled_size` / `avg_price` so the executor can reconcile to
the *actual* fill rather than the intended order.

---

## `cli/` — the Typer app

| File | What it does |
|------|--------------|
| `app.py` | Builds the `hl` Typer app, parses global flags (`--network/--account/--json/--dry-run/-y`) into `GlobalState`, wires the command groups. |
| `context.py` | `GlobalState`; `build_for(state, for_write)` resolves account + key and enforces the mainnet gate (keys loaded only for writes); `typed_confirm`. |
| `commands/` | `account · markets · asset · trade · exec_ · sentry · config · tune` — noun→verb groups. |
| `output.py` | Rich-table / JSON rendering (`--json` switch). |
| `watch.py` | Poll-based `rich.Live` refresh for `-w` watch modes (positions/orders/book/price). |
| `stubs.py` | Phase-labelled placeholders so `hl --help` is fully navigable. |

Command surface: `account add|ls|set-default|remove|positions|orders|balances|portfolio` ·
`markets ls|prices` · `asset price|book` · `trade order|cancel|cancel-all|set-leverage`
(Mode A) · `exec propose|once|run|shadow|status|report|breaker` (Mode B) ·
`sentry once|run|shadow|manage|status|log` (in-trade manager; mainnet manage is graduation-gated) ·
`tune run|diff|promote|history` · `config show|set|edit`.

---

## `exchange/` — backends

| File | What it does | Key surface |
|------|--------------|-------------|
| `base.py` | The `Exchange` Protocol both backends satisfy. | `Exchange`, methods: `get_marks/get_book/get_candles/equity/get_positions/get_open_orders/place_order/cancel/cancel_all/set_leverage` |
| `marks.py` | Public `/info` reads over **httpx** (keyless), TTL-cached (HTTP errors raise; cache returned by copy); includes `candleSnapshot` for regime and `meta` for per-asset `szDecimals`. | `MarksFeed`, `MarksFeed.candles()`, `MarksFeed.sz_decimals()`, `api_url()` |
| `paper.py` | Simulated book on public marks; state-backed (persists in `state-paper.db`). | `PaperExchange` |
| `hyperliquid.py` | Live testnet/mainnet. Reads keyless (`frontendOpenOrders`, so trigger orders are visible to `cancel-all` and cleanup); writes need the agent key (SDK + `eth_account` lazy-imported) and are rounded to the asset's size/price precision on the wire (`rounding.py`). | `HyperliquidExchange` (key only in `_agent_key`) |
| `rounding.py` | Pure per-asset wire rounding: size floors to `szDecimals`, price to 5 sig figs / `6−szDecimals` decimals. | `round_size()`, `round_price()` |
| `factory.py` | `build_exchange(network, caps, account=, agent_key=)` picks the backend. | `build_exchange()` |

---

## `accounts/` — multi-account store

| File | What it does | Key surface |
|------|--------------|-------------|
| `store.py` | SQLite account metadata at `~/.hyperliquid-cli/accounts.db`: alias, address, network, type, key_ref; per-network default. | `open_store()`, `Account`, `AccountType` |
| `keystore.py` | Per-account agent key as a `0600` file, never logged; address derivation lazy via `eth_account`. | `Keystore` |

---

## `executor/` — Mode B pipeline

| File | What it does | Key surface |
|------|--------------|-------------|
| `intake.py` | Build candidates from CLI flags / dicts; side inferred from level geometry; pair/reason aliases. | `make_candidate()`, `candidate_from_dict()`, `parse_batch()` |
| `enrich.py` | Assemble the LLM's input: marks, equity, positions, P&L, recent decisions **and resolved outcomes** (the track record, in R — both newest-first; recent rows carry `coin` + `minutes_ago` so the model can anchor them), tunable surface, a labeled candle context (`{"interval", "order", "bars"}` — bare bars are meaningless without a timeframe), regime label, and a `followup` block on WAIT re-checks. | `enrich(…, outcomes=, candles=, regime=, followup=, now=)`, `EnrichedContext` |
| `regime.py` | Deterministic market-regime classifier (computed in **code**, not by the LLM). Kaufman efficiency-ratio over the candle window → `trend`/`range`/`None` (`<20` bars or no feed ⇒ `None`; ER threshold `0.35`), plus a 12-bar OHLC tail for the model. | `classify()`, `summarize()` |
| `decision.py` | The LLM call (lazy `anthropic`, `claude-sonnet-5`, forced strict rationale-first tool `submit_decision` — the 2-4-sentence rationale is the model's only reasoning space under a forced tool call; low temp sent only to models that accept it — NOT Sonnet 5/Opus 4.7+/Fable, so the default order-path model gets none) + validate/clamp. User turn = one task line + the context as compact JSON in a `<context>` tag. Carries `recheck_in_minutes` for WAIT timing (clamped to `[0,1440]`); `stop_reason` rides on `DecisionResult` so truncation/refusal drops are diagnosable. Schema-invalid → dropped + tallied, never guessed. | `decide()`, `validate_decision()`, `load_decision_prompt()`, `DecisionResult` |
| `gate.py` | The deterministic risk gate (first-failure wins, incl. mark sanity — mark present, inside sl/tp, R:R at mark ≥ floor) + fixed-fractional sizing **at the mark** + side inference. | `evaluate()`, `GateContext` (`mark=`), `GateOutcome`, `infer_side()` |
| `execute.py` | `fire()` records the idempotency key **before** placing → a crash skips (missed trade), never double-fires. Releases the key on a clean reject. | `fire()` |
| `protect.py` | Native exchange-side SL/TP reduce-only triggers; required on testnet/mainnet. Failed protection cleans up after itself (`cancel_placed`), and `cancel_coin_triggers` removes the surviving half of a pair after a close. | `requires_native_protection()`, `protective_orders()`, `place_protection()`, `emergency_close()`, `cancel_placed()`, `cancel_coin_triggers()`, `ProtectionResult` |
| `resolve.py` | The monitor step: close open trades on SL/TP/expiry → the `trades` ledger (won/lost/expired/closed, realized, R-multiple — R against `initial_sl`, and a stop-out on the profit side of entry books `won`, since sentry may have ratcheted the stop past entry). On live networks it also reconciles **vanished** positions (native trigger fired on a wick, or a manual close — outcome inferred from candle extremes, else `closed` at mark), cancels surviving triggers after a close, and resolves shadow trades orderlessly. | `resolve_open_trades(…, shadow_only=)` |
| `monitor.py` | Read-only position-health view. | `position_health()` |
| `runner.py` | `run_once()` — the full pass orchestrator (resolve → re-check due WAIT deferrals → pull → enrich(+candles/regime/outcomes) → decide (skipped when the coin has no mark — the gate would reject anyway, so the paid call isn't spent) → defer-if-WAIT / gate → fire → open ledger row (**before** protection, so a crash never leaves an untracked position; abort resolves it `aborted`) → protect → log → advance HWM). An `act+wait` decision is parked in the `deferred` table and re-checked with fresh data (within freshness, up to `HL_FOLLOWUP_MAX_ATTEMPTS`, labeled `followup`); re-checks freeze while the breaker is tripped. Shadow books hypothetical trades; unmanaged exchange positions raise an edge-triggered alert. Honors `dry_run` (fully side-effect-free), `fire_enabled` (shadow), injected `decide_fn`, and an `Alerter`. | `run_once()`, `PassSummary` (`seen/rechecked/approved/fired/rejected/failed/dropped/deferred/resolved/managed`) — `managed` = sentry 6a actions, run just before resolve |

---

## `state/` — durable SQLite (network-scoped)

`store.py` — one DB per network (`state-<network>.db`). Holds: the intake stream +
high-water mark, idempotency keys, the decision log, the `trades` ledger (with a
`shadow` flag for hypothetical trades, `initial_sl` anchoring R math once sentry
ratchets the working `sl`, and a one-shot `scaled_out` flag; additive column
migrations run on open), the `sentry_log` management audit trail, the `deferred`
table (WAIT candidates parked for re-check), the paper book, the breaker flag, and
a `meta` key/value table. `resolved_trades(limit=N)` returns the most recent N
(newest-closed first).

Key surface (`StateStore`): `enqueue` · `pull_new` · `get_hwm`/`advance_hwm` ·
`set_status` · `already_fired`/`record_fire`/`release_fire` · `log_decision`/`recent_decisions` ·
`open_trade`/`open_trades`/`resolve_trade`/`resolved_trades` ·
`update_trade_sl`/`split_trade` · `log_sentry`/`recent_sentry` ·
`defer_candidate`/`due_deferred`/`drop_deferred`/`deferred_count` (with `DeferredCandidate`) ·
`paper_positions`/`upsert_paper_position`/`delete_paper_position`/`paper_realized`/`add_paper_realized` ·
`breaker_tripped`/`set_breaker` · `meta_get`/`meta_set`. Constructed via `open_state(caps, network)`.

The HWM + idempotency keys are what make a restart never double-fire.

---

## `safety/` — the guardrails

| File | What it does | Key surface |
|------|--------------|-------------|
| `breaker.py` | Persisted kill switch + daily-loss-limit (day-start equity drawdown, resets on date rollover). | `Breaker.tripped/set/daily_loss_hit` |
| `alerts.py` | Structured JSONL (`alerts-<network>.log`) + stderr. No deps, no keys. `None` in shadow/tests = silent. | `Alerter.alert(event, level=, **fields)` |
| `graduation.py` | Mainnet-readiness verdict: N resolved trades / N days / positive expectancy vs the hard caps. Surfaced in `exec report`. | `assess(trades, caps)` |

---

## `sentry/` — the in-trade manager (6a mechanics · 6b shadow · 6c gated live · 6d ADD)

| File | What it does | Key surface |
|------|--------------|-------------|
| `engine.py` | Pure trade-management rules, measured in R against `initial_sl`: breakeven ratchet (`breakeven_trigger_r`/`_buffer_r`), ATR/percent trail (activates at `trail_start_r`), one-shot scale-out ladder. Invariants: the stop only ratchets toward profit, never sits at/past the mark, dust moves (`min_move_r`) are suppressed, missing candle data never moves a stop. | `plan()`, `active()`, `atr()`, `ScaleOut`, `MoveStop` |
| `apply.py` | Fires the plan: paper scale-out = reduce-only LIMIT at the ladder level (the book realizes it exactly), live = reduce-only MARKET booking the real fill; a live stop moves **place-new-then-cancel-old** (never naked; reject ⇒ old level kept on both exchange and ledger); scale-outs are idempotent (`sentry:scale:<id>` key recorded before the order, like `fire`). Shadow rows are managed identically but orderlessly; a shadow pass never touches real trades. Every action → `sentry_log`. | `manage_open_trades()`, `ManageSummary` |
| `context.py` | 6b: the management context — position state in R, the original thesis (intake reasoning/news + entry verdict from the decision log), two candle timescales (15m + 1h), regime, the trade's own management history, the trail surface. Keyless by construction. | `build_context()`, `ManagementContext` |
| `decision.py` | 6b: the LLM manager (order-path model, forced strict rationale-first `submit_management`). Bounded menu — hold (the stated default) / tighten_stop / reduce (25·50·75) / close / extend_tp; **no ADD until 6d**. Structural validation drops (never guesses) a bad action, non-finite confidence, or an action whose own parameter is unusable; direction sanity is the 6c gate's job. | `decide_management()`, `validate_management()`, `ManagementAction`, `ManagementDecision`, `ManagementResult` |
| `shadow.py` | 6b: propose-and-log over every open trade (real + hypothetical), pairing each LLM proposal with what the 6a rule baseline would do at the same instant (`agrees` = crude alignment), before the rules mutate the book. Fires nothing; drops logged as `shadow_dropped`. This paired log is the value-add evidence that gates 6c. | `shadow_pass()`, `ShadowSummary` |
| `gate.py` | 6c/6d: the management gate — deterministic, first-failure, the verdict is input never bypass. Breaker/loss-limit ⇒ only ↓risk actions pass (ADD included in the ban); per-position daily action budget; cooldown; extend↔bank opposing window; tighten must ratchet, clear `min_move_r`, and sit off the mark; one partial per trade; extend_tp requires breakeven-or-better and moves ≤ 1R per action. ADD (6d): winners only (≥ `sentry_add_min_r`), the stop must rise with it, and the CODE sizes it — min(unrealized-profit coverage, ½ the coin's total size, notional room, leverage room), lifetime per-coin add budget. | `evaluate_management()`, `ManageGateContext`, `ManageOutcome`, `CloseAll`, `MoveTP`, `AddTo` |
| `adopt.py` | 7d: adopts Mode A positions the ledger doesn't know — requires an existing exchange stop trigger (entry = actual avg price, `initial_sl` = trigger price, farthest stop anchors R, nearest tp is the target or a 100R out-of-reach park; row flagged `adopted`, conviction 0). **Never invents a stop** — stopless positions stay skipped and keep the `unmanaged_position` alert. Records only; places no orders. Runs before every watch pass + `hl sentry adopt`. | `adopt_unmanaged()`, `AdoptSummary` |
| `live.py` | 6c: the live pass — real trades only (the shadow book keeps rules + proposals). Eval spacing (`sentry_eval_interval_minutes`) and the rolling-24h LLM call budget throttle spend; churn clocks are read from the sentry log itself, so a restart can't reset them. Every evaluation logged: `managed_hold`/`managed_rejected`/`managed_dropped` or the applied `managed_<action>` with confidence + rationale. A judgment CLOSE books won/lost by the **sign** of realized P&L. `graduation_for_management()` gates mainnet management on the TESTNET book. | `manage_live()`, `LiveSummary`, `graduation_for_management()` |

Config lives on the tunable surface (`TunableConfig.trail`, clamped; all rules
default **off**, so an unconfigured install behaves exactly as before). `run_once`
runs the manager just before resolve. `hl sentry once|run` is the **watch pass**
(`run_once(include_intake=False)`): manage + resolve + re-check due WAIT deferrals
on sentry's cadence — it may *enter* a parked setup through the normal decision +
entry gate, but it never consumes the intake stream (that stays with `hl exec`).
`hl sentry shadow` (or `run --shadow`) runs the 6b propose-and-log pass; `status`
shows the shadow scoreboard. `hl sentry manage` (or `run --manage`) applies gated
live LLM actions; on **mainnet** it refuses until graduation clears on the
testnet book; `--shadow` and `--manage` are mutually exclusive. Churn + pyramid
hard caps live in `.env` (`HL_SENTRY_*`). ADD's apply order is
raise-stop-first → idempotent market add → ledger child row (own `initial_sl`,
honest R) → slice protection (failure ⇒ emergency close, `aborted`).

---

## `tuner/` — self-tuning (out-of-path, propose→approve)

| File | What it does | Key surface |
|------|--------------|-------------|
| `stats.py` | Resolved-trade cohorts (coin × side × conviction-bucket), win-rate + avg-R; sample-gated (`MIN_COHORT_SAMPLES=5`). A `scaled` row (sentry partial) counts as a win only when its realized P&L is positive — the 6c manager can bank a partial loss. | `cohorts()`, `summary()`, `conviction_bucket()`, `Cohort` |
| `config_tuner.py` | Propose tunable-surface edits (`claude-sonnet-5`, forced strict `submit_config`; every field description states its units + clamp bounds — strict mode can't encode numeric ranges, so descriptions are the model's only channel for them); clamped on propose. No eligible cohort ⇒ model not called. | `propose_config()`, `ConfigProposal` |
| `prompt_tuner.py` | Refine the decision prompt from decisions-vs-outcomes (`claude-sonnet-5`, text). Pairs include the decision **rationale** (which reasoning won/lost is the point of tuning a prompt); the current prompt goes to the model in a tag, not JSON-escaped; a fenced output is stripped before it can reach `promote`. | `propose_prompt()`, `PromptProposal` |
| `promote.py` | proposed → active (config re-clamped); promotion **consumes** the proposal file (promotable exactly once) and the `promotions.jsonl` audit records what went live (full config / prompt hash+size) + `diff`/`history`. Artifacts live beside `config_path`. | `paths()`, `write_proposed_config/prompt()`, `promote()`, `history()`, `diff()`, `TunerPaths` |

---

## `agent/` — the autonomous supervisor (Phase 7a)

| File | What it does | Key surface |
|------|--------------|-------------|
| `intake_watch.py` | The producer-agnostic signal handoff: polls `<intake_dir>/<network>/` for `*.json` candidate batches → parse → enqueue → archive to `processed/` (`failed/` + alert on bad content; nothing deleted). Enqueue happens **before** the move, so a crash in between re-parses into content-hash duplicates, never a double-queue. A 2s settle window skips files still being written. | `poll()`, `intake_dir()`, `IntakeResult` |
| `daily.py` | The daily job (§15.3–.5): journal yesterday (distilling the reflection lesson via the `submit_journal` tool), run both sample-gated tuners, auto-promote pending proposals on **paper only** (testnet/mainnet wait for a human `tune promote`), emit the `agent_daily_report` alert. | `run_daily()` |
| `supervisor.py` | One deterministic loop owning all cadences: intake poll every tick (new candidates trigger an exec pass immediately), exec + sentry passes on their intervals, the daily job at `HL_AGENT_DAILY_UTC` (meta-persisted — a restart never re-runs it; a start after the scheduled time still runs it), hourly heartbeat alert, exponential failure backoff. Passes are injected callables; LLM calls stay inside them. Last-run timestamps persist in state meta for cross-process `agent status`. | `Supervisor` (`tick()`, `run_forever()`), `Cadence`, meta keys `LAST_*` |

Wired by `hl agent run|status` (`cli/commands/agent.py`); cadences live on the
tunable surface (`TunableConfig.agent`, clamped); deploy templates in `deploy/`.

---

## `journal/` — the daily trade journal (Phase 7b)

| File | What it does | Key surface |
|------|--------------|-------------|
| `digest.py` | One UTC day of the state store, tallied deterministically: per-verdict lines (coin/action/conviction/**rationale** — a skip without its rationale is unauditable), gate-reason tally, opened/resolved trades with realized/R/expectancy/profit factor, sentry action counts, warning+ alert events, and a write-time snapshot that reconciles with `exec report`. | `build_digest()`, `render()`, `DayDigest`, `day_bounds()`, `utc_date()` |
| `narrative.py` | The LLM half: one opus call ("senior discretionary trader" persona) reflecting on the digest — judge process not just P&L. Out-of-path; input is our own tallied outcomes, never raw external text. | `narrate()` |
| `writer.py` | Digest + reflection → `journal/<network>/YYYY-MM-DD.md`. The narrative is cached per-date in state meta (one call per day, ever); a narrative failure degrades to a placeholder + `journal_narrative_failed` alert — the deterministic digest always writes. | `write_journal()`, `journal_path()` |

| `lessons.py` | The reflection memory's read side (§15.4): the bounded "recent lessons" block for the decision + management contexts. Hard caps `HL_AGENT_REFLECT_INJECT_MAX`/`_MAX_CHARS` bound it; tunable `agent.reflection_inject` switches it off; the decision log records which lesson dates were in context. | `recent_lessons()` |

Wired by `hl journal write|show|ls` and the agent's daily job (writes yesterday).
`agent.journal_narrative` (tunable) switches the LLM section; `HL_JOURNAL_MODEL` /
`HL_JOURNAL_MAX_TOKENS` cap it.

---

## `tests/` — 395 passing, keyless

Highest-risk code first: gate/sizing, the LLM-output validator/clamp, paper
exchange + monitor, intake idempotency + HWM, config-schema clamping, the mainnet
gate, protection/abort, graduation, alerts, key redaction, and the CLI. The LLM is
**always mocked** — executor tests inject a deterministic `decide_fn`; `decide`/tuners
are tested via `FakeClient`/`FakeTool`/`FakeText`. `_helpers.py` holds the fixtures.
