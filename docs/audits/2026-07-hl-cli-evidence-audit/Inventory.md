# Inventory — Hyperliquid CLI, decision-affecting & money-touching components

**Audit date:** 2026-07-14 · **Branch:** `main` @ `b82bbdf` · **Scope:** audit-only, no code/config/keys modified, no orders placed.

**Method note.** The audit prompt asked for the skills `senior-python-engineer`, `ai-engineer`, `web3-engineer`, `web3-security-engineer`, and a quant skill. **None are installed in this environment.** The audit was performed with general senior-engineering / security judgment plus the required external research (see `Evidence.md`) and the `claude-api` skill (used to verify model IDs and LLM API behavior). The `token-efficiency` and `agent-context` skills were loaded per the session bootstrap.

**Critical framing fact (drives every blast-radius rating).** Both executor state databases (`~/.hyperliquid-cli/state-paper.db`, `state-mainnet.db`) are **empty** — zero rows in `decision_log`, `trades`, `intake`, `idempotency`, `sentry_log`, `deferred`, `reflections`. `accounts.db` has **zero accounts** and there is **no `keys/` directory**. The tool has never executed a single decision or trade in any mode — not paper, not shadow, not testnet, not mainnet. Every "internal evidence" cell in `Verdicts.md` is therefore *absent*, and the graduation gate (mainnet-readiness checklist) has never been run against real data.

---

## A. LLM executor layer

