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
hl sentry    once | run | shadow | manage | adopt | status | log         # Phase 6 in-trade manager
hl tune      run | diff | promote | history
hl config    show | set | edit
hl agent     run | status                                # Phase 7 autonomous supervisor
hl journal   write | show | ls                           # Phase 7 daily journal
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
├── sentry/         # in-trade manager: deterministic trail engine, LLM manager + management gate (§14)
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
| **6a — Trail engine**            | Deterministic in-trade mechanics: breakeven ratchet, ATR/percent trail, scale-out ladder (§14) | Trades trail + scale out on paper, ratchet-only, restart-safe          |
| **6b — Sentry shadow**           | LLM manager proposes actions per open position; logged only, measured vs the 6a baseline      | Shadow log shows sane actions; value-add vs baseline measurable        |
| **6c — Sentry live (↓risk)**     | HOLD/TIGHTEN/REDUCE/CLOSE/EXTEND_TP through the management gate; deferred re-entry cadence    | Gated actions fire on paper/testnet; churn caps hold                   |
| **6d — Pyramiding (ADD)**        | The one risk-increasing action, hardest gate; testnet until graduation                        | ADDs pass full entry caps; add-risk covered by unrealized P&L          |

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

---

## 14. Sentry — the in-trade manager (Phase 6)

Sentry extends the §1 split into the *life* of a trade: today the executor fires,
places static SL/TP, and waits for a level to hit. Sentry actively manages what
happens in between — and owns entry timing for parked WAIT candidates.

