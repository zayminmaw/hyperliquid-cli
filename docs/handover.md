# Handover: hyperliquid-cli (`hl`)

**Date:** 2026-07-01
**Author:** zayminmaw (built with Claude Code)
**Status:** Code-complete through all five planned phases (Phase 0–5). 235 tests pass.
Remaining work is **operational**, not coding: supply agent keys, run testnet/shadow
to accumulate resolved trades, let the graduation checklist clear, then go to mainnet
at tiny caps.

---

## What This Project Does

`hl` is a Python CLI for trading on Hyperliquid. It has two modes: **manual** orders
placed directly by a human (Mode A), and an **LLM-driven executor** (Mode B) where a
human supplies the trade thesis (candidate setups with entry/tp/sl/reasoning) and a
language model supplies the execution judgment — act or skip, now or wait, how
convinced — inside a deterministic risk gate that owns all the money math and safety.
It runs on three networks: simulated `paper` (the default, no keys), `testnet` (fake
money, real fills), and `mainnet` (real money, heavily gated).

---

## How to Run It

Full detail in [setup.md](./setup.md); every command, argument and option is in the
[CLI reference](./cli.md). The short version:

### Prerequisites
- Python ≥ 3.12 (`/opt/homebrew/bin/python3.12` on this machine).
- No keys/signing libs for paper mode or tests. `ANTHROPIC_API_KEY` for the LLM call;
  an agent wallet for live trading.

### Setup
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # paper + reads + tests, keyless
cp .env.example .env           # hard caps; edit as needed
```

### Running (development / paper)
```bash
hl --help
hl exec propose --coin BTC --entry 60000 --tp 66000 --sl 58000 --reason "breakout"
hl exec once                   # one full executor pass on the paper book
hl exec report                 # equity, positions, P&L, breaker, graduation verdict
.venv/bin/pytest -q            # 235 passing
```

### Running (production / live)
```bash
pip install -e ".[exchange,llm]"
hl --network testnet account add main --address 0xYOURADDR   # prompts for agent key (0600)
hl --network testnet exec run                                 # continuous executor loop
# mainnet additionally needs: HL_ENABLE_MAINNET=1 + --network mainnet + a typed 'mainnet' confirm
```

---

## Architecture

Full version in [architecture.md](./architecture.md); module-by-module in
[modules.md](./modules.md).

### High-Level Structure

```
hlcli/
├── cli/        # typer app, global flags, command groups, rich/json output, watch loops
├── core/       # config (hard caps) · config_schema (tunable+clamp) · types · network gate · llm client
├── exchange/   # Exchange protocol · paper book · hyperliquid live · marks feed · factory
├── accounts/   # sqlite account store + 0600 keystore
├── executor/   # intake → enrich(+candles/regime) → decision(LLM) → gate → execute → protect → resolve → runner
├── tuner/      # stats cohorts · config_tuner · prompt_tuner · promote (propose→approve)
├── state/      # sqlite: intake stream, HWM, idempotency, decision log, trades, paper book
└── safety/     # breaker (kill switch + loss limit) · alerts · graduation
```

### Key Files

| File | Purpose |
|------|---------|
| `hlcli/executor/runner.py` | `run_once()` — the executor pass orchestrator |
| `hlcli/executor/gate.py` | The deterministic risk gate + fixed-fractional sizing |
| `hlcli/executor/decision.py` | LLM decision (lazy anthropic) + validate/clamp/drop |
| `hlcli/core/config.py` | Hard caps (`.env`, off-limits to tuner) |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` (the safety contract) |
| `hlcli/state/store.py` | Network-scoped SQLite: HWM, idempotency, decision log, trades |
| `hlcli/exchange/hyperliquid.py` | Live backend; key only in `_agent_key`; SDK lazy |
| `hlcli/cli/context.py` | Resolves account/key + enforces the mainnet gate |
| `PLAN.md` | Authoritative spec — resolves any conflict |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |

### Data Flow

A Mode B pass: resolve open trades (SL/TP/expiry → trades ledger) → re-check any
due WAIT deferrals with fresh data → pull candidates past the high-water mark →
enrich (marks, equity, positions, recent decisions, a candle tail + regime label) →
LLM decide → if the decision is *act + wait*, defer it for a later re-check (HWM
still advances); otherwise → deterministic gate (first-failure wins) → fire approved
as a MARKET order (idempotency key recorded *before* placing) → reconcile to the
actual fill → place native SL/TP triggers (testnet/mainnet; emergency-close if that
fails) → log the full decision + fill → advance the HWM. See the diagram in
[architecture.md](./architecture.md).

---

## Key Decisions & Why

The full table is in [decisions.md](./decisions.md). The five that matter most:

