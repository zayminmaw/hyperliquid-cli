# Evidence gate — what any feature must pass before touching the order path

From the 2026-07 evidence audit (`docs/audits/2026-07-hl-cli-evidence-audit/`, Improvement-Plan §5–§6).
A feature that can't answer all seven does not go in front of real money. Paste the checklist into the PR.

## The 7-point order-path checklist

1. **Evidence.** What external research or documented exchange behavior supports it? Grade it:
   (a) rigorous research / documented best practice · (b) mixed, context-dependent ·
   (c) no credible evidence either way · (d) evidence or incidents against it.
   Grade (c)/(d) features ship **OFF by default behind a flag** (precedent: conviction sizing, sentry ADD).
2. **Boxing.** Can it exceed a hard cap, pick a disallowed coin, set a stop, or bypass the gate?
   If yes, redesign until no. The LLM's output is an input to the gate, never a bypass.
3. **Failure behavior.** What happens on transport error, rate limit, partial fill, crash mid-action?
   A "naked / unprotected / untracked position" outcome is a **blocking defect** (precedent: `abort_failed`,
   confirmed emergency close, reduce-only retries).
4. **Idempotency.** Can a retry double-submit? Is there a `cloid` (client order id) to resolve an
   unknown outcome against the exchange instead of guessing?
5. **Testing.** A unit test proving the behavior **and its failure path**; then validated
   paper → shadow → testnet before mainnet (ladder below).
6. **Kill switch.** Does the breaker / kill switch halt it? Is there an alert when it fails?
7. **Measurement.** Is the outcome logged so its value can be proven — or disproven — on this
   tool's own data? (Precedent: conviction is logged even while sizing ignores it.)

## The validation ladder (no change ships on vibes)

1. **Unit** — `pytest` from the repo root; behavior + failure path.
2. **Paper** — `hl exec run` / `hl agent run` against live public marks; no regression in
   fire/reject/abort tallies.
3. **Shadow** — decisions + hypothetical outcomes accumulate in the decision log; the real
   integration test against live data.
4. **Testnet** — anything touching order writes gets a live drill with a funded agent wallet,
   including a **forced protection-failure** proving no naked position. (Also the place to confirm
   the `orderStatus`-by-cloid response parse.)
5. **Graduation** — mainnet opens only after `exec report`'s graduation gate passes on
   shadow/testnet data (≥ `HL_GRADUATION_MIN_TRADES` resolved over ≥ `HL_GRADUATION_MIN_DAYS`,
   `avg_r` > `HL_GRADUATION_MIN_EXPECTANCY`) — and then at the smallest caps. Only genuine
   strategy outcomes grade: `scaled` partials, `aborted`/`abort_failed` (mechanical protection
   failures, surfaced separately as `aborts`), and adopted rows (no LLM verdict) are excluded
   from `n` and expectancy. A nonzero `aborts` count is itself a reason not to promote.

## Success metrics per change class (minimum sample before mainnet)

| Change class | Metric | Minimum sample |
|---|---|---|
| LLM-vs-rule arbiter (`HL_DECISION_SOURCE`) | avg-R + drawdown, each book | ≥ graduation sample **per book**, same intake, separate `HL_DATA_DIR`s |
| Re-enabling conviction sizing (`sizing.enabled`) | monotonic bucket→avg-R in `exec report`'s `conviction_calibration` | ≥ graduation sample |
| Execution changes (slippage cap, order type) | measured slippage + fees per fill vs baseline | ≥ 30 fills |

If the LLM does not beat the rule baseline, ship the rule. If conviction does not predict realized R,
sizing stays flat. Evidence decides; not vibes.
