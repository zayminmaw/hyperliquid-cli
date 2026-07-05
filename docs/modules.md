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
| `llm.py` | The **one** lazy `anthropic` import. | `make_client()` |

`OrderResult` carries `filled_size` / `avg_price` so the executor can reconcile to
the *actual* fill rather than the intended order.

---

## `cli/` — the Typer app

| File | What it does |
|------|--------------|
| `app.py` | Builds the `hl` Typer app, parses global flags (`--network/--account/--json/--dry-run/-y`) into `GlobalState`, wires the command groups. |
| `context.py` | `GlobalState`; `build_for(state, for_write)` resolves account + key and enforces the mainnet gate (keys loaded only for writes); `typed_confirm`. |
| `commands/` | `account · markets · asset · trade · exec_ · config · tune` — noun→verb groups. |
| `output.py` | Rich-table / JSON rendering (`--json` switch). |
| `watch.py` | Poll-based `rich.Live` refresh for `-w` watch modes (positions/orders/book/price). |
| `stubs.py` | Phase-labelled placeholders so `hl --help` is fully navigable. |

Command surface: `account add|ls|set-default|remove|positions|orders|balances|portfolio` ·
`markets ls|prices` · `asset price|book` · `trade order|cancel|cancel-all|set-leverage`
(Mode A) · `exec propose|once|run|shadow|status|report|breaker` (Mode B) ·
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
| `decision.py` | The LLM call (lazy `anthropic`, `claude-sonnet-4-6`, forced strict rationale-first tool `submit_decision` — the 2-4-sentence rationale is the model's only reasoning space under a forced tool call; low temp sent only to models that accept it, incl. NOT Sonnet 5/Opus 4.7+/Fable) + validate/clamp. User turn = one task line + the context as compact JSON in a `<context>` tag. Carries `recheck_in_minutes` for WAIT timing (clamped to `[0,1440]`); `stop_reason` rides on `DecisionResult` so truncation/refusal drops are diagnosable. Schema-invalid → dropped + tallied, never guessed. | `decide()`, `validate_decision()`, `load_decision_prompt()`, `DecisionResult` |
| `gate.py` | The deterministic risk gate (first-failure wins, incl. mark sanity — mark present, inside sl/tp, R:R at mark ≥ floor) + fixed-fractional sizing **at the mark** + side inference. | `evaluate()`, `GateContext` (`mark=`), `GateOutcome`, `infer_side()` |
| `execute.py` | `fire()` records the idempotency key **before** placing → a crash skips (missed trade), never double-fires. Releases the key on a clean reject. | `fire()` |
| `protect.py` | Native exchange-side SL/TP reduce-only triggers; required on testnet/mainnet. Failed protection cleans up after itself (`cancel_placed`), and `cancel_coin_triggers` removes the surviving half of a pair after a close. | `requires_native_protection()`, `protective_orders()`, `place_protection()`, `emergency_close()`, `cancel_placed()`, `cancel_coin_triggers()`, `ProtectionResult` |
| `resolve.py` | The monitor step: close open trades on SL/TP/expiry → the `trades` ledger (won/lost/expired/closed, realized, R-multiple). On live networks it also reconciles **vanished** positions (native trigger fired on a wick, or a manual close — outcome inferred from candle extremes, else `closed` at mark), cancels surviving triggers after a close, and resolves shadow trades orderlessly. | `resolve_open_trades(…, shadow_only=)` |
| `monitor.py` | Read-only position-health view. | `position_health()` |
| `runner.py` | `run_once()` — the full pass orchestrator (resolve → re-check due WAIT deferrals → pull → enrich(+candles/regime/outcomes) → decide (skipped when the coin has no mark — the gate would reject anyway, so the paid call isn't spent) → defer-if-WAIT / gate → fire → open ledger row (**before** protection, so a crash never leaves an untracked position; abort resolves it `aborted`) → protect → log → advance HWM). An `act+wait` decision is parked in the `deferred` table and re-checked with fresh data (within freshness, up to `HL_FOLLOWUP_MAX_ATTEMPTS`, labeled `followup`); re-checks freeze while the breaker is tripped. Shadow books hypothetical trades; unmanaged exchange positions raise an edge-triggered alert. Honors `dry_run` (fully side-effect-free), `fire_enabled` (shadow), injected `decide_fn`, and an `Alerter`. | `run_once()`, `PassSummary` (`seen/rechecked/approved/fired/rejected/failed/dropped/deferred/resolved`) |

---

## `state/` — durable SQLite (network-scoped)

`store.py` — one DB per network (`state-<network>.db`). Holds: the intake stream +
high-water mark, idempotency keys, the decision log, the `trades` ledger (with a
`shadow` flag for hypothetical trades; additive column migrations run on open), the
`deferred` table (WAIT candidates parked for re-check), the paper book, the breaker
flag, and a `meta` key/value table. `resolved_trades(limit=N)` returns the most
recent N (newest-closed first).

Key surface (`StateStore`): `enqueue` · `pull_new` · `get_hwm`/`advance_hwm` ·
`set_status` · `already_fired`/`record_fire`/`release_fire` · `log_decision`/`recent_decisions` ·
`open_trade`/`open_trades`/`resolve_trade`/`resolved_trades` ·
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

## `tuner/` — self-tuning (out-of-path, propose→approve)

| File | What it does | Key surface |
|------|--------------|-------------|
| `stats.py` | Resolved-trade cohorts (coin × side × conviction-bucket), win-rate + avg-R; sample-gated (`MIN_COHORT_SAMPLES=5`). | `cohorts()`, `summary()`, `conviction_bucket()`, `Cohort` |
| `config_tuner.py` | Propose tunable-surface edits (`claude-opus-4-8`, forced strict `submit_config`; every field description states its units + clamp bounds — strict mode can't encode numeric ranges, so descriptions are the model's only channel for them); clamped on propose. No eligible cohort ⇒ model not called. | `propose_config()`, `ConfigProposal` |
| `prompt_tuner.py` | Refine the decision prompt from decisions-vs-outcomes (`claude-opus-4-8`, text). Pairs include the decision **rationale** (which reasoning won/lost is the point of tuning a prompt); the current prompt goes to the model in a tag, not JSON-escaped; a fenced output is stripped before it can reach `promote`. | `propose_prompt()`, `PromptProposal` |
| `promote.py` | proposed → active (config re-clamped); promotion **consumes** the proposal file (promotable exactly once) and the `promotions.jsonl` audit records what went live (full config / prompt hash+size) + `diff`/`history`. Artifacts live beside `config_path`. | `paths()`, `write_proposed_config/prompt()`, `promote()`, `history()`, `diff()`, `TunerPaths` |

---

## `tests/` — 235 passing, keyless

Highest-risk code first: gate/sizing, the LLM-output validator/clamp, paper
exchange + monitor, intake idempotency + HWM, config-schema clamping, the mainnet
gate, protection/abort, graduation, alerts, key redaction, and the CLI. The LLM is
**always mocked** — executor tests inject a deterministic `decide_fn`; `decide`/tuners
are tested via `FakeClient`/`FakeTool`/`FakeText`. `_helpers.py` holds the fixtures.
