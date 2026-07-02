# AGENT-CONTEXT

> Last updated: 2026-07-02 | Session: ANTHROPIC_API_KEY via .env + masked in `config show`; 241 tests pass; next = operational testnet/paper runs (user supplying key + testnet wallet)

---

## 🎯 CURRENT TASK

- Task: Post-review hardening ✅ complete
- Goal: fix every bug/improvement from the 2026-07-02 end-to-end review (NaN clamps, wire rounding, trigger cleanup, position reconciliation, shadow outcomes, account safety, promote lifecycle)
- Status: done — 235 tests pass, keyless-import invariant re-verified, docs synced
- Next action: none coding-side. Operational as before: supply agent keys, run testnet/shadow (shadow now genuinely accumulates tuner/graduation outcomes), clear graduation, tiny mainnet caps.
- Blocked by: none

---

## 📍 LAST ACTION

- Did: ANTHROPIC_API_KEY now readable from `.env` (shell env wins) via `_LLMEnv` in core/llm.py, passed explicitly to `anthropic.Anthropic(api_key=…)`; kept OFF the Caps object; `hl config show` displays it masked (first4…last4, `…` if ≤8 chars, "not set" if absent)
- Result: 241 pass (was 235); verified live: set → `sk-a…TAIL`, unset → `not set`
- File(s) touched: hlcli/core/llm.py, hlcli/cli/commands/config.py, hlcli/tests/test_llm.py (new), .env.example, docs/setup.md

---

## 🗺️ CODEBASE MAP

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |
| `hlcli/core/config.py` | Hard caps (`HL_*` env); `get_caps()`; relative `config_path` anchors to `data_dir` |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` (non-finite ⇒ field default) + `load_tunable()` |
| `hlcli/core/{network,types,llm}.py` | network gate · domain types (`OpenOrder.is_trigger`) · llm: the ONE lazy anthropic import; key from shell env or `.env`, never on Caps; `masked_api_key()` |
| `hlcli/cli/context.py` | `GlobalState`, `build_for(state, for_write)` — account/key resolution, mainnet gate |
| `hlcli/cli/commands/` | account/trade/markets/asset/exec_/config/tune · exec run has failure backoff + per-pass tunable reload |
| `hlcli/accounts/{store,keystore}.py` | SQLite metadata (resolve is network-checked; alias globally unique) · `0600` keys (perms enforced on load too) |
| `hlcli/exchange/marks.py` | keyless httpx `/info`: marks/book/candles/`sz_decimals` (meta) |
| `hlcli/exchange/rounding.py` | pure wire rounding: size floors to szDecimals; px 5 sig figs / 6−szDecimals |
| `hlcli/exchange/hyperliquid.py` | live backend; writes rounded on the wire; `frontendOpenOrders` incl. triggers |
| `hlcli/exchange/{base,paper,factory}.py` | protocol · paper (rejects triggers; flips overfill unless reduce-only) · factory |
| `hlcli/state/store.py` | sqlite: intake/HWM/idempotency/decision_log/trades(+`shadow`, additive migrations)/deferred/paper book |
| `hlcli/executor/gate.py` | first-failure gate incl. mark sanity; `_size` priced at mark; `infer_side` |
| `hlcli/executor/{enrich,decision,regime}.py` | context (+resolved outcomes, `followup`) · `decide` + NaN-safe `validate_decision` · ER regime |
| `hlcli/executor/{intake,execute,runner,resolve,protect}.py` | content-hash batch ids · idempotent fire · `run_once` (ledger-first, shadow book, unmanaged alert) · resolver (vanished-position reconciliation, shadow orderless, trigger cleanup) · protection + `cancel_placed`/`cancel_coin_triggers` |
| `hlcli/tuner/{stats,config_tuner,prompt_tuner,promote}.py` | cohorts · tuners · promote consumes proposals, audit records content |
| `hlcli/safety/{breaker,alerts,graduation}.py` | kill switch + loss-limit (`persist=` for dry-run) · JSONL alerts · graduation verdict |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety; LLM output is gate input, never a bypass
- [2026-06-27] hard caps in .env; tunable surface clamped on load; anthropic + exchange deps lazy; sonnet-4-6 order path / opus-4-8 tuner; idempotency key recorded BEFORE fire
- [2026-07-01] wait→follow-up: act+wait DEFERRED not rejected; re-check inside freshness, `HL_FOLLOWUP_MAX_ATTEMPTS`; frozen while breaker tripped; re-checks labeled via `followup` in context
- [2026-07-02] Non-finite numbers NEVER clamp: NaN slides through min/max as the UPPER bound, so conviction/recheck are dropped and tunables fall back to defaults (`math.isfinite` everywhere a clamp guards money)
- [2026-07-02] Gate mark-sanity: the entry is a MARKET order ⇒ mark must exist, sit strictly inside sl/tp, and R:R **at the mark** must clear the floor; sizing + notional/leverage caps priced at the mark, not the proposed entry
- [2026-07-02] Ledger-first fills: trades row written on fill BEFORE protection; failed protection ⇒ emergency close + cancel placed triggers + row resolved `aborted` (was: no ledger). Positions the ledger doesn't know raise an edge-triggered `unmanaged_position` alert
- [2026-07-02] Live resolver reconciles against get_positions(): a vanished position (native trigger on a wick / manual close) books won/lost from candle extremes (SL checked first — pessimistic) else `closed` at mark; every live close cancels the coin's surviving reduce-only triggers
- [2026-07-02] Shadow books hypothetical trades (`trades.shadow=1`, entry at mark) resolved orderlessly — THIS is the tuner/graduation training data; shadow passes never touch real trades; hypothetical book honors one-per-coin
- [2026-07-02] Wire rounding is code's job: size FLOORS to szDecimals (never up past a cap), px = 5 sig figs then ≤6−szDecimals decimals; unknown coin passes through (exchange's reject is clearer). Reads use frontendOpenOrders so triggers are visible/cancelable
- [2026-07-02] promote() consumes proposal files (promotable exactly once) and records what went live; account resolve refuses a wrong-network alias; `account add` = row first, key second; relative HL_CONFIG_PATH anchors to HL_DATA_DIR

---

## ⚠️ GOTCHAS

- §13 open questions have default choices — confirm with user before a task relies on one.
- Keep no top-level imports of anthropic / hyperliquid / eth_account in hot paths. Verified 2026-07-02 in a fresh core-only venv (scratchpad `hlcore`; old `/tmp/hlcore` is PEP-668-locked, rebuild if needed).
- Marks/book/candles/meta go through **httpx** `/info`, NOT SDK `Info` — don't "simplify" onto the SDK or paper stops being keyless.
- PassSummary counters are disjoint: `rejected` = gate said no; `failed` = gate-approved but died at the exchange (reject/unfilled/aborted). Don't fold them back together.
- Executor entry is a MARKET order; ledger + protection size from `OrderResult.filled_size`/`avg_price`. Don't revert to GTC limit entry (review finding H1).
- test helpers' `caps()` pins `config_path=/nonexistent/...` so prompt/config reads never touch a dev's real `~/.hyperliquid-cli`; tuner tests still pass their own tmp `config_path`.
- Python 3.12 at `/opt/homebrew/bin/python3.12` (system default is 3.11).
- Executor tests inject `run_once(..., decide_fn=...)`; real `decide`/tuners tested via fakes. `exec`/`tune run` need ANTHROPIC_API_KEY.
- `resolved_trades(limit=N)` = most recent N (newest-closed first) — don't assume oldest-first.
- FakeLiveExchange (test_protect) models positions/open_orders/canceled; `fail_triggers="tp"` = partial-protection case.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
