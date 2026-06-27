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
- [Agent Context](./AGENT-CONTEXT.md) — agent working memory
- [Claude guidance](./CLAUDE.md) — how to work in this repo

## Status

Current phase: **Phase 1 — Manual trade** ✅ code complete (live testnet order deferred) → next: Phase 2 (deterministic executor)
Last updated: 2026-06-27
