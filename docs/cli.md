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
notional, leverage) plus the exchange's own validation. Writes, so the mainnet gate
applies. `--dry-run` prints the resolved order without placing it. A rejected order
exits `1`.

> Notional is checked against `price` (limit), `trigger` (stop/TP), or the current
> mark (market) × size, vs `MAX_NOTIONAL_PER_TRADE`.

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

### `exec status`
Live position-health view for the executor's book, with a note of how many WAIT
candidates are parked for re-check. `-w`/`--watch` for live refresh.

### `exec report`
Account summary: equity, open positions, unrealized P&L, breaker state, the count of
`deferred` (WAIT) candidates awaiting re-check, and the **graduation**
(mainnet-readiness) verdict from resolved trades.

---

## `hl config` — configuration

### `config show`
Print the resolved hard caps **and** the clamped tunable surface (network, mainnet
flag, the ceilings, allowed coins, model names, `risk_per_trade_pct`, regime
on/off, min conviction).

### `config set` · `config edit`
**Not built** — editing the tunable surface is the tuner's job. These print a
"not built yet (Phase 4)" notice and exit `1`. Use the `hl tune` flow instead.

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
