# Setup & Running

## Prerequisites

- **Python ≥ 3.12** (the repo uses 3.12 features; the system default may be 3.11).
  On this machine 3.12 is at `/opt/homebrew/bin/python3.12`.
- No keys or signing libs are needed for paper mode or the test suite — they are
  lazy-imported and only required for their phase.

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # core + pytest; paper mode + all public reads work keyless
```

Optional extras, installed only when you need them:

| Extra | Adds | Needed for |
|-------|------|-----------|
| `.[exchange]` | `hyperliquid-python-sdk`, `eth-account`, `websocket-client` | placing live testnet/mainnet orders |
| `.[llm]` | `anthropic` | the LLM decision call (`exec` non-stub) and the tuners |
| `.[dev]` | `pytest` | running the test suite |

```bash
pip install -e ".[exchange,llm,dev]"   # everything
```

## Configuration

Two config layers — **keep them separate** (this split is what makes self-tuning safe):

### 1. `.env` — hard caps (off-limits to the LLM and tuner)

Copy `.env.example` to `.env` and adjust. Every var is `HL_`-prefixed.

| Var | Default | Meaning |
|-----|---------|---------|
| `HL_DEFAULT_NETWORK` | `paper` | default network when `--network` is omitted |
| `HL_ENABLE_MAINNET` | `0` | one of three conditions to touch mainnet |
| `HL_STARTING_EQUITY` | `10000` | paper starting equity |
| `HL_MAX_NOTIONAL_PER_TRADE` | `1000` | hard notional ceiling per trade |
| `HL_MAX_CONCURRENT_POSITIONS` | `3` | max open positions |
| `HL_DAILY_LOSS_LIMIT_PCT` | `5` | day-start drawdown that halts new fires |
| `HL_MAX_LEVERAGE` | `3` | hard leverage ceiling |
| `HL_RR_FLOOR` | `1.5` | minimum reward:risk to accept a candidate |
| `HL_MAX_SIGNAL_AGE_MINUTES` | `30` | freshness cutoff for a candidate |
| `HL_FOLLOWUP_MAX_ATTEMPTS` | `3` | max WAIT re-checks before a deferred candidate is dropped (`0` disables deferral) |
| `HL_ALLOWED_COINS` | `BTC,ETH,SOL` | the only tradable coins |
| `HL_GRADUATION_MIN_TRADES` / `_DAYS` / `_EXPECTANCY` | `20` / `7` / `0.0` | mainnet-readiness thresholds |
| `HL_DECISION_MODEL` / `_MAX_TOKENS` | `claude-sonnet-4-6` / `1024` | order-path model |
| `HL_TUNER_MODEL` / `_MAX_TOKENS` | `claude-opus-4-8` / `4096` | daily-tuner model |

`HL_DATA_DIR` (default `~/.hyperliquid-cli`) and `HL_CONFIG_PATH`
(default `config/active_config.json`) are also configurable.

The LLM decision call and the tuners read `ANTHROPIC_API_KEY` from the environment.

### 2. `config/active_config.json` — the tunable surface (clamped on load)

The values the self-tuner may change. Missing file → safe defaults. A malformed file
fails loudly (`ConfigError`) rather than silently running on the wrong config. See
`config/active_config.example.json`. Fields: `risk_per_trade_pct`, `regime`
(enabled + allowed_regimes), `sizing` (conviction→size mapping),
`max_candidates_per_pass`, `decision_temperature`, `max_hold_minutes`.

## Running

### Paper (default, keyless)

```bash
hl --help
hl config show                 # resolved hard caps + clamped tunable surface
hl markets prices              # live public marks
hl asset book BTC -w           # live order book, -w to watch

# Mode B — deterministic executor on paper:
hl exec propose --coin BTC --entry 60000 --tp 66000 --sl 58000 --reason "breakout"
hl exec once                   # intake → enrich → decide → gate → fire → persistent paper book
hl exec report                 # equity, positions, unrealized P&L, breaker, graduation
hl exec breaker --on           # kill switch: halts new fires (open positions still managed)
```

`exec once` = one pass · `exec run` = continuous loop · `exec shadow` = decide+log,
fire nothing · `exec status` / `report` = state views · `exec breaker` = kill switch.

### Live trading (testnet/mainnet)

```bash
pip install -e ".[exchange]"
hl --network testnet account add main --address 0xYOURADDR   # prompts for the agent key (stored 0600)
hl --network testnet trade order limit BTC long 0.001 50000  # Mode A manual order
hl --network testnet exec once                                # Mode B executor
```

Use **agent ("API") wallets** — they can trade but not withdraw. Keys live only in
`~/.hyperliquid-cli/keys/` (0600) and are loaded only for write actions.

### Mainnet (gated)

All three are required, by design:

```bash
export HL_ENABLE_MAINNET=1
hl --network mainnet exec once          # then type 'mainnet' at the confirmation prompt
# -y skips the prompt but the env flag is still required
```

Native exchange-side SL/TP is a hard prerequisite: a live entry that can't be
protected is emergency market-closed, never left naked.

### The tuner (out-of-path)

```bash
hl tune run        # writes proposed_config.json / proposed_prompt.md (never active); no-op if no eligible cohort
hl tune diff       # proposed vs active
hl tune promote    # proposed → active (config re-clamped); appends promotions.jsonl
hl tune history    # promotion audit log
```

## Tests

```bash
.venv/bin/pytest -q          # 177 passing, ~3s, fully keyless (no anthropic/hyperliquid/eth_account needed)
```

The LLM call is mocked in every test (deterministic fixtures / injected `decide_fn`);
`shadow` mode is the real integration test against live data.
