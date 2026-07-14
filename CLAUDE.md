# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session Bootstrap

At the start of every session, before doing anything else:

1. Run `token-saver:start` — enable token efficiency rules
2. Run `ctx:auto` — read `AGENT-CONTEXT.md` and orient yourself; summarise current task, last action, and any blockers in one short paragraph

## How I want you to work (read this first)

These three rules override the instinct to be fast or to look productive. In this codebase,
a wrong assumption is more expensive than a clarifying question.

1. **Ask when unclear or unsure.** If a request is ambiguous, if there are two reasonable
   interpretations, or if you'd be guessing at intent, file structure, or an API contract —
   stop and ask. A short question now beats a large wrong diff. Do **not** invent file paths,
   component names, env vars, or endpoints to fill a gap; ask instead.

2. **Think before you code.** Before writing or editing, state your plan: what files you'll
   touch, what you expect to find, and why. For non-trivial changes, outline the approach and
   wait for confirmation rather than producing a large diff immediately.

3. **Verify before you assume.** Read the actual file before editing it. Confirm a function,
   prop, hook, or export exists before calling it. Check which library/version is in use
   (this repo has several overlapping ones — see Gotchas). Don't assume a pattern from one
   part of the app applies everywhere; grep and confirm.

If a task conflicts with these rules, surface the conflict instead of silently resolving it.

## Status: greenfield

There is **no code yet**. `PLAN.md` is the authoritative spec — a detailed, opinionated build plan for a Python CLI that trades on Hyperliquid. Read it before writing anything; the sections below summarize the load-bearing decisions but `PLAN.md` is the source of truth and resolves conflicts.

Work proceeds **phase by phase** (PLAN.md §12), each phase ending at a review gate:

| Phase                        | Scope                                                          | Gate                                                  |
| ---------------------------- | -------------------------------------------------------------- | ----------------------------------------------------- |
| 0 — Skeleton                 | CLI scaffold, config + clamp, network resolution, paper stub   | `hl --help`; paper `once` no-ops cleanly              |
| 1 — Manual trade             | Account store, marks feed, Mode A orders, monitor, watch modes | Place + manage a real **testnet** order               |
| 2 — Executor (deterministic) | Intake, gate, sizing, execute, idempotency, breaker            | Candidates → paper fills; deterministic; restart-safe |
| 3 — LLM decision             | Enrich, decision prompt, structured output + clamp, `shadow`   | Shadow runs produce sane, fully-logged decisions      |
| 4 — Self-tuning              | Stats cohorts, config + prompt tuners, propose→approve         | Tuner proposes from logged outcomes; `promote` works  |
| 5 — Mainnet hardening        | Native exchange SL/TP, mainnet gate, key review, alerting      | Testnet/shadow expectancy clears → tiny mainnet caps  |
| 6 — Sentry (a→d)             | In-trade manager: trail engine → LLM shadow → gated live → ADD | Per sub-phase, PLAN.md §14                            |
| 7 — Agent mode (a→d)         | Supervisor + intake dir, daily journal, reflection, adoption   | Per sub-phase, PLAN.md §15                            |

Don't build ahead of the current phase or skip a gate.

## The one design decision everything hangs off

**The LLM owns judgment; deterministic code owns mechanics and safety.** This inverts the rule from the older ThirdEye executor (which kept the LLM out of the order path). Here the LLM _is_ the executor, but only inside a box the code draws:

- **LLM decides** (per candidate setup): act/skip, timing (now/wait), conviction (0–1). Structured JSON, low temperature.
- **Code decides** everything that touches money: sizing math, SL/TP placement, risk caps, tick/size rounding, idempotency, the kill switch.
- The LLM's output is an **input to the risk gate, never a bypass of it.** It is validated and clamped before anything reaches the exchange — it can never exceed a hard cap, pick a disallowed coin, or skip the gate. Output that fails schema validation is **dropped and tallied, never guessed at.**

Every decision is logged with full input context + resulting fill + outcome. That log is both the audit trail (P&L attribution / replay) and the training data for the tuners. Preserve it.

## Two trading modes

- **Mode A — manual** (`hl trade …`): direct human orders, no LLM, only hard caps + exchange validation.
- **Mode B — LLM executor** (`hl exec …`): human supplies the _thesis_ (candidate setups with entry/tp/sl/reasoning/news); the LLM supplies _execution judgment_; the code supplies _safety and math_.

## Networks: paper → testnet → mainnet

`paper` (simulated book, public mainnet marks, no keys) is the **default everywhere**. `testnet` uses fake money with real fills. `mainnet` is **gated, not blocked** and requires _all_ of: `HL_ENABLE_MAINNET=1` env flag **and** `--network mainnet` **and** a typed confirmation (`-y` skips the prompt but still needs the env flag). Native exchange-side SL/TP trigger orders are a hard mainnet prerequisite (a crashed executor must not leave a position unprotected).

## Config: hard caps vs tunable surface — keep this split intact

This split is what makes self-tuning safe. Never let a tunable value reach the order path unclamped.

- **`.env` — hard caps, off-limits to the LLM and the tuner.** Network/mainnet gate, paths, `STARTING_EQUITY`, `MAX_NOTIONAL_PER_TRADE`, `MAX_CONCURRENT_POSITIONS`, `DAILY_LOSS_LIMIT_PCT`, `MAX_LEVERAGE`, `RR_FLOOR`, `ALLOWED_COINS`, `MAX_SIGNAL_AGE_MINUTES`, model names + token budgets.
- **`config/active_config.json` — the tunable surface.** Regime gate, risk profile, `risk_per_trade_pct`, conviction→size mapping, decision-prompt parameters. **Loaded and clamped in code** so a bad value can't reach the order path. Missing file → safe defaults.

