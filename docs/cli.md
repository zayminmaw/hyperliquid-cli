# CLI Reference

Complete reference for every `hl` command, argument, and option, generated from the
command modules in `hlcli/cli/commands/`. Noun → verb taxonomy.

- **Arguments** are positional and (unless noted) required.
- **Options** are flags; defaults are shown.
- **Exit codes:** `0` success; `2` bad usage / failed validation (`typer.BadParameter`);
  `1` when an order/cancel/leverage call is rejected by the exchange, or a stubbed command is invoked.

```
hl [GLOBAL OPTIONS] <group> <command> [ARGS] [OPTIONS]
```

Running `hl`, any group, or any group with no command prints help (`no_args_is_help`).

---

## Global options

Parsed by the top-level callback (`cli/app.py`) **before** the group; they apply to
every command and are stored on the context as `GlobalState`.

| Option | Default | Meaning |
|--------|---------|---------|
| `--network paper\|testnet\|mainnet` | `paper` (or `HL_DEFAULT_NETWORK`) | Which network to act on. Resolved + validated up front; an unknown value fails with a `--network` error. |
| `--account <alias>` | per-network default | Which stored account to act as (testnet/mainnet only; paper needs none). |
| `--json` | off | Machine-readable JSON output instead of rich tables. |
| `--dry-run` | off | Resolve everything but place **no** orders (side-effect-free where it applies). |
| `-y`, `--yes` | off | Skip confirmation prompts. For mainnet it skips the *typed* confirm but **still requires** `HL_ENABLE_MAINNET=1`. |

Global options go **before** the command group:

```bash
hl --network testnet --json exec report
hl --dry-run trade order market BTC long 0.01
```

### The mainnet gate

Any **write** on `--network mainnet` requires all three, by design:
`HL_ENABLE_MAINNET=1` (env) **and** `--network mainnet` **and** a typed `mainnet`
confirmation (or `-y` to skip just the prompt). Reads are not gated.

---

## `hl account` — accounts & portfolio views

Multi-account store (`~/.hyperliquid-cli/accounts.db`); agent keys stored `0600` and
never logged. Add/list/select plus read-only portfolio views.

### `account add <alias>`
Add an account for the current `--network`. Paper needs no account (errors if tried).
A trade account **prompts** for the agent private key (hidden input — never a CLI arg,
never logged) and prints the derived agent address to approve on Hyperliquid.

| Arg/Option | Default | Meaning |
|-----------|---------|---------|
| `alias` (arg) | — | Unique account alias. |
| `--address <0x…>` | required | The main account address being traded. |
| `--read-only` | off | Monitor-only account; no key prompt. |

```bash
hl --network testnet account add main --address 0xYOURADDR
hl --network mainnet account add watcher --address 0xABC… --read-only
```

### `account ls`
List accounts. `--all` lists every network (default: just the current `--network`).

### `account set-default <alias>`
Make `<alias>` the default for its network.

### `account edit <alias>`
Edit an existing account **in place** — `--address <0x…>` re-points it at a different
main account, `--rekey` replaces the agent key (hidden prompt, same as `add`; refused on a
read-only account). The alias and its default flag are kept. Renaming an alias isn't
supported — `remove` + `add` for that.

### `account remove <alias>`
Remove the account and delete its stored key (if any).

### `account positions` · `account orders`
Open positions / open orders for the resolved account. Both accept `-w`/`--watch`
for a live-refreshing table (ignored under `--json`).

### `account balances`
Account equity for the current network.

### `account portfolio`
One-line summary: equity, open-position count, total unrealized P&L.

---

## `hl markets` — market data

### `markets ls`
List tradable markets with their marks. `--all` shows every market (default:
`ALLOWED_COINS` only).

### `markets prices [COINS…]`
Marks for the given coins (default: `ALLOWED_COINS`). `--all` shows every market.

```bash
hl markets prices            # ALLOWED_COINS
hl markets prices BTC ETH
hl markets ls --all
```

---

## `hl asset` — per-coin price & book

### `asset price <coin>`
Current mark for one coin. `-w`/`--watch` for live refresh.

