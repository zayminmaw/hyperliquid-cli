# AGENT-CONTEXT

> Last updated: 2026-07-01 | Session: synced all docs (README, ACTION-ITEMS, docs/*) to code — candle/regime + WAIT-defer now reflected (177 tests)

---

## 🎯 CURRENT TASK

- Task: Phase 5 — Mainnet hardening ✅ code-complete
- Goal: native exchange-side SL/TP at entry, runtime prereq enforcement, graduation checklist in report, key review, alerting
- Status: done (177 tests pass). Build plan fully implemented through all phases; all docs synced to code.
- Next action: none coding-side. Remaining is operational — supply agent keys, run testnet/shadow to accumulate resolved trades, let graduation clear, then tiny mainnet caps. (Candle feed + regime: DONE. Wait→follow-up loop: DONE.) Optional follow-ups: webhook/email alert tailing alerts-<net>.log; live testnet native-trigger reconciliation.
- Blocked by: none. (Phase 1 live testnet order, Phase 3 shadow, Phase 4 config-tuner LLM, Phase 5 real graduation — all deferred pending keys.)

---

## 📍 LAST ACTION

- Did: synced ALL docs to code — README, ACTION-ITEMS.md (new 3.7 candle/regime + 3.8 WAIT-defer items), docs/{handover,modules,architecture,decisions,cli,setup}.md. Fixed stale test counts (143/152/153→177), removed the false "regime is always None" claims (regime now computed from candle feed), added the WAIT→defer loop + `HL_FOLLOWUP_MAX_ATTEMPTS` everywhere.
- Then (prior): follow-ups to the wait loop — a tripped breaker now FREEZES re-checks (`due=[]` when breaker_tripped; parked candidates keep attempts until it clears); `exec report` surfaces `deferred` count, `exec status` notes it (human view). 170 pass.
- Then (prior): built the wait→follow-up loop. Decision tool gains `recheck_in_minutes` (validator clamps to [0,1440]; missing→None); `Decision.recheck_in_minutes`; new `HL_FOLLOWUP_MAX_ATTEMPTS` cap (default 3, 0=disabled). New `deferred` table + `defer_candidate`/`due_deferred`/`drop_deferred`/`deferred_count` + `DeferredCandidate`. Runner refactored: extracted `_evaluate`+`_fire_and_reconcile` (shared by intake + deferral re-check loops), `_wait` intercepts act+wait BEFORE the gate to park (HWM still advances; gate stays pure), `_schedule_recheck` clamps next-check WITHIN freshness (None⇒terminal reject when no room/attempts). PassSummary +`rechecked`/`deferred`. Due deferrals re-checked first each pass with fresh data; skipped in dry_run.
- Result: docs match code; 177 pass (verified this session)
- File(s) touched: README.md, ACTION-ITEMS.md, AGENT-CONTEXT.md, docs/{handover,modules,architecture,decisions,cli,setup}.md
- Prior: candle feed + deterministic regime (MarksFeed.candles, executor/regime.py, enrich/runner wiring). `MarksFeed.candles` (keyless /info candleSnapshot, lookback×interval window) + `Exchange.get_candles` on both backends; new `executor/regime.py` (Kaufman efficiency-ratio classify→trend/range/None at <20 bars; compact 12-bar `summarize`); runner gathers per-coin context once (best-effort `_fetch_candles`, degrades on feed failure), feeds `enrich(candles=, regime=)`; regime now reaches the gate. (Prior: decision.py P1 rationale-first tool order, P2 temperature-by-model guard, P3 conviction anchor + execution-trader persona.)
- Result: 162 pass; keyless-import invariant re-verified in /tmp/hlcore
- File(s) touched: hlcli/core/types.py, hlcli/exchange/{marks,base,paper,hyperliquid}.py, hlcli/executor/{regime(new),enrich,runner,decision}.py, tests/{_helpers,test_regime(new),test_marks,test_executor}

---

## 🗺️ CODEBASE MAP

| Path | Role |
| ---- | ---- |
| `PLAN.md` | Authoritative spec — resolves conflicts |
| `ACTION-ITEMS.md` | Phase-by-phase status (source of truth) |
| `hlcli/core/config.py` | Hard caps (pydantic-settings, `HL_*` env); `get_caps()` |
| `hlcli/core/config_schema.py` | Tunable surface + `clamp()` + `load_tunable()` (incl. `max_hold_minutes`) |
| `hlcli/core/{network,types,llm}.py` | network gate · domain types · `make_client()` (the ONE lazy anthropic import) |
| `hlcli/cli/app.py` | Typer app + global callback; wires `cli/commands/*` |
| `hlcli/cli/context.py` | `GlobalState`, `build_for(state, for_write)` — resolves account/key, mainnet gate |
| `hlcli/cli/commands/` | account/trade/markets/asset/exec_/config/**tune** · `cli/{stubs,watch,output}.py` |
| `hlcli/accounts/{store,keystore}.py` | SQLite account metadata · `0600` agent-key files |
| `hlcli/exchange/marks.py` | public `/info` reads over **httpx** (keyless); `api_url` |
| `hlcli/exchange/hyperliquid.py` | live backend (SDK+eth_account lazy); reads keyless, writes need key |
| `hlcli/exchange/{base,paper,factory}.py` | protocol · paper (state-backed book + fills) · factory |
| `hlcli/state/store.py` | sqlite: intake/HWM/idempotency/decision_log/**trades**/**deferred**(WAIT re-checks)/paper_positions; `DeferredCandidate`; `open_state` |
| `hlcli/executor/gate.py` | `evaluate` (first-failure gate) + `_size` (fixed-fractional) + `infer_side` |
| `hlcli/executor/{enrich,decision,regime}.py` | `EnrichedContext`(carries candles+regime) · `decide`(sonnet-4-6, rationale-first strict tool)+`validate_decision`+`load_decision_prompt` · `regime.classify`(ER trend/range)+`summarize` |
| `hlcli/executor/{intake,execute,runner,resolve,monitor,protect}.py` | propose · fire · `run_once`(decide_fn/fire_enabled/alerter) · close-out→trades (native_protected on live) · health · native SL/TP + emergency-close |
| `hlcli/tuner/{stats,config_tuner,prompt_tuner,promote}.py` | cohorts(sample-gated) · opus-4-8 config(strict)+prompt tuners · proposed→active+diff/history |
| `hlcli/safety/{breaker,alerts,graduation}.py` | kill switch+loss-limit · JSONL+stderr alert sink · mainnet-readiness verdict (in `exec report`) |

---

## 🧠 DECISIONS

- [2026-06-27] LLM owns judgment, code owns mechanics + safety; LLM output is gate input, never a bypass
- [2026-07-01] wait→follow-up: act+wait is DEFERRED not rejected — runner intercepts WAIT before the gate (gate stays pure), parks in `deferred` table, HWM still advances. Re-check scheduled WITHIN max_signal_age (clamped; no room⇒terminal reject). attempts = HL_FOLLOWUP_MAX_ATTEMPTS re-checks (default 3); each re-check uses FRESH enrich/candles/regime. Due deferrals processed before new intake; skipped in dry_run AND while breaker tripped (frozen, attempts intact). `exec report.deferred` + `exec status` note surface the parked count.
- [2026-06-27] hard caps in .env (off-limits to LLM/tuner); tunable surface in config/active_config.json, clamped on load
- [2026-06-27] anthropic + live-exchange deps lazy-imported so paper + tests run without keys/signing libs
- [2026-06-27] Order-path model claude-sonnet-4-6; daily tuner claude-opus-4-8
- [2026-06-27] Idempotency key (candidate id) recorded BEFORE fire → crash skips (missed trade), never double-fires
- [2026-06-27] Candidate side inferred from level geometry (long: sl<entry<tp); incoherent → rejected at intake + gate
- [2026-06-27] Paper book persists in state-<network>.db; manual paper (no state) stays in-memory
- [2026-06-30] Decision: out-of-range conviction is CLAMPED; bad enum / missing / non-numeric is DROPPED (never guessed). regime left None (no price-history feed); shadow=`fire_enabled=False` (logs+advances HWM) vs dry_run (mutates nothing)
- [2026-06-30] Phase 4: executor-side resolver writes trade outcomes (SL/TP at trigger price, expiry at mark). Tuners clamp on propose+promote+load, sample-gated (no cohort ⇒ no model call); propose→approve everywhere (§13 Q4)
- [2026-06-30] Candles: keyless /info candleSnapshot (15m, lookback 48, once per coin per pass, uncached, best-effort). Regime computed in CODE (Kaufman ER, threshold 0.35, <20 bars⇒None) not by the LLM — feeds the gate + a 12-bar OHLC tail to the model. Decision tool reordered rationale-first; temperature sent only to models that accept it.
- [2026-06-30] Phase 5 (§13 Q6): native SL/TP is a HARD mainnet prereq — enforced at runtime (unprotectable live entry → emergency MARKET close, status `aborted`, no ledger, key already spent so no re-fire). Triggers scoped to testnet+mainnet (resolver closes via reduce-only MARKET there; paper keeps LIMIT-at-level). Graduation thresholds are hard caps. Alerts = JSONL+stderr (no deps/keys), None in shadow.

---

## ⚠️ GOTCHAS

- §13 open questions have default choices (LLM scope, candidate source, news input, tuner autonomy, cadence, native SL/TP). Confirm with user before a task relies on one — don't assume.
- Keep no top-level imports of anthropic / hyperliquid / eth_account in hot paths (breaks paper + tests). Verified by `/tmp/hlcore` venv (no exchange extra).
- Marks/book/reads go through **httpx** `/info` (core dep), NOT SDK `Info` — don't "simplify" onto the SDK or paper stops being keyless. SDK+eth_account are write-only (signing).
- Tunable values must never reach the order path unclamped.
- Executor entry is a MARKET order (gate), so a live `accepted` means `filled`; the runner records open_trade + sizes protection from `OrderResult.filled_size`/`avg_price`, NOT the intended order. Paper fixtures have mark==entry so counts/P&L are unchanged. Don't revert to a GTC limit entry — it reintroduces phantom-position tracking (review finding H1).
- Python 3.12 is at `/opt/homebrew/bin/python3.12` (system default is 3.11; project needs ≥3.12).
- Executor tests inject `run_once(..., decide_fn=...)` (act_now/drop in _helpers) so the LLM is never hit; real `decide`/tuners tested via FakeClient/FakeTool/FakeText. `exec`/`tune run` (with eligible cohort) need ANTHROPIC_API_KEY; empty stream / gated tuner skip the call.
- Tuner artifacts live beside `config_path`: proposed_/active_ config.json + prompt.md + promotions.jsonl. Tests MUST pass an isolated `caps(config_path=tmp/...)` or they write into repo `config/`. `tune` uses network-scoped state db for resolved trades.

---

## 🔗 CONTEXT LINKS

- Plan: ./PLAN.md
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs (use for Phase 1 exchange + Phase 5 trigger orders)
- Reference CLI surface: chrisling-dev/hyperliquid-cli (TypeScript)
- SDK: hyperliquid-python-sdk
