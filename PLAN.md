# Hyperliquid CLI — Build Plan

A Python CLI for trading on Hyperliquid, with an LLM-driven executor that makes
the discretionary "what to trade / when to fire" call, a self-tuning loop that
optimizes that decision-making over time, and clean separation between **paper**,
**testnet**, and **mainnet**.

This is a greenfield plan. It keeps the parts of the old ThirdEye executor that
earned their place (deterministic risk gate, clamped config, idempotency,
out-of-path tuner) and drops the parts that don't fit this project (the ThirdEye
Postgres coupling, the "no LLM in the order path" rule, the mainnet block).

---

## 1. The core idea: split judgment from mechanics

The old executor kept the LLM **out** of the order path on purpose —
nondeterminism on real money, broken P&L attribution. You want the opposite: the
LLM _is_ the executor. The way to get that without throwing away auditability is
to draw the line in a different place:

- **The LLM owns judgment.** Given candidate setups + live market + portfolio +
  recent outcomes, it decides _which_ candidates to take, _when_ to fire (now /
  wait / skip), and a _conviction_ score.
- **Deterministic code owns mechanics.** Sizing math, SL/TP placement, risk
  caps, tick/size rounding, idempotency, the kill switch. The LLM's output is
  **validated and clamped** before anything reaches the exchange — it can never
  exceed a hard cap, pick a disallowed coin, or skip the gate.

So the LLM gets full discretion _inside a box the code draws_. Every decision is
logged with its complete input context and the resulting fill + outcome — which
is exactly the dataset the self-tuner needs, and which keeps P&L attribution
intact (you can always replay why a trade was taken).

This is the one design decision everything else hangs off. Flagging it up front
because it inverts the old plan's central rule.

---

## 2. Two trading modes

### Mode A — Normal (manual) trade

Direct, human-in-control orders. No LLM, no gate beyond hard caps and exchange
validation. This is the `chrisling-dev/hyperliquid-cli` surface, rebuilt in
Python: `hl trade order limit|market|stop-loss|take-profit`, `cancel`,
`cancel-all`, `set-leverage`. Used for discretionary trading, testing fills, and
manually closing what the executor opened.

### Mode B — LLM executor trade

You (or an upstream source) submit **candidate setups** — each with `pair`,
`entry`, `tp`, `sl`, `reasoning`, `news`. The LLM does **not** blindly execute
them. It decides, per candidate:

- **act / skip** — is this worth taking at all, given the book and the regime?
- **timing** — fire now, or wait for a better price / condition?
- **conviction** — maps to size _within_ the deterministic bounds.

So the human supplies the _thesis_; the LLM supplies the _execution judgment_;
the code supplies the _safety and math_. This is the "senior discretionary
trader" role from the old plan, moved into the order path on purpose.

> **Open question (see §13):** do you also want the LLM to _generate_ its own
> candidates from market + news, or only choose among the ones you feed it? This
> plan assumes **choose-among-supplied** as the primary path, with autonomous
> generation as a later extension.

---

## 3. Networks: paper → testnet → mainnet

| Network   | Money                 | Keys                              | Marks                | Default |
| --------- | --------------------- | --------------------------------- | -------------------- | ------- |
| `paper`   | none (simulated book) | none                              | public mainnet marks | ✅      |
| `testnet` | fake (real fills)     | funded testnet wallet / agent key | testnet `/info`      |         |
| `mainnet` | **real**              | funded mainnet wallet / agent key | mainnet `/info`      |         |

`paper` is the default everywhere. `mainnet` is **gated**, not blocked:

- Requires an explicit env flag (`HL_ENABLE_MAINNET=1`) _and_ `--network mainnet`
  _and_ a typed confirmation (or `-y` for automation, but only with the env flag set).
- Enforces hard caps that neither the LLM nor the tuner can touch.
- Recommended **graduation checklist** before first real order: N days / N
  resolved trades of positive testnet (or shadow) expectancy, reviewed in the report.
- **Native exchange-side SL/TP is a mainnet prerequisite** (§7). On real money
  you don't want a crashed executor to leave a position unprotected.