### `asset book <coin>`
Order book (asks shown top-down, then bids).

| Arg/Option | Default | Meaning |
|-----------|---------|---------|
| `coin` (arg) | — | Coin symbol (case-insensitive). |
| `--depth <n>` | `5` | Levels per side. |
| `-w`, `--watch` | off | Live refresh (ignored under `--json`). |

```bash
hl asset price BTC -w
hl asset book ETH --depth 10
```

---

## `hl trade` — manual orders (Mode A)

Human-in-control. **No LLM, no risk gate** — only the hard caps (allowed-coin,
notional, leverage, and the account-wide gross-exposure ceiling) plus the exchange's
own validation. Writes, so the mainnet gate applies. `--dry-run` prints the resolved
order without placing it. A rejected order exits `1`.

> Notional is checked against `price` (limit), `trigger` (stop/TP), or the current
> mark (market) × size, vs `MAX_NOTIONAL_PER_TRADE`. A non-reduce-only entry also
> honors the account-wide `MAX_TOTAL_EXPOSURE_USD` / `MAX_GROSS_LEVERAGE` ceilings
> (audit A) — the same check the Mode B gate runs; a reduce-only close is never
> blocked. The daily new-entry cap (`MAX_TRADES_PER_DAY`) is executor-only — it's
> derived from the executor ledger, which manual orders don't write.

### `trade order limit <coin> <side> <size> <price>`
Resting limit order.

| Arg/Option | Default | Meaning |
|-----------|---------|---------|
| `coin` `side` `size` `price` (args) | — | `side` is `long`\|`short`. |
| `--reduce-only` | off | Only reduces an existing position. |

### `trade order market <coin> <side> <size>`
Market order. Options: `--reduce-only` (default off).

### `trade order stop-loss <coin> <side> <size> <trigger>`
Stop-loss trigger. `side` is the **closing** side (e.g. `short` to protect a long).