**Scope (user-confirmed 2026-07-05):** sentry never originates trades. It watches
two pools: **deferred WAIT candidates** (enters them through the existing decision
+ entry gate when the market gives the opportunity — same followup semantics,
sentry's cadence) and **open positions** (manages them). §13 Q1 stays at
choose-among-supplied.

**Research grounding:** Alpha Arena (real LLMs, real money, on Hyperliquid) showed
discipline beats prediction — the winner traded <3×/day with strict exits; losers
overtraded, over-levered, flip-flopped. FinPos showed naive LLMs fail position-aware
trading without explicit position/exposure representation and a multi-timescale
view. Practitioner rules (breakeven+buffer at ~+1R, ratchet-only trailing, 50%
scale-out at +1R, pyramid only from unrealized profit) are deterministic — so they
are **code, not LLM**. The LLM adds judgment on top: thesis broken → close early,
regime flip → tighten, confirmed trend → add.

### Mechanics vs judgment

- **6a trail engine (code, always-on once enabled):** per open trade, each pass:
  breakeven move (SL → entry ± buffer once unrealized ≥ trigger R), trail
  (ATR-multiple or percent, from candles already fetched), optional scale-out
  (close a fraction at a ladder R). Invariants enforced in code: **SL only ratchets
  toward profit, never widens; protection is replaced place-new-then-cancel-old
  (never naked); dust moves suppressed.** On paper the ledger's `sl` *is* the
  protection (the resolver closes on it); on live networks the engine also syncs
  the native triggers. The engine is the measurable baseline for 6b.
- **LLM manager (6b–6d):** bounded action menu per position — it picks an action,
  never free-forms an order:

| Action         | Risk | Management-gate conditions (deterministic)                                                                                     |
| -------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------ |
| `HOLD`         | =    | default; always valid; non-HOLD requires a rationale                                                                            |
| `TIGHTEN_STOP` | ↓    | strictly better than current SL (ratchet), min gap from mark; breakeven only at ≥ trigger R                                     |
| `REDUCE`       | ↓    | pct ∈ {25, 50, 75}; remainder ≥ min size, else it becomes a CLOSE                                                                |
| `CLOSE`        | ↓    | always allowed                                                                                                                   |
| `EXTEND_TP`    | ~    | only once SL ≥ breakeven; bounded move per action                                                                                |
| `ADD`          | ↑    | unrealized ≥ +1R at mark; add ≤ ½ current size; add-risk ≤ unrealized P&L; SL raised in the same action; full entry caps re-run; max adds/position |

### The management gate (first-failure, mirrors §5)

```
schema-valid LLM output → breaker (tripped ⇒ only ↓risk actions pass)
  → cooldown + rate limits → action-specific checks (table above)
  → wire rounding → idempotency (action content-hash) → fire
```

### Anti-churn (the Alpha Arena lesson — enforced in code, not prompt)

Evaluate on candle close, not continuously; per-position cooldown after any
action; hard caps on actions/position/day and LLM calls/day; no opposing actions
inside a window (no ADD within N min of a REDUCE); HOLD is the schema default at
low temperature; invalid output dropped + tallied, never guessed at (§6 rule).

### Config split (§9 holds)

- **`.env` hard caps:** `SENTRY_MAX_ACTIONS_PER_POSITION_PER_DAY`,
  `SENTRY_MAX_LLM_CALLS_PER_DAY`, `SENTRY_MIN_ACTION_INTERVAL_MINUTES`,
  `SENTRY_MAX_ADDS_PER_POSITION`, add-size ratio ceiling.
- **Tunable surface (clamped):** trail style (atr|percent|off), ATR multiple,
  breakeven trigger R + buffer, scale-out ladder (R, fraction), cadence minutes.

### Logging & learning

Every evaluation → `sentry_log` (full position context + proposed action + gate
verdict + fill), same audit/tuner-fuel role as `decision_log`. Shadow (6b) logs
LLM proposals *next to* what the 6a baseline did, so the LLM's value-add is
measured before it can act. Graduation before 6c/6d mirrors §7: sentry actions on
mainnet only after testnet/shadow evidence clears.

---

## 15. Agent mode — autonomous operation (Phase 7)

Purpose: hl runs unattended on a server. An upstream signal producer drops
candidate batches on *its* schedule; the agent trades them through the existing
executor, sentry manages everything open (Mode A and Mode B alike), and the
system journals and reflects on itself daily.

**Independence is a hard constraint:** this repo is open source and
producer-agnostic. Signals arrive as JSON batches in the §5 `Candidate` schema
(`coin/entry/tp/sl/reasoning/news`) — nothing in hl knows or references who
wrote them. Any private signal engine integrates by writing files (or calling
`hl exec propose`); the bridge lives on the producer's side, never here.

### 15.1 The handoff: watched intake directory (JSON batch files)

The producer drops `*.json` (a list or a single object of candidate fields)
into `~/.hyperliquid-cli/intake/<network>/`. The agent polls the directory;
per new file: parse → queue into the intake stream → move the file to
`processed/` (parse failure ⇒ `failed/` + alert; never silently deleted).

Files beat an HTTP API here, deliberately:

- **No open port.** The process holding trading keys exposes zero network
  surface — the right OSS security posture for a trading CLI.
- **Durable, auditable, replayable.** The raw batch survives on disk; the
  existing content-hash candidate ids make a re-dropped file a no-op, and the
  §5 idempotency machinery already guarantees no double-fire across restarts.
- **Transport-agnostic.** Same host: the producer's cron writes the file.
  Cross-host: scp/rsync/object-store sync — entirely the producer's concern.

An authenticated HTTP intake (`hl agent serve`) can become a later opt-in
sub-phase if push-over-network is ever needed; it is not the default.

### 15.2 The supervisor: `hl agent run`

One foreground process; the scheduler is deterministic code (the "agent" is
the loop — LLM calls stay exactly where they already are: decision, sentry
manager, tuners, journal narrative):

- **Intake watch** (poll every few seconds): a new batch triggers an exec pass
  immediately — signals trade while fresh, no cadence wait.
- **Exec cadence** (periodic `run_once`): catches deferred WAITs and freshness
  expiry even when no new files arrive.
- **Sentry cadence**: watch pass per sentry interval; `--shadow` / `--manage`
  semantics and the §14 graduation rules unchanged.
- **Daily jobs** (UTC times, tunable): journal write → reflection distill →
  tuner run → report alert.
- **Ops surface:** heartbeat + breaker events via the existing JSONL alerter;
  `hl agent status` = last-pass times, breaker state, open positions, day P&L,
  pending tuner proposals.

Crash-safety is already built (idempotency keys recorded before fire, intake
HWM, ledger-first fills); the supervisor adds per-loop failure backoff like
`exec run`. `deploy/` ships a systemd unit (`Restart=always`), a Dockerfile,
and a VPS ops doc. Native SL/TP (§7) covers the dead window between crash and
restart.

### 15.3 Daily journal: `hl journal`

Per network, per day, built deterministically from the state store: fires and
skips with a gate-reason tally, resolves with R-multiples, expectancy and
profit factor, sentry actions taken, breaker / loss-limit events, pending
tuner proposals. Written to `~/.hyperliquid-cli/journal/<network>/YYYY-MM-DD.md`.
Then **one** opus call appends a narrative reflection section — out-of-path,
fully logged like tuner calls, and structurally unable to touch config.
`hl journal write` (idempotent re-run), `show [date]`, `ls`.

### 15.4 Reflection memory (bounded inject)

The daily reflection is also distilled into a one-paragraph row in a
`reflections` table. The exec decision prompt and the sentry context gain a
"recent lessons" block: the last N paragraphs (N small, token-capped, both
clamped). Two rules keep this safe: reflections are generated **only from our
own logged outcomes**, never from raw external text (prompt-injection
hygiene); and the block is advisory context — the gate still owns everything
that touches money. `decision_log` records which reflection rows were in
context, so the inject's value is measurable the same way 6b measured the
sentry LLM.

### 15.5 Autonomy boundaries (§1/§9/§10 hold — restated because agent mode tempts violations)

- **Tuner proposals auto-promote on paper only.** Testnet and mainnet stay
  propose→approve — the journal and `agent status` surface pending diffs so
  approval is one command. No LLM ever promotes its own tunable surface on a
  live network.
- **Mode A adoption:** sentry adopts an unmanaged position into the ledger
  only when a stop already exists (exchange trigger order): entry = actual avg
  price, `initial_sl` = trigger price, row flagged `adopted`; thereafter
  managed identically to Mode B. **No stop anywhere ⇒ alert + skip — never
  invent one** (an invented stop is an order the human didn't specify).
  `hl sentry adopt` does the same on demand.
- **Mainnet:** every existing gate (env flag + `--network` + typed confirm +
  graduation) is unchanged. Agent mode adds no new path to mainnet.

### 15.6 Config split (§9 holds)

- **`.env` hard caps:** `HL_AGENT_JOURNAL_MODEL` (+ token budget),
  `HL_AGENT_REFLECT_INJECT_MAX` (N) + token cap, intake dir override,
  daily-job UTC times.
- **Tunable surface (clamped):** exec cadence minutes, intake poll seconds,
  journal narrative on/off, reflection inject on/off.

### 15.7 Sub-phases and gates

| Sub-phase | Scope | Gate |
| --------- | ----- | ---- |
| 7a | supervisor + intake dir + deploy templates | drop a batch file → paper trades end-to-end; `kill -9` mid-pass + restart ⇒ no double-fire, file not reprocessed |
| 7b | journal (deterministic digest + opus narrative) | a day of paper trading yields a journal that reconciles with `exec report`; narrative present + logged |
| 7c | reflection memory + scheduled tuners | capped inject visible in `decision_log`; nightly tuner: paper auto-promotes, testnet/mainnet wait for approval |
| 7d | Mode A adoption | manual testnet order with a stop gets adopted + trailed; stopless position alerts and stays untouched |

Order: 7a → 7b → 7c → 7d.
