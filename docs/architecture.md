# Architecture

System overview for `hl` — a Python CLI that trades on Hyperliquid in two modes:
manual human orders (Mode A) and an LLM-driven executor (Mode B). The whole design
hangs off one rule:

> **The LLM owns judgment; deterministic code owns mechanics and safety.**

The LLM decides *act/skip, timing, conviction* per candidate setup. Code decides
everything that touches money: sizing math, SL/TP placement, risk caps, rounding,
idempotency, the kill switch. The LLM's output is an **input to the risk gate, never
a bypass of it** — it is validated and clamped before anything reaches the exchange.

---

## Component map

```
hlcli/
├── cli/        # typer app, command groups, global flags, rich/json output, watch loops
├── core/       # config (hard caps) · config_schema (tunable+clamp) · types · network gate · llm client
├── exchange/   # Exchange protocol · paper book · hyperliquid (testnet/mainnet) · marks feed · factory
├── accounts/   # sqlite account store + 0600 keystore
├── executor/   # intake → enrich(+candles/regime) → decision(LLM) → gate → execute → protect → resolve → runner
├── tuner/      # stats cohorts · config_tuner · prompt_tuner · promote (propose→approve)
├── state/      # sqlite: intake stream, HWM, idempotency, decision log, trades, paper book
└── safety/     # breaker (kill switch + loss limit) · alerts (JSONL+stderr) · graduation
```

### Layer responsibilities

| Layer | Owns | Never does |
|-------|------|-----------|
| `cli/` | Parse flags, resolve network/account, render output | Sizing or risk decisions |
| `core/config` (hard caps) | The immutable box: notional/leverage ceilings, loss limit, allowed coins, mainnet gate | Get rewritten by the tuner |
| `core/config_schema` (tunable) | The values the tuner may change — **clamped on load** | Reach the order path unclamped |
| `executor/decision` (LLM) | Judgment: act/skip, timing, conviction | Touch money, bypass the gate |
| `executor/gate` | All money math + every risk check, first-failure wins | Trust LLM output blindly |
| `exchange/` | Placing/cancelling orders, reading marks/positions | Decide whether to trade |
| `safety/` | Kill switch, loss limit, alerts, mainnet-readiness verdict | — |
| `state/` | Durable book, idempotency, audit log — restart safety | — |

---

## The executor pass (Mode B data flow)

One `run_once` pass (`hlcli/executor/runner.py`) is the heart of Mode B:

```
                ┌──────────────────────────────────────────────────────────┐
                │  resolve open trades (SL/TP/expiry → ledger)             │  ← monitor step
                │  then re-check any due WAIT deferrals with fresh data    │
                └───────────────────────────┬──────────────────────────────┘
                                                 ▼
   intake stream ──pull_new(>HWM)──►  for each candidate:
   (exec propose)                          │
                                           ▼
                                   enrich  (marks, equity, positions, recent decisions, tunable,
                                           │   candle tail + regime via Kaufman ER — None if no feed)
                                           ▼
                                   decide  (LLM: claude-sonnet-5, strict tool, low temp)
                                           │   schema-invalid → DROP + tally + log (never guessed)
                                           │   act + wait → DEFER (park for re-check, advance HWM)
                                           ▼
                                   gate    (deterministic, first-failure wins)
                                           │
                          ┌────────────────┴────────────────┐
                       rejected                          approved
                          │                                 │
                          ▼                                 ▼
                   log + advance HWM                fire (MARKET entry)
                                                          │ idempotency key recorded BEFORE placing
                                                          ▼
                                                  reconcile to actual fill
                                                  (filled_size / avg_price)
                                                          │
                                              ┌───────────┴───────────┐
                                          unfilled                 filled
                                              │                       │
                                              ▼                       ▼
                                        no position        place native SL/TP triggers
                                                                      │ (testnet/mainnet)
                                                          ┌───────────┴───────────┐
                                                      protected               can't protect
                                                          │                       │
                                                          ▼                       ▼
                                                   open_trade ledger      emergency MARKET close
                                                   + fire alert           status=aborted (naked never)
                                                          │
                                                          ▼
                                                  log decision + advance HWM
```

Three knobs shape a pass:

- `dry_run` — compute everything, **mutate nothing** (side-effect-free preview).
- `fire_enabled=False` — **shadow** mode: decide, gate, log, but fire nothing. Gate-approved decisions are booked as **hypothetical trades** (`trades.shadow = 1`, entered at the mark) and resolved orderlessly at their SL/TP/expiry by later shadow passes — that resolved record is what feeds the tuner and the graduation checklist before any real money moves. The hypothetical book also honors one-per-coin/max-concurrent, and a shadow pass never touches real trades (it may hold a read-only exchange).
- `decide_fn` — injected so tests drive the mechanics with a deterministic decider; the real LLM call is mocked, never hit in tests.

### The WAIT → follow-up loop

When the LLM returns *act + wait* with a `recheck_in_minutes`, the candidate isn't
rejected — it's **deferred**. The runner intercepts this *before* the gate (so the
gate stays a pure act-now decision), parks the candidate in the `deferred` table, and
advances the HWM. Each pass re-checks any due deferrals first, with **fresh**
enrich/candles/regime. Re-checks are scheduled *within* the candidate's freshness
window and capped at `HL_FOLLOWUP_MAX_ATTEMPTS` (no room or attempts left ⇒ terminal
reject). A tripped breaker **freezes** re-checks (attempts preserved); `dry_run`
skips them. `PassSummary` reports `rechecked` and `deferred`; `exec report` and
`exec status` surface the parked count.

### The risk gate order (first-failure wins)

`hlcli/executor/gate.py` — a short-circuit pipeline. The first failing check returns its reason:

```
schema-valid decision → kill switch → daily-loss-limit → freshness
  → allowed-coin → regime sanity → level sanity (entry/sl/tp coherent)
  → R:R floor → mark sanity (mark present, inside sl/tp, R:R at mark ≥ floor)
  → one-per-coin → max-concurrent → equity>0
  → sizing + notional cap + leverage cap → conviction→size clamp
```

**Mark sanity** exists because the entry is a MARKET order: the mark — not the
proposed entry — is what the fill will pay. A missing mark, a mark outside the
sl/tp band, or an entry that has run far enough that reward:risk *measured from
the mark* no longer clears the floor is rejected in code, regardless of what the
LLM thought of the timing.

**Sizing** is fixed-fractional and priced at the mark: `risk_per_trade_pct ×
equity ÷ |mark − sl|`, then scaled by a conviction fraction, then clamped by
`max_notional_per_trade` and `max_leverage` (both computed at the mark).
Conviction only scales size *within* the hard caps — it can never raise the
ceiling. One-per-coin makes the per-trade cap the total per-coin cap. The
leverage ceiling is per-order; aggregate exposure is bounded by
`max_concurrent_positions × max_notional_per_trade`.

---

## Networks

`paper` (default everywhere) → `testnet` → `mainnet`.

| Network | Money | Fills | Keys | Notes |
|---------|-------|-------|------|-------|
| `paper` | simulated | simulated book on **public mainnet marks** | none | keyless + SDK-free; the test/dev default |
| `testnet` | fake | real exchange fills | agent wallet | native SL/TP triggers required |
| `mainnet` | real | real | agent wallet | **gated**: `HL_ENABLE_MAINNET=1` + `--network mainnet` + typed confirm; native SL/TP is a hard prerequisite |

Reads (marks, book, positions) go over **httpx** against the public `/info` endpoint
so paper mode never needs the SDK or a key. The `hyperliquid-python-sdk` and
`eth_account` are **lazy-imported** and used only for signing writes.

---

## Self-tuning (out-of-path, propose→approve)

Both tuners run **outside** the order path and **propose** changes a human approves
before they go live — never auto-applied.

```
resolved trades ──► stats.cohorts (sample-gated: <5 samples ⇒ no model call)
                         │
                         ▼
   config_tuner (opus-4-8, strict tool) ──► proposed_config.json   (clamped on propose)
   prompt_tuner (opus-4-8, text)        ──► proposed_prompt.md
                         │
              hl tune diff / promote / history
                         ▼
   promote: proposed_* → active_*  (config re-clamped) + promotions.jsonl audit
```

The decision-prompt and tunable config are the only things a tuner can move, and
every path that consumes them re-clamps. See [decisions.md](./decisions.md).

---

## Where the audit trail lives

Every decision is logged to the network-scoped SQLite store with full input context
+ the decision + the gate outcome + the resulting fill. That decision log is both the
audit trail (P&L attribution / replay) and the training substrate for the tuners.
Resolved trades land in a separate `trades` ledger (won/lost/expired, realized P&L,
R-multiple) which feeds cohorts and the graduation verdict.

See [modules.md](./modules.md) for per-module detail and [setup.md](./setup.md) to run it.