| Arg/Option | Default | Meaning |
|-----------|---------|---------|
| `coin` `side` `size` `trigger` (args) | — | `trigger` = trigger price. |
| `--reduce-only` / `--no-reduce-only` | **on** | Reduce-only by default (it's a protective order). |

### `trade order take-profit <coin> <side> <size> <trigger>`
Take-profit trigger. Same shape as `stop-loss` (reduce-only on by default).

### `trade cancel <coin> <oid>`
Cancel one order by coin + order id.

### `trade cancel-all`
Cancel all orders. `--coin <coin>` limits to one coin.

### `trade set-leverage <coin> <leverage>`
Set leverage (rejected locally if `> MAX_LEVERAGE`). `--isolated` uses isolated
margin (default: cross).

```bash
hl --network testnet trade order limit BTC long 0.001 50000
hl --network testnet trade order stop-loss BTC short 0.001 48000
hl --network testnet trade cancel BTC 123456
hl --network testnet trade set-leverage BTC 3 --isolated
```

---

## `hl exec` — LLM executor (Mode B)

The deterministic pipeline + LLM decision. State is network-scoped; paper uses the
persistent paper book, testnet/mainnet use the live backend.

### `exec propose`
Queue candidate setup(s) into the intake stream. Either supply all four levels, or a
JSON batch via `--file`. Side is inferred from level geometry; incoherent levels are
rejected (`sl<entry<tp` = long, `tp<entry<sl` = short). Batch items without an `id` get one derived from their content, so re-importing the same file enqueues nothing new. Duplicates (same id) are
skipped — reported as `duplicates`.

| Option | Default | Meaning |
|--------|---------|---------|
| `--coin` / `--pair <coin>` | — | Coin (aliases accepted). |
| `--entry <px>` | — | Entry price. |
| `--tp <px>` | — | Take-profit level. |
| `--sl <px>` | — | Stop-loss level. |
| `--reason <text>` | `""` | Thesis / reasoning passed to the LLM. |
| `--news <text>` | `""` | Optional news context. |
| `--file <path>` | — | JSON list or single object batch (instead of the flags). |

```bash
hl exec propose --coin BTC --entry 60000 --tp 66000 --sl 58000 --reason "breakout"
hl exec propose --file setups.json
```

### `exec once`
One full pass: resolve open trades → re-check due WAIT deferrals → intake → enrich
→ LLM decision → gate → fire → log. An `act + wait` decision is deferred for a later
re-check rather than fired. Honors the global `--dry-run` (computes, mutates nothing;
deferrals are skipped). Writes, so the mainnet gate applies. Emits a `PassSummary`
(`seen/rechecked/approved/fired/rejected/failed/dropped/deferred/resolved` — `rejected` is the gate saying no, `failed` is a gate-approved order dying at the exchange: reject/unfilled/aborted).

### `exec shadow`
A full pass that **decides, gates, and logs but fires nothing** — the pre-mainnet
confidence builder and tuner training-data source. Read-only backend; advances the
high-water mark.

### `exec run`
Continuous loop (ctrl-c to stop). `--interval <seconds>` between passes (default
`5.0`). A failing pass is caught and logged so the loop survives transient
LLM/network faults.

### `exec breaker`
Show or toggle the kill switch (halts new fires; open positions still managed).
`--on` trips it, `--off` clears it; with neither it just shows current state.

```bash
hl exec breaker            # show
hl exec breaker --on       # trip
hl exec breaker --off      # clear
```

### `exec reconcile`
Diff the exchange (positions + resting orders) against the ledger and return a
**safe / requires-halt** verdict (wave-2 G). Flags an unexpected position (on the exchange,
not in the ledger), a size mismatch, or a live position with no native protection. On an
unsafe divergence it **trips the breaker** (and emits a critical `reconcile_halt` alert) so a
restart can't fire into an inconsistent book — run it after any crash, especially on mainnet.
`--no-halt` reports only; `--dry-run` never trips.

### `exec status`
Live position-health view for the executor's book, with a note of how many WAIT
candidates are parked for re-check. `-w`/`--watch` for live refresh.

### `exec report`
Account summary: equity, open positions, unrealized P&L, breaker state, the count of
`deferred` (WAIT) candidates awaiting re-check, and the **graduation**
(mainnet-readiness) verdict from resolved trades. Also carries the execution-quality
`performance` block — profit factor, max drawdown, trade-based Sharpe/Sortino, and
realized entry slippage (audit C/D) — `conviction_calibration`, and `management_cohorts`
(realized R by which sentry management events fired — the sentry-tuning evidence, audit J).
These span the whole resolved set (real + shadow), same as graduation.

---

## `hl config` — configuration

### `config show`
Print the resolved hard caps **and** the clamped tunable surface (network, mainnet
flag, the ceilings, allowed coins, model names, `risk_per_trade_pct`, regime
on/off, min conviction).

### `config set <key> <value>`
Set one field of the **tunable surface** (`config/active_config.json`), then re-clamp
on write. Keys are dotted paths into the tunable model — e.g. `risk_per_trade_pct`,
`sizing.enabled`, `trail.style`, `regime.allowed_regimes` (comma-list). **Hard caps are
refused** (`max_notional_per_trade`, `max_leverage`, …) — those live in `.env`. The value
written is the *clamped* one, so a manual set can never widen the box; an unknown key
lists the settable ones and exits `1`. The tuner (`hl tune`) is the data-driven path to
the same surface; `set`/`edit` are direct operator control over it.

### `config edit`
Open `config/active_config.json` in `$EDITOR` (seeded with the current clamped surface if
absent). On save it is re-validated and clamped: an out-of-range edit is silently pulled
back into range, malformed JSON fails loudly — nothing bad reaches the order path.

### `config reset`
Remove `config/active_config.json`, reverting the tunable surface to the built-in safe
defaults. Hard caps (`.env`), the decision prompt, and any pending tuner proposals are left
untouched.

---

## `hl tune` — self-tuning (propose → approve)

Out-of-path. Proposals are written, never auto-applied; a human promotes them.
Both tuners are **sample-gated** — on a thin record `run` reports the gate and calls
no model.

### `tune run`
Propose config + prompt edits from the resolved-trade record. Writes
`proposed_config.json` / `proposed_prompt.md` beside the active config (never the
active files). Reports the cohorts considered and a hint to review.

### `tune diff`
Show each pending proposal against what's currently live.

### `tune promote`
Make pending proposals active (config **re-clamped** on the way in); appends to the
`promotions.jsonl` audit log.

### `tune history`
The promotion audit trail.

```bash
hl tune run                # writes proposals (or reports the gate)
hl tune diff               # review
hl tune promote            # activate
hl tune history            # audit
```

---

## Output modes

Every command honors `--json` for machine-readable output. Without it, results render
as rich tables / styled notes. Watch (`-w`) modes fall back to a single rendered
table under `--json` (no live loop). See [setup.md](./setup.md) for end-to-end
examples and [architecture.md](./architecture.md) for what each `exec` verb does
inside the pipeline.

---

## `hl agent` — the autonomous supervisor (Phase 7)

One process owning all cadences: intake-directory watch, exec passes, sentry
passes, daily jobs. The loop is deterministic code; LLM calls stay inside the
existing decision/management/tuner paths. See [deploy/README.md](../deploy/README.md)
for running it under systemd/Docker and the producer file-drop contract.

### `agent run`

`--shadow` / `--manage` carry the sentry semantics (exclusive; `--manage` on
mainnet requires testnet graduation). Cadences come from the tunable surface
(`agent.intake_poll_seconds`, `agent.exec_interval_minutes`,
`agent.sentry_interval_seconds` — read at start); the daily-job time is
`HL_AGENT_DAILY_UTC`. A new batch file in `<data_dir>/intake/<network>/`
triggers an exec pass immediately; ingested files archive to `processed/`,
unparseable ones to `failed/` plus an alert.

### `agent status`

Cross-process pulse from the state store: `liveness` (`never`/`alive`/`stale`, the
verdict from the heartbeat's age vs the staleness threshold), `running` (kept for
back-compat — true when `alive`), `stale_after_s`, pass ages, last daily date,
breaker, equity/book, realized-today, deferred count, pending tuner proposals, and
the intake dir.

### `agent watchdog`

A cron/systemd-friendly liveness reaper (audit F). A hard-killed supervisor
(SIGKILL, host crash) can't alert for itself, so run this separately: it emits a
**critical** `agent_stale` alert when the last tick is older than the staleness
threshold **and** positions are open, and exits non-zero so a monitor can escalate.
Quiet (exit `0`) when the loop is alive, never started, or stale with nothing at
risk. The threshold is `HL_AGENT_STALE_AFTER_SECONDS` (0 → 3× the intake poll).

```bash
hl agent run                       # paper by default, like everything else
hl --network testnet -y agent run --manage
hl --json agent status
hl --network mainnet agent watchdog   # e.g. from cron every minute
```

---

## `hl journal` — the daily trade journal (Phase 7)

One markdown file per UTC day per network at `<data_dir>/journal/<network>/`.
The digest half is deterministic (decisions with the model's own rationale,
gate-reason tally, trades + R/expectancy/profit factor, sentry actions,
warning+ alerts, a snapshot reconciling with `exec report`); the reflection
half is **one** opus call, cached in state meta so rebuilding a day never
re-rolls or re-bills it. The agent's daily job writes yesterday's journal
automatically.

### `journal write`

`--date YYYY-MM-DD` (default today) · `--no-narrative` skips the LLM section
(also switchable via the tunable `agent.journal_narrative`). A missing API key
degrades to a placeholder + `journal_narrative_failed` alert — the digest
always writes.

### `journal show [date]` · `journal ls`

```bash
hl journal write                   # today, with reflection
hl journal show 2026-07-07
hl --json journal ls
```

### `sentry adopt` (Phase 7d)

Adopts unmanaged (Mode A) positions **that carry an exchange stop trigger** into
the ledger: entry at the actual average price, `initial_sl` at the trigger, row
flagged `adopted` — thereafter trailed/managed identically to a Mode B trade.
Records only, places no orders; a stopless position is skipped (set one with
`hl trade order stop-loss` — adoption never invents a stop) and keeps raising
the `unmanaged_position` alert. Runs automatically before every watch pass.