---

## 4. Command surface

Noun → verb, borrowed from the reference repo. Global flags replace its
`--testnet` with a three-way `--network`.

```
hl account   add | ls | set-default | remove | positions | orders | balances | portfolio
hl markets   ls | prices
hl asset     price <coin> | book <coin>                 # -w for live watch
hl trade     order limit|market|stop-loss|take-profit
             cancel | cancel-all | set-leverage          # Mode A
hl exec      propose | once | run | shadow | status | report | breaker   # Mode B
hl tune      run | diff | promote | history
hl config    show | set | edit
```

**Global flags:** `--network paper|testnet|mainnet` · `--account <alias>` ·
`--json` (scriptable output) · `--dry-run` · `-y` (skip confirms).

Key `exec` commands:

- `hl exec propose --pair BTC --entry 60000 --tp 64000 --sl 58500 --reason "…" --news "…"`
  → drops a candidate into the intake queue (also accepts a JSON/file batch).
- `hl exec once` → one full pass: intake → enrich → LLM decision → gate → fire → monitor.
- `hl exec run` → continuous loop (systemd / tmux / cron).
- `hl exec shadow` → run the LLM decision and log it, but **fire nothing**.
  This builds confidence pre-mainnet and generates tuner training data.
- `hl exec breaker` → toggle the kill switch (halts new fires; open positions still managed).

---

## 5. The executor pass

```
resolve open positions vs marks
  → kill switch? (breaker / daily-loss-limit hit → manage only, no new fires)
  → pull new candidates from intake
  → enrich: marks, portfolio state, regime signal, recent resolved outcomes
  → LLM decision  (structured JSON, per candidate)
  → deterministic risk gate  (validate + clamp + caps)
  → execute approved orders
  → log decision + fill (full context)
  → monitor SL/TP/expiry
```

Idempotency + a high-water mark on the intake stream mean a restart never
double-fires. Same discipline as the old executor.

### The risk gate (deterministic, first-failure wins)

The LLM's decision is an _input_ to the gate, not a bypass of it:

```
schema-valid LLM output → kill switch → daily-loss-limit → freshness
  → allowed-coin → regime sanity → level sanity (entry/sl/tp coherent)
  → R:R floor → one-per-coin → max-concurrent
  → sizing + notional cap + leverage cap → conviction→size clamp
```

- **Sizing:** fixed-fractional — `risk_per_trade_pct × equity ÷ stop_distance`,
  clamped by `max_notional_per_trade` and `max_leverage`. Conviction only scales
  size _within_ those bounds; it can never raise the ceiling.
- **One-per-coin** makes the per-trade cap the total exposure cap per coin.
- Any LLM output that fails schema validation is dropped and tallied, not guessed at.

---

## 6. The LLM decision layer