## The risk gate (deterministic, first-failure wins)

Order of checks — implement as a short-circuit pipeline:

```
schema-valid LLM output → kill switch → daily-loss-limit → freshness
  → allowed-coin → regime sanity → level sanity (entry/sl/tp coherent)
  → R:R floor → mark sanity (mark present, inside sl/tp, R:R at mark ≥ floor)
  → one-per-coin → max-concurrent
  → sizing + notional cap + leverage cap → conviction→size clamp
```

- **Sizing:** fixed-fractional, priced at the **mark** (the entry is a MARKET order) — `risk_per_trade_pct × equity ÷ |mark − sl|`, clamped by `max_notional_per_trade` and `max_leverage`. Conviction only scales size _within_ those bounds; it never raises the ceiling.
- **One-per-coin** makes the per-trade cap the total per-coin exposure cap.

## Self-tuning is out-of-path and propose→approve

Both tuners run **outside** the order path and **propose** changes a human approves before they go live — never auto-applied to mainnet. Config tuner edits the tunable surface from resolved-trade cohorts (sample-gated: no eligible cohort ⇒ the model isn't called). Prompt/strategy tuner refines the _decision prompt_ from logged decisions-vs-outcomes. Flow: `hl tune run` (writes `proposed_*`, never active) → `diff` → `promote` → `history`.

## Models

- **Order-path decision** → `claude-sonnet-4-6` (hot loop; the gate is the real safety authority, so this needn't be the largest model).
- **Daily tuner** → `claude-opus-4-8` (out-of-path, once a day, deeper reasoning worth the cost).

Both configurable via `.env`. **`anthropic` and live-exchange deps are lazy-imported** so `paper` mode and the test suite run with no keys or signing libs present — preserve this; don't add top-level imports of `anthropic`, `hyperliquid`, or `eth_account` into hot import paths.

## Intended stack & module layout (PLAN.md §12)

Python ≥ 3.12 · `typer` + `rich` (CLI/TUI) · `pydantic` + `pydantic-settings` · `hyperliquid-python-sdk` · `eth-account` · `httpx`/`requests` · `websocket-client` · `anthropic` (lazy) · `pytest`.

```
hlcli/
├── cli/        # typer app + command groups + json/rich output
├── core/       # config (hard caps), config_schema (tunable+clamp), domain types, network resolution + mainnet gate
├── exchange/   # base protocol, paper book, hyperliquid (testnet+mainnet), marks+cache
├── accounts/   # sqlite multi-account store + keystore
├── executor/   # intake, enrich, decision (LLM), gate, execute, monitor
├── tuner/      # stats cohorts, config_tuner, prompt_tuner, promote
├── state/      # sqlite: book, idempotency, high-water mark, decision log
├── safety/     # breaker / kill switch / loss limits / mainnet gate
└── tests/
```

Accounts live in a local SQLite store (`~/.hyperliquid-cli/accounts.db`). Use **agent ("API") wallets by default** for trading accounts — they can trade but not withdraw. Keys never hit logs or the LLM decision context.

## Command surface (target)

Noun → verb. Global flags: `--network paper|testnet|mainnet` · `--account <alias>` · `--json` · `--dry-run` · `-y`.

```
hl account  add | ls | set-default | remove | positions | orders | balances | portfolio
hl markets  ls | prices
hl asset    price <coin> | book <coin>            # -w for live watch
hl trade    order limit|market|stop-loss|take-profit | cancel | cancel-all | set-leverage   # Mode A
hl exec     propose | once | run | shadow | status | report | breaker                        # Mode B
hl sentry   once | run | shadow | manage | adopt | status | log                              # in-trade manager (§14)
hl tune     run | diff | promote | history
hl config   show | set | edit
hl agent    run | status        # autonomous supervisor (§15)
hl journal  write | show | ls   # daily journal (§15)
```

`exec once` = one full pass (intake → enrich → decision → gate → fire → monitor). `exec run` = continuous loop. `exec shadow` = decide and log but **fire nothing** (pre-mainnet confidence + tuner training data). `exec breaker` = kill switch (halts new fires; open positions still managed). Idempotency + a high-water mark on the intake stream mean a restart never double-fires.

## Evidence gate (2026-07 audit — binding on order-path changes)

Any feature that touches the order path must pass the 7-point checklist in `docs/evidence-gate.md`
(evidence grade, boxing, failure behavior, idempotency, tests, kill switch, measurement) and the
paper → shadow → testnet → graduation validation ladder. **Audit-set defaults are deliberate — do
not "fix" them back:** conviction→size scaling OFF (`sizing.enabled`; re-enable only when
`exec report`'s calibration proves it), sentry ADD cap 0, `HL_DECISION_SOURCE` selects the llm|rule
arbiter for A/B, an unconfirmed emergency close books `abort_failed` (never `aborted`), entries are
slippage-capped IOC limits. Full rationale: `docs/audits/2026-07-hl-cli-evidence-audit/`.

## Testing

No tooling is configured yet (no `pyproject.toml`/CI). When adding it, the intended runner is `pytest`. Prioritize tests for the **highest-risk code**: the gate/sizing, the LLM-output validator/clamp, the paper exchange + monitor, intake idempotency + high-water mark, config-schema clamping, the mainnet gate, and the CLI. **Mock the LLM decision call** in tests (deterministic fixtures); `shadow` mode is the real integration test against live data.

## Open questions (PLAN.md §13)

The plan makes a default choice on each; if a task touches one, confirm the choice rather than assume: LLM scope (choose-among-supplied vs also generate candidates), candidate source, news input, tuner autonomy, decision cadence, and native SL/TP as a mainnet prerequisite.
