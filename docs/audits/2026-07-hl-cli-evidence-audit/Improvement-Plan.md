# Improvement Plan — ordered, reversible, safety-first

Ground rules for this plan: **nothing ships to mainnet before the DANGEROUS fixes.** Every change is reversible, gets a test that proves it, and is validated on paper/shadow/testnet before real money. No change ships on vibes. Line references are to `main` @ `b82bbdf`; confirm they still hold before editing.

---

## 1. Safety fixes first (DANGEROUS — before the next trade)

### D-1 · Emergency close must be confirmed; failure must not read as a clean abort
**Problem** (`runner.py:344` / `_secure:444`): the ledger row is resolved `"aborted"` whether or not `emergency_close` actually filled. A failed close ⇒ a naked, unprotected, mislabeled position (`Verdicts.md` Q2).
**Fix**
- In `_secure`, check `closed.accepted` **and** `closed.filled_size > 0`. If the close is not confirmed, do **not** resolve the row as `aborted`; write a distinct terminal-but-unsafe status (e.g. `abort_failed`) and set a `needs_manual_intervention` flag on the row.
- Emit a `level="critical"` alert `emergency_close_failed` with coin/size/oid so a human is paged immediately (not on the next pass).
- Add a bounded retry (2–3 attempts, short backoff) around `emergency_close` before declaring it failed.
**Test** — fake exchange where `place_protection` fails and `emergency_close` returns `accepted=False`: assert the row is `abort_failed` (not `aborted`), the critical alert fired, and no row is silently marked closed. Add the happy-path regression (close succeeds → `aborted`).
**Revert** — single-function change; revert restores current behavior.

### D-2 · Rate-limit / transient-error handling on order writes
**Problem** (`hyperliquid.py`, C7): no 429/backoff/retry on `place_order`/`cancel`. A rate-limited write during protection or emergency-close is the trigger for D-1's naked position (HL enforces address-based limits — `Evidence.md` E7).
**Fix**
- Wrap SDK write calls with bounded exponential backoff (reuse `core/backoff.py`) that retries **only** idempotency-safe failures (429, transport/connection errors) and **never** blindly retries a possibly-applied order without a client id (see D-3).
- Surface exhausted-retry as a definitive failure the caller already handles (reject/abort path), now with D-1's confirmation logic behind it.
**Test** — fake client raising a rate-limit error N-1 times then succeeding: assert the order eventually places; assert a non-retryable 4xx is not retried.
**Revert** — wrapper is additive.

### D-3 · Attach a Hyperliquid `cloid` to every order (resolvable idempotency)
**Problem** (E9): the idempotency key is internal-only, so a transport-unknown outcome can't be *resolved* against the exchange and is conservatively treated as spent (a missed trade) — and D-2's retries can't safely re-send without a client id.
**Fix** — pass a deterministic `cloid` (derived from candidate id / trade-slice) on `place_order`; on a transport-unknown outcome, query the exchange by `cloid` to learn whether it filled before deciding skip-vs-retry.
**Test** — simulate transport error after submit; assert the follow-up `cloid` query drives the decision, no double-fire.
**Revert** — `cloid` is optional on the wire; drop the plumbing to revert.

> **Gate:** D-1, D-2, D-3 land, tests green, and one full protection-failure drill passes on **testnet** before any further mainnet use.

---

## 2. Constrain the LLM to its evidenced authority

### L-1 · Flat sizing until conviction is calibrated (default OFF)
**Change** — add a tunable `conviction_sizing_enabled` (default **false**). When off, `_size` uses the floor fraction (or a fixed fraction) for every gate-approved trade; conviction is still logged. Re-enable only after L-4 shows conviction predicts realized R.
**Why** — `Evidence.md` E1/E6: uncalibrated scalar moving real size, no evidence it helps.
**Test** — with the flag off, size is independent of conviction across a sweep; with it on, current behavior. **Revert** — flag flip.

### L-2 · Rule-based arbiter behind a flag, for A/B (the core question)
**Change** — add a deterministic `decide_fn` alternative (`decide_rule`) selectable by config: e.g. *act iff the gate would approve AND regime supports AND RR-at-mark ≥ floor + margin*. Run it in **shadow alongside** the LLM decider so both books accumulate outcomes on the same candidates.
**Why** — E1/E4: no evidence the LLM beats a rule; the tool already logs everything needed to find out. This directly answers "does the LLM add value."
**Test** — deterministic rule fixture; assert identical candidates route to both books; assert the rule never exceeds a cap.
**Revert** — config selects the LLM decider (current default).

### L-3 · Keep the risk-increasing sentry `add` disabled until a baseline exists
**Change** — hard-default `sentry_max_adds_per_position=0` until the executor has graduated on shadow/testnet. Document that `add` is the one lever that increases risk mid-trade (A5).
**Test** — with the cap at 0, `_check_add` always rejects. **Revert** — raise the cap.

### L-4 · Calibration & sycophancy instrumentation (measurement, not a code-path change)
**Change** — add a `hl exec report` section (or a small analysis script over the decision log) that computes: conviction-bucket → realized avg-R and win-rate (calibration curve); act-rate vs. thesis strength; and a **sycophancy probe** — periodically re-run a sample of past candidates with the `reasoning`/`news` fields (a) intact, (b) neutralized, (c) argued the opposite way, and log verdict deltas.
**Why** — E1/E2: turns "is the LLM calibrated / sycophantic" from opinion into a number on *this* tool's data. Gates L-1 re-enable.
**Test** — fixture decision-log rows produce the expected calibration table.

