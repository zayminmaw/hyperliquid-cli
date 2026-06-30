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
| `base.py` | The `Exchange` Protocol both backends satisfy. | `Exchange`, methods: `get_marks/get_book/equity/get_positions/get_open_orders/place_order/cancel/cancel_all/set_leverage` |
| `marks.py` | Public `/info` reads over **httpx** (keyless), TTL-cached. | `MarksFeed`, `api_url()` |
| `paper.py` | Simulated book on public marks; state-backed (persists in `state-paper.db`). | `PaperExchange` |
| `hyperliquid.py` | Live testnet/mainnet. Reads keyless; writes need the agent key (SDK + `eth_account` lazy-imported). | `HyperliquidExchange` (key only in `_agent_key`) |
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
| `enrich.py` | Assemble the LLM's input: marks, equity, positions, P&L, recent decisions, tunable surface. `regime=None` (no price-history feed yet). | `enrich()`, `EnrichedContext` |
| `decision.py` | The LLM call (lazy `anthropic`, `claude-sonnet-4-6`, forced strict tool `submit_decision`, low temp) + validate/clamp. Schema-invalid → dropped + tallied, never guessed. | `decide()`, `validate_decision()`, `load_decision_prompt()`, `DecisionResult` |
| `gate.py` | The deterministic risk gate (first-failure wins) + fixed-fractional sizing + side inference. | `evaluate()`, `GateContext`, `GateOutcome`, `infer_side()` |
| `execute.py` | `fire()` records the idempotency key **before** placing → a crash skips (missed trade), never double-fires. Releases the key on a clean reject. | `fire()` |
| `protect.py` | Native exchange-side SL/TP reduce-only triggers; required on testnet/mainnet. | `requires_native_protection()`, `protective_orders()`, `place_protection()`, `emergency_close()`, `ProtectionResult` |
| `resolve.py` | The monitor step: close open trades on SL/TP/expiry → the `trades` ledger (won/lost/expired, realized, R-multiple). | `resolve_open_trades()` |
| `monitor.py` | Read-only position-health view. | `position_health()` |
| `runner.py` | `run_once()` — the full pass orchestrator (resolve → pull → enrich → decide → gate → fire → reconcile → protect → log → advance HWM). Honors `dry_run`, `fire_enabled` (shadow), injected `decide_fn`, and an `Alerter`. | `run_once()`, `PassSummary` |

---

## `state/` — durable SQLite (network-scoped)

`store.py` — one DB per network (`state-<network>.db`). Holds: the intake stream +
high-water mark, idempotency keys, the decision log, the `trades` ledger, the paper
book, the breaker flag, and a `meta` key/value table.

Key surface (`StateStore`): `enqueue` · `pull_new` · `get_hwm`/`advance_hwm` ·
`set_status` · `already_fired`/`record_fire`/`release_fire` · `log_decision`/`recent_decisions` ·
`open_trade`/`open_trades`/`resolve_trade`/`resolved_trades` ·
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
| `config_tuner.py` | Propose tunable-surface edits (`claude-opus-4-8`, forced strict `submit_config`); clamped on propose. No eligible cohort ⇒ model not called. | `propose_config()`, `ConfigProposal` |
| `prompt_tuner.py` | Refine the decision prompt from decisions-vs-outcomes (`claude-opus-4-8`, text). | `propose_prompt()`, `PromptProposal` |
| `promote.py` | proposed → active (config re-clamped) + `promotions.jsonl` audit + `diff`/`history`. Artifacts live beside `config_path`. | `paths()`, `write_proposed_config/prompt()`, `promote()`, `history()`, `diff()`, `TunerPaths` |

---

## `tests/` — 153 passing, keyless

Highest-risk code first: gate/sizing, the LLM-output validator/clamp, paper
exchange + monitor, intake idempotency + HWM, config-schema clamping, the mainnet
gate, protection/abort, graduation, alerts, key redaction, and the CLI. The LLM is
**always mocked** — executor tests inject a deterministic `decide_fn`; `decide`/tuners
are tested via `FakeClient`/`FakeTool`/`FakeText`. `_helpers.py` holds the fixtures.