| Decision | Why |
|----------|-----|
| LLM owns judgment; code owns mechanics + safety | The gate, not the model, is the safety authority |
| LLM output is gate **input**, never a bypass; schema-invalid → dropped, never guessed | A guessed-at decision is an unaudited trade |
| Hard caps (`.env`) vs tunable surface (clamped on load) | A tuned value can never reach the order path unclamped |
| Idempotency key recorded *before* placing; MARKET entry | A crash skips, never double-fires; no phantom positions |
| Native SL/TP is a hard mainnet prerequisite | A crashed executor must never leave a position naked |

---

## Known Issues & Limitations

- **Regime is a coarse, single-signal classifier** — a Kaufman efficiency-ratio over
  a 15m/48-bar window (trend/range, `None` below 20 bars). It's deliberately simple;
  a richer multi-timeframe regime model is a natural future enrichment. The candle
  fetch is best-effort, so a feed hiccup degrades the pass to `regime=None` (the
  gate's regime check is then skipped, not faked).
- **Watch modes (`-w`) poll** via `rich.Live` rather than using a native websocket.
  Call sites are unchanged, so an `Info.subscribe` upgrade is drop-in later.
- **No CI / lint / type-check tooling** is configured — only `pytest` under
  `[tool.pytest.ini_options]`. Adding CI is a reasonable next step.
- **Live paths are unexercised against a real exchange** (see below) — they are
  covered by parse/response-shape tests and mocks, not a live fill.

## What's Not Finished (deferred, pending credentials — not code gaps)

Each of these is code-complete and mock-tested; only the live run is outstanding:

- [ ] **Phase 1 gate** — place + manage a real testnet order (needs a funded agent wallet).
- [ ] **Phase 3 gate** — a real LLM shadow run on paper/testnet (needs `ANTHROPIC_API_KEY`).
- [ ] **Phase 4** — a real config-tuner LLM run from a live cohort (needs a key + ≥5-sample cohort).
- [ ] **Phase 5** — accumulate resolved trades until `graduation.assess` clears, then mainnet at tiny caps.
- [ ] **Live native-trigger reconciliation** — validate slippage / partial-fill handling on testnet.

### Open questions still on their default answer (PLAN.md §13)

Confirm with the user before a task relies on one — don't assume:
LLM scope (choose-among-supplied vs also generate candidates), candidate source,
news input, tuner autonomy, decision cadence. (§13 Q6 — native SL/TP as a hard
prerequisite — has been **confirmed**, not left on default.)

---

## External Dependencies

| Service/Library | Version | Purpose | Notes |
|-----------------|---------|---------|-------|
| `typer` | ≥0.12 | CLI framework | core |
| `rich` | ≥13 | tables, live watch | core |
| `pydantic` / `pydantic-settings` | ≥2.6 / ≥2.2 | domain models, hard caps from env | core |
| `httpx` | ≥0.27 | public `/info` reads (marks, book) | core — keeps paper keyless |
| `hyperliquid-python-sdk` | ≥0.9 | live order placement/signing | `[exchange]` extra, **lazy** |
| `eth-account` | ≥0.11 | agent-key signing / address derivation | `[exchange]` extra, **lazy** |
| `websocket-client` | ≥1.7 | (future) native subscriptions | `[exchange]` extra |
| `anthropic` | ≥0.40 | the LLM decision + tuner calls | `[llm]` extra, **lazy** |
| `pytest` | ≥8 | test suite | `[dev]` extra |
| Anthropic API | — | `claude-sonnet-4-6` (decision), `claude-opus-4-8` (tuner) | needs `ANTHROPIC_API_KEY` |
| Hyperliquid API | — | marks/book (public) + order placement (signed) | testnet + mainnet endpoints |

The keyless invariant is load-bearing: **never add a top-level import of
`anthropic`, `hyperliquid`, or `eth_account`** into a hot import path — it breaks
paper mode and the test suite. Verified by running the suite + paper in a venv that
has none of the extras installed.

---

## Where to Pick Up

1. **Get a funded testnet agent wallet** → close the Phase 1 gate (a real order) and
   run `exec run` on testnet to start filling the trades ledger.
2. **Set `ANTHROPIC_API_KEY`** → run `hl exec shadow` for the Phase 3 gate (sane,
   fully-logged real decisions) and let it accumulate tuner training data.
3. **Let trades resolve** → once `exec report` shows the graduation verdict passing
   (`GRADUATION_MIN_TRADES`/`_DAYS`/`_EXPECTANCY`), go mainnet at tiny caps.
4. **Optional:** a webhook/email channel tailing `alerts-<network>.log`; a richer
   multi-timeframe regime model; live native-trigger reconciliation; CI.

## Contacts & Related

- Original author: zayminmaw (`zayminmaw77@gmail.com`)
- Spec / source of truth: [`PLAN.md`](../PLAN.md); working memory: [`AGENT-CONTEXT.md`](../AGENT-CONTEXT.md)
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs
- SDK: `hyperliquid-python-sdk`; reference CLI surface: `chrisling-dev/hyperliquid-cli` (TypeScript)