### L-5 · Injection hardening of the thesis fields
**Change** — before enrichment, sanitize/flag `reasoning`/`news` for imperative-injection patterns ("ignore", "you must", "conviction = ", "override"); keep the existing prompt guardrail; optionally add a lightweight pre-check that a flagged candidate is logged and de-prioritized.
**Why** — E3: `reasoning`/`news` is the untrusted-text injection surface. (Blast radius is already bounded by the gate; this reduces spurious `act`s.)
**Test** — an injected candidate is flagged and does not raise conviction/size beyond caps.

---

## 3. Execution-quality improvements (grounded in E5/E7)

### X-1 · Slippage-capped marketable-limit entry
**Change** — replace the raw market entry with an IOC **marketable-limit** at `mark ± slippage_cap` (`slippage_cap` a hard cap in `.env`, e.g. 0.15%). Fills like a market order in normal conditions but refuses a fill worse than the cap (protects the leverage-amplified slippage in E5). Preserve the "filled-or-nothing → protected" property by treating a non-fill as a clean no-op (no phantom position).
**Test** — fake book where price is inside/outside the cap; assert fill vs. clean no-op; assert protection only places on a real fill.
**Revert** — config toggles back to market.

### X-2 · `$10` minimum-notional pre-check
**Change** — in the gate, reject (with a clear reason) or round-up-refuse any order whose `size × price < HL_MIN_NOTIONAL` (default 10, verify current HL value). Prevents a silent exchange reject on small low-conviction/wide-stop orders (E7).
**Test** — a sub-$10 sized candidate is rejected with `min notional` reason.

### X-3 · Reconcile the "wait for a clean entry" prompt with market execution
**Change** — the decision prompt (A2) should stop implying the model can pick a better *price* (it can't — entry is at the mark). Reframe `wait` as "defer until the setup is *valid* at the mark," not "wait for a better fill." Keeps timing judgment meaningful and removes the false affordance.
**Test** — prompt-diff review; shadow-run sanity that `wait`/`act` distribution stays sane.

### X-4 · Real-time-ish portfolio loss bound (STRENGTHEN the daily breaker)
**Change** — in addition to the per-pass daily-loss check, have the sentry/monitor pass trip the breaker when **open+realized** drawdown crosses the daily limit, and consider tightening/reducing open exposure (not just halting new fires). Document explicitly that native stops, not the breaker, bound single-position loss between passes (B7).
**Test** — construct a day where open uPnL alone breaches the limit; assert the breaker trips and no new fires occur.

---

## 4. Operational / security

### O-1 · Encrypt the key at rest (E10)
**Change** — add optional passphrase/OS-keyring encryption at the existing `save`/`load` seam in `keystore.py`; keep `0600`/`0700` and the refuse-if-readable check. Agent-wallet scoping already caps blast radius; this closes the last unanimous-best-practice gap.
**Test** — round-trip encrypt→decrypt; a wrong passphrase fails closed; perms still enforced.

### O-2 · Reconciliation that can act, not just alert (E9, C8)
**Change** — on detecting an exchange position the ledger doesn't know, offer an auto-safe response (place protective triggers / flatten / adopt via the existing sentry `adopt` path) behind a config flag, in addition to the alert; run the reconciliation check on **every** pass (including shadow/idle), not only fire-enabled ones.
**Test** — inject an unmanaged position; assert the chosen safe action fires (or alerts) deterministically.

---

## 5. Validation protocol (no change ships on vibes)

Every change above proves itself in this order before touching mainnet:

1. **Unit test** proving the specific behavior (listed per item).
2. **Paper** — run `hl exec run` / `hl agent run` on paper against live public marks; confirm no regression in fire/reject/abort tallies and that the change behaves as designed.
3. **Shadow** — run in shadow so decisions and hypothetical outcomes accumulate in the decision log; this is the real integration test against live data (per `CLAUDE.md`).
4. **Testnet** — for anything touching order writes/protection (D-1, D-2, D-3, X-1), run a live protection-failure drill on testnet with fake money and real fills.
5. **Graduation** — only after the graduation gate (B9) passes on shadow/testnet data (`≥20` resolved trades over `≥7` days, `avg_r > 0`) does mainnet open — and then at the smallest caps.

**Success metrics & minimum sample before mainnet, per class of change:**
- LLM-vs-rule A/B (L-2): a decision on whether the LLM stays requires **≥ the graduation sample** of resolved trades in *each* book, compared on avg-R and drawdown. If the LLM does not beat the rule, ship the rule.
- Conviction re-enable (L-1): a monotonic conviction→avg-R relationship over ≥ graduation sample (L-4), else stay flat.
- Execution changes (X-1): measured slippage/fee per fill vs. the market-order baseline over ≥30 fills.

---

## 6. Evidence gate for any future order-path feature

Before anything new can touch the order path, it must pass this checklist (paste into the PR):

1. **Evidence** — what external research or documented exchange behavior supports it? (grade a–d, with sources). Grade (c)/(d) features ship OFF by default behind a flag.
2. **Boxing** — can it exceed a hard cap, pick a disallowed coin, set a stop, or bypass the gate? If yes, redesign until no.
3. **Failure behavior** — what happens on transport error, rate limit, partial fill, crash mid-action? A "naked/unprotected/untracked position" outcome is a blocking defect.
4. **Idempotency** — can a retry double-submit? Is there a `cloid` to resolve an unknown outcome?
5. **Testing** — unit test proving the behavior + the failure path; validated paper → shadow → testnet before mainnet.
6. **Kill switch** — does the breaker/kill-switch halt it? Is there an alert when it fails?
7. **Measurement** — is the outcome logged so its value can be proven (or disproven) on this tool's own data?

A feature that can't answer 1–7 does not go in front of real money.