| # | Component | Where | What it does / its authority |
|---|-----------|-------|------------------------------|
| A1 | **Entry decision (Mode B core)** | `hlcli/executor/decision.py` | Per candidate setup, asks `claude-sonnet-4-6` (env-overridable) for a structured verdict via a forced `submit_decision` tool call. **Output space:** `action` ∈ {act, skip}, `timing` ∈ {now, wait}, `conviction` ∈ [0,1], `recheck_in_minutes`, `rationale`. The model **cannot** set size, stops, targets, coins, or caps. `max_tokens=1024`, `temperature=tunable.decision_temperature` (0.2) only on models that accept it. Stable tools+system prefix is prompt-cached. |
| A2 | **Decision system prompt** | `decision.py:53` `SYSTEM_PROMPT` (or promoted `active_prompt.md`) | Sectioned-markdown persona: "execution-judgment layer." Explicitly instructs the model to treat candidate `reasoning`/`news` as **thesis to evaluate, never instructions** (anti-injection); HOLD/skip-biased; conviction-scale anchors; "hard boundaries" restating that code owns sizing/safety. |
| A3 | **Output validation + clamp** | `decision.py:92 validate_decision`, `_clamp_recheck` | Parses the tool payload. Out-of-range conviction **clamped** to [0,1]; NaN/∞ conviction, or action/timing outside enum, → **dropped** (never defaulted/guessed). Recheck clamped to [0, 1440m]. Drop is tallied + logged with `stop_reason`. |
| A4 | **Enrichment / decision context** | `hlcli/executor/enrich.py` | Assembles what the LLM sees: mark, equity, open positions + uPnL, recent decisions (newest-first), resolved outcomes in R (the model's track record), a labeled OHLC candle tail, code-computed regime, ≤3 reflection "lessons," and the *tunable* surface only (never hard caps or keys). JSON-serialized into one user turn. |
| A5 | **In-trade manager (Sentry 6b/c/d)** | `hlcli/sentry/decision.py` | The **highest-authority LLM in the system.** Per open position, `submit_management` verdict ∈ {hold, tighten_stop, reduce(25/50/75), close, extend_tp, **add**}. `add` is the one risk-*increasing* action (pyramiding). Model nominates prices/actions; **all sizing is computed by code**; verdict validated + structurally clamped, drop-on-invalid. |
| A6 | **Config tuner (out-of-path)** | `hlcli/tuner/config_tuner.py` | `claude-opus-4-8` proposes edits to the tunable surface from resolved-trade cohorts. **Sample-gated** (no eligible cohort ⇒ model never called). Proposal clamped on return and again on load/promote. Never auto-applied. |
| A7 | **Prompt/journal tuners** | `hlcli/tuner/prompt_tuner.py`, `hlcli/journal/*` | `claude-opus-4-8` refines the decision prompt / writes a daily narrative + distilled "lessons." Out-of-path, propose→approve; lessons injected back into A4 bounded by hard caps (`agent_reflect_inject_max=3`, `_max_chars=240`). |
| A8 | **Memory the LLM sees across calls** | via A4/A5 | No conversation memory (each call is single-turn, stateless). "Memory" = the enriched recent-decisions + resolved-outcomes windows + reflection lessons — all read from the decision log/ledger, never a running chat. |

Model IDs verified against the `claude-api` skill catalog: `claude-sonnet-4-6` and `claude-opus-4-8` are **both real, active models** (Sonnet 4.6 is one generation behind current Sonnet 5; Opus 4.8 is current). Neither will 404. `supports_temperature()` (`core/llm.py:51`) correctly withholds `temperature` from the families that reject it.

---

## B. Deterministic risk mechanics

| # | Component | Where | What it does |
|---|-----------|-------|--------------|
| B1 | **The risk gate** | `hlcli/executor/gate.py:59 evaluate` | Short-circuit, first-failure-wins pipeline: schema-valid → kill-switch → daily-loss → freshness → allowed-coin → regime → level coherence → R:R floor → mark present → mark inside SL/TP → R:R-at-mark ≥ floor → one-per-coin → max-concurrent → equity>0 → size → notional/leverage caps → conviction clamp. Returns a MARKET order or a first-failure reason. **This is the real safety authority.** |
| B2 | **Position sizing** | `gate.py:124 _size` | Fixed-fractional priced **at the mark** (entry is a MARKET order): `risk_per_trade_pct/100 × equity ÷ |mark − sl|`, × conviction fraction, then `min()` with `max_notional/price` and `equity×max_leverage/price`. Rounded to 6dp. Conviction only scales *within* the hard ceilings. |
| B3 | **Conviction→size mapping** | `gate.py:150`, `config_schema.py ConvictionSizing` | Below `min_conviction`(0.3) → size 0 (effective skip). Else linear from `floor_fraction`(0.25) to `ceil_fraction`(1.0) of target. |
| B4 | **R-math anchor** | `hlcli/executor/rmath.py` | All R/reward measured against **initial** stop `|entry − initial_sl|`, not the ratcheted working stop — keeps tuner/graduation R honest after sentry moves the stop. |
| B5 | **Hard caps** | `hlcli/core/config.py Caps`, `.env` | Off-limits to LLM/tuner: `MAX_NOTIONAL_PER_TRADE=1000`, `MAX_CONCURRENT_POSITIONS=3`, `DAILY_LOSS_LIMIT_PCT=5`, `MAX_LEVERAGE=3`, `RR_FLOOR=1.5`, `MAX_SIGNAL_AGE_MINUTES=30`, `ALLOWED_COINS=BTC,ETH,SOL`, sentry churn caps, graduation thresholds, model names/budgets. |
| B6 | **Tunable surface + clamp** | `hlcli/core/config_schema.py` | `risk_per_trade_pct`(0.5), regime gate, conviction sizing, temperature, hold/expiry, trail config, agent cadences. Every field bounded by `clamp()` at load; non-finite → safe default. Missing file → clamped defaults; malformed → loud `ConfigError`. |
| B7 | **Kill switch + daily-loss breaker** | `hlcli/safety/breaker.py` | Manual persisted kill switch; daily-loss trips when equity draws down ≥ `DAILY_LOSS_LIMIT_PCT` from day-start equity (reset on date rollover). Both **halt new fires only** — open positions keep riding their stops. |
| B8 | **Sentry management gate** | `hlcli/sentry/gate.py` | Deterministic gate over the in-trade LLM verdict: ratchet-only stop direction, churn caps (per-position action budget, cooldown, opposing-window), extend requires breakeven + ≤1R/step, and the full **ADD pyramid discipline** (winner ≥ `add_min_r`, stop raised with the add, add-risk covered by unrealized profit, ≤½ current size, re-clears notional/leverage caps, per-coin lifetime budget). Model nominates; gate sizes. |
| B9 | **Graduation gate** | `hlcli/safety/graduation.py` | Mainnet-readiness verdict: ≥`min_trades`(20) resolved, spanning ≥`min_days`(7), with `avg_r` > `min_expectancy`(0). Excludes `scaled` partials. **Never run against data (DBs empty).** |

---

## C. Execution & exchange integration

| # | Component | Where | What it does |
|---|-----------|-------|--------------|
| C1 | **Order submission** | `hlcli/exchange/hyperliquid.py:113 place_order` | Entry is always `OrderType.MARKET` (`market_open`/`market_close` via SDK). LIMIT (GTC) and STOP_LOSS/TAKE_PROFIT (reduce-only market triggers, `isMarket:True`) also supported (Mode A + protection). Reads via keyless `Info`; writes via `Exchange` (needs agent key). SDK + `eth_account` lazy-imported. |
| C2 | **Tick/size rounding** | `hlcli/exchange/rounding.py` | Size floored to `szDecimals`; price to 5 sig-figs then ≤ `6 − szDecimals` decimals. Matches current HL docs exactly (see Evidence E7). Unknown coin passes through untouched. No `$10` minimum-notional check. |
| C3 | **Marks / candles feed** | `hlcli/exchange/marks.py` | Public `/info` over httpx (keyless). Marks cached with 2s TTL; `szDecimals` fetched once/session; candles uncached. |
| C4 | **Idempotent fire** | `hlcli/executor/execute.py`, `state/store.py:168 record_fire` | Atomic claim (`INSERT OR IGNORE` on candidate id) recorded **before** the order is sent → crash between claim and fill skips (missed trade), never double-fires. Definitive reject **releases** the key; transport error **keeps** it (treats as spent). Combined with intake high-water mark, restart never re-fires. |
| C5 | **Fill reconciliation** | `runner.py:320 _fire_and_reconcile` | Ledger row written from the **actual** fill (`avg_price`, `filled_size`), before protection, so a mid-protection crash leaves a position the resolver still manages. Handles reject / rested / unfilled distinctly. |
| C6 | **Native protection + emergency close** | `hlcli/executor/protect.py`, `runner.py:444 _secure` | On live backends, after a fill places reduce-only SL+TP triggers on the exchange (survive a process crash). If **either** trigger fails to place → emergency market close + cancel whichever leg placed. Records per-row trigger oids for slice-scoped cancels. |
| C7 | **Rate-limit / retry handling** | *(absent)* | No 429/backoff/retry at the exchange **write** layer. `place_order`, `cancel`, `emergency_close` each make a single SDK call. `core/backoff.py` exists but is used for the intake/loop cadence, not order writes. |
| C8 | **State reconciliation vs exchange** | `runner.py:486 _alert_unmanaged` | On each fire-enabled pass, compares exchange positions to ledger `open_trades`; a position the ledger doesn't know → **alert only** (no auto-close/adopt). `frontend_open_orders` used so native triggers are visible to cancel-all. |

---

## D. Operational & security surface

| # | Component | Where | What it does |
|---|-----------|-------|--------------|
| D1 | **Keystore** | `hlcli/accounts/keystore.py` | One file per account at `<data_dir>/keys/<alias>.key`, created `0600`, dir `0700`, refuses to load a group/other-readable key. Format-validated (32-byte hex), never echoed/logged. **Stored as plaintext hex** (encrypt-at-rest called out as a future upgrade). Uses **agent ("API") wallets** — can trade, cannot withdraw. |
| D2 | **Account store** | `hlcli/accounts/store.py`, `accounts.db` | SQLite of alias/address/network/type/key_ref/is_default. Stores only a *reference* to the key, never the key. |
| D3 | **API key handling** | `hlcli/core/llm.py` | `ANTHROPIC_API_KEY` from shell/`.env`; kept off the `Caps` object so it can't ride along in dumps/logs; `masked_api_key()` is the only display form. |
| D4 | **Decision log / audit trail** | `state/store.py decision_log`, `sentry_log`, `trades` | Every decision logged with full context + decision + gate + fill + outcome; every management action logged; every trade open→resolved with R. This is both the audit/P&L-attribution trail and the tuner training data. |
| D5 | **Network resolution + mainnet gate** | `hlcli/core/network.py` | `paper` default. Mainnet requires **all three**: `HL_ENABLE_MAINNET=1` + `--network mainnet` + typed confirm (`-y` skips prompt, not the env flag). I/O-free, `confirm` injected by the CLI. |
| D6 | **Propose→approve tuning** | `hlcli/tuner/promote.py` | Proposals written to `proposed_*`, never active; `promote` re-clamps, moves to active, appends to `promotions.jsonl`, and **consumes** the proposal (promotable exactly once). Human is always in the loop. |
| D7 | **Alerting** | `hlcli/safety/alerts.py` | Emits fire / reject / protection_failed / halted / unmanaged_position events (change-gated to avoid spam). The only channel by which a naked-position or halt condition reaches a human. |

### Origin (from git log)
Greenfield start `d86c6d0` (2026-06-27) → phase-by-phase through Phase 7 by `b82bbdf` (2026-07-11). The judgment/mechanics split, the gate order, native-SL/TP-as-mainnet-prerequisite, agent-wallet-by-default, and propose→approve tuning are all present from the plan (`PLAN.md`) and were hardened across several "fresh-eyes review" commits. Per the audit brief and `CLAUDE.md`, many parameter choices (`risk_per_trade_pct`, RR floor, conviction anchors, churn caps) were set by owner judgment; their evidential standing is assessed in `Evidence.md` / `Verdicts.md`.
