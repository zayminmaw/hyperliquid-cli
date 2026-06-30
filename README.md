# hyperliquid-cli (`hl`)

> Python CLI for trading on Hyperliquid. Manual (Mode A) orders plus an LLM-driven
> executor (Mode B) that owns trade judgment inside a deterministic risk gate that
> owns the math and safety. Clean separation of paper → testnet → mainnet.

## Quick Start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # paper + all reads work keyless; add [exchange] to trade live, [llm] later
hl --help
hl exec once                   # paper, no-op pass
hl config show                 # resolved hard caps + clamped tunable surface
hl markets prices              # live public marks (paper)
hl asset book BTC -w           # live order book, -w to watch

# Mode B — deterministic executor on paper (LLM decision lands in Phase 3):
hl exec propose --coin BTC --entry 60000 --tp 66000 --sl 58000 --reason "breakout"
hl exec once                   # intake → gate → fire → persistent paper book
hl exec report                 # equity, positions, unrealized P&L, breaker state
hl exec breaker --on           # kill switch: halts new fires

# live trading (needs the exchange extra + an approved agent wallet):
pip install -e ".[exchange]"
hl --network testnet account add main --address 0xYOURADDR   # prompts for agent key
hl --network testnet trade order limit BTC long 0.001 50000
```

## Trading modes

- **Mode A — manual** (`hl trade …`): direct human orders, hard caps + exchange validation only.
- **Mode B — LLM executor** (`hl exec …`): you supply candidate setups; the LLM
  decides act/skip, timing, conviction; deterministic code does sizing, SL/TP,
  caps, idempotency, kill switch. LLM output is always validated + clamped before the gate.

## Networks

`paper` (default, simulated book on public marks, no keys) → `testnet` (fake money,
real fills) → `mainnet` (real money, **gated**: needs `HL_ENABLE_MAINNET=1` +
`--network mainnet` + typed confirmation, and native exchange-side SL/TP).

## Docs

- [Plan](./PLAN.md) — goals, phases, architecture (source of truth)
- [Action Items](./ACTION-ITEMS.md) — phase-by-phase progress checklist
- [Architecture](./docs/architecture.md) — system overview, executor pass, the gate
- [CLI Reference](./docs/cli.md) — every command, argument & option
- [Setup](./docs/setup.md) — install, config (hard caps vs tunable), running, tests
- [Modules](./docs/modules.md) — per-package reference
- [Decisions](./docs/decisions.md) — key technical decisions & why
- [Handover](./docs/handover.md) — full handover doc
- [Agent Context](./AGENT-CONTEXT.md) — agent working memory
- [Claude guidance](./CLAUDE.md) — how to work in this repo

## Status

All five phases (0–5) **code-complete**; 153 tests pass, keyless.
Remaining work is operational: supply agent keys → run testnet/shadow → clear the
graduation checklist → mainnet at tiny caps. See [handover](./docs/handover.md).
Last updated: 2026-06-30