**Inputs (the decision context):** candidate setups; current marks; open
positions + equity + realized/unrealized P&L; regime signal; a rolling window of
recent resolved trades (what worked / what didn't); the tunable strategy config.

**Output (strict JSON schema):** per candidate — `action` (act/skip), `timing`
(now/wait), `conviction` (0–1), `rationale` (short). Plus an optional
portfolio-level note. Enforced via tool/structured output, low temperature,
validated and clamped before the gate.

**Models:**

- _Order-path decision_ → `claude-sonnet-4-6`. Lower latency and cost for the
  hot loop; the gate is the real safety authority, so the decision model doesn't
  need to be the largest.
- _Daily tuner_ → `claude-opus-4-8`. Runs out-of-path, once a day, where deeper
  reasoning is worth the cost.

(Both configurable via `.env`.)

**Determinism mitigations:** structured schema + validation + clamp; the gate is
final; `shadow` mode lets you watch decisions without risk; every decision is
logged for replay and attribution.

---

## 7. Mainnet hardening

Things that are optional on paper/testnet but **required** before real money:

- **Native exchange-side SL/TP trigger orders.** Today's plan watches SL/TP in
  the executor; a crashed process leaves positions naked. On mainnet, place the
  protective orders _on the exchange_ at entry time. (The reference repo already
  exposes `stop-loss` / `take-profit` trigger orders — reuse that path.)
- **Mainnet env gate + typed confirmation** (§3).
- **Hard caps the LLM and tuner cannot touch** (§9).
- **Kill switch + daily-loss-limit** that halt new mainnet fires automatically.
- **Key handling review** (§8) — agent wallets, no keys in logs.
- **Alerting** on fires, rejects, breaker trips, loss-limit hits.

---

## 8. Accounts & keys

Borrowed wholesale from the reference repo, because it's the right model:

- Local **SQLite** account store (`~/.hyperliquid-cli/accounts.db`): alias,
  address, network, type (`trade` / `read-only`), key reference.
- **Agent ("API") wallets** for trading — they can trade but not withdraw, so the
  key is far safer to hold in config than a main wallet key.
- **Read-only accounts** for monitoring with no key.
- Default-account selection so commands don't need `--account` every time.

Given your Web3-security lens: keys never hit logs or the decision context;
stored with locked file permissions (encrypt-at-rest is a reasonable upgrade);
agent-wallet-by-default for any trading account.

---

## 9. Config model: hard vs tunable

Two layers, same split as the old plan — and this split is what makes the
self-tuning safe:

- **`.env` — hard caps. Off-limits to the LLM and the tuner.** Network + mainnet
  gate, DSN/paths, `STARTING_EQUITY`, `MAX_NOTIONAL_PER_TRADE`,
  `MAX_CONCURRENT_POSITIONS`, `DAILY_LOSS_LIMIT_PCT`, `MAX_LEVERAGE`,
  `RR_FLOOR`, `ALLOWED_COINS`, `MAX_SIGNAL_AGE_MINUTES`, model names + token budgets.
- **`config/active_config.json` — the tunable surface.** Regime gate, risk
  profile, `risk_per_trade_pct`, conviction→size mapping, decision-prompt
  parameters. **Loaded and clamped in code** so a bad value can never reach the
  order path. Missing file → safe defaults.

---

## 10. Self-tuning (out-of-path, propose → approve)

Two tuners, both running _outside_ the order path, both proposing changes a human
approves before they go live. This matches your approval-gate working style and
keeps an LLM from silently rewriting its own trading logic on mainnet.

1. **Config tuner** — reads resolved-trade cohorts and proposes edits to the
   tunable surface (which regimes/setups to favor, risk %, conviction mapping).
   Sample-gated: no eligible cohort ⇒ the model isn't even called. Clamped on load.
2. **Strategy/prompt tuner** — this is the "self-tune the decision-making" piece.
   It analyzes logged **LLM decisions vs outcomes** and proposes refinements to
   the _decision prompt / heuristics_ (e.g. "shorts entered into post-news spikes
   underperformed — add caution"). Also propose → approve.

```
hl tune run       # writes proposed_config.json + proposed_prompt.md (never active)
hl tune diff      # show proposal vs live
hl tune promote   # after review: proposed → active
hl tune history   # audit trail of past promotions
```

Optionally, auto-promote can be allowed on `paper` only (see §13).

---

## 11. What we take from `chrisling-dev/hyperliquid-cli`

It's TypeScript and has no LLM/executor/tuner/paper layer — but its **trading +
monitoring CLI surface is excellent** and worth mirroring in Python:

| Borrow                                                           | Why                                 |
| ---------------------------------------------------------------- | ----------------------------------- |
| Noun→verb command taxonomy (`account`/`trade`/`markets`/`asset`) | Clean, discoverable, scriptable     |
| Multi-account SQLite store + agent wallets + read-only accounts  | Right key-safety model              |
| Global `--json` output mode                                      | Pipe into `jq`, scripts, cron       |
| WebSocket **watch modes** (`-w`) for positions/orders/book       | Great live UX                       |
| Order-defaults config (slippage)                                 | Sensible per-user defaults          |
| Optional background caching server for low-latency marks         | Useful once you scale up polling    |
| `stop-loss` / `take-profit` trigger-order path                   | Reuse for native mainnet SL/TP (§7) |

**Don't take:** the language (we're Python on `hyperliquid-python-sdk`), and
anything executor/LLM/tuner/paper — that's all ours. For the terminal UI, the
Python equivalent of their Ink TUI is `rich` (tables, color-coded P&L) or
`textual` if you want full interactivity.

---

## 12. Stack, layout, phases

### Dependencies

```
# runtime
python >= 3.12
typer, rich            # CLI + TUI
pydantic, pydantic-settings
hyperliquid-python-sdk # testnet + mainnet order placement
eth-account            # agent-wallet signing
requests / httpx       # marks
websocket-client       # watch modes
# llm (lazy-imported)
anthropic              # executor decision + tuner
# dev
pytest
```

`anthropic` and the live-exchange deps are lazy-imported so `paper` mode and the
test suite run without keys or signing libs present.

### Module layout

```
hlcli/
├── __main__.py
├── cli/            # typer app + command groups + json/rich output
├── core/           # config (hard caps), config_schema (tunable+clamp),
│                   # domain types, network resolution + mainnet gate
├── exchange/       # base protocol, paper book, hyperliquid (testnet+mainnet), marks+cache
├── accounts/       # sqlite multi-account store + keystore
├── executor/       # intake, enrich, decision (LLM), gate, execute, monitor
├── tuner/          # stats cohorts, config_tuner, prompt_tuner, promote
├── state/          # sqlite: book, idempotency, high-water mark, decision log
├── safety/         # breaker / kill switch / loss limits / mainnet gate
└── tests/
```

### Phased build (each phase ends at a review gate)

| Phase                            | Scope                                                                                          | Gate to pass                                                           |
| -------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **0 — Skeleton**                 | CLI scaffold, config model + clamp, network resolution, paper stub                             | `hl --help`; paper `once` no-ops cleanly                               |
| **1 — Manual trade**             | Account store, marks feed, Mode A order commands, monitor, status/report, watch modes          | Place + manage a real **testnet** order end-to-end                     |
| **2 — Executor (deterministic)** | Intake, gate, sizing, execute, idempotency, high-water mark, breaker                           | Candidates flow → paper fills; fully deterministic; restart-safe       |
| **3 — LLM decision**             | Enrich, decision prompt, structured output + clamp, decision log, `shadow`                     | Shadow runs produce sane, fully-logged decisions on paper/testnet      |
| **4 — Self-tuning**              | Stats cohorts, config + prompt tuners, propose→approve                                         | Tuner proposes from real logged outcomes; `promote` works; clamps hold |
| **5 — Mainnet hardening**        | Native exchange SL/TP, mainnet gate + confirmation, graduation checklist, key review, alerting | Testnet/shadow expectancy clears → controlled mainnet at tiny caps     |

### Testing

Mirror the old plan's discipline: unit tests for the gate/sizing (the highest-risk
code), the LLM-output validator/clamp, the paper exchange + monitor, intake
idempotency + high-water mark, config-schema clamping, the mainnet gate, and the
CLI. The LLM decision call itself is mocked in tests (deterministic fixtures);
`shadow` mode is the integration test against real data.

---

## 13. Open questions for you

The plan makes a default choice on each; flag any you want to change.

1. **LLM scope** — choose-among-supplied candidates (assumed), or also let it
   _generate_ its own candidates from market + news? This changes how big the
   decision prompt and enrich layer get.
2. **Candidate source** — manual `hl exec propose` + a file/queue (assumed), or do
   you want a live feed (e.g. still reading from ThirdEye)? Standalone is assumed.
3. **News input** — supplied per-candidate by you (assumed), or should the
   executor fetch/enrich news itself?
4. **Tuner autonomy** — propose→approve everywhere (assumed), or allow
   auto-promote on `paper` (and maybe `testnet`)?
5. **Decision cadence** — call the LLM every pass, or only when new candidates
   exist / marks move materially? Drives cost and latency.
6. **Native SL/TP for mainnet** — confirm you want exchange-side trigger orders as
   a hard mainnet prerequisite (strongly recommended).
