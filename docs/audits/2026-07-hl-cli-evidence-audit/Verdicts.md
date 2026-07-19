# Verdicts

**Verdict legend:** **KEEP** (evidenced, sound) · **STRENGTHEN** (sound design, weak implementation) · **CONSTRAIN** (keep but narrow its authority/limits) · **REMOVE** (unjustified complexity) · **DANGEROUS — FIX BEFORE NEXT TRADE** (can lose funds now).

Every "Internal evidence" cell reads *none* because the tool has **never traded in any mode** — both state DBs are empty, no accounts, no keys (see `Inventory.md`). Blast radius is rated for the mainnet path, which is the point of the tool.

## Component-by-component verdict table

| # | Component | Why it was added | External evidence (grade → `Evidence.md`) | Internal evidence | Blast radius if wrong | Verdict |
|---|-----------|------------------|-------------------------------------------|-------------------|-----------------------|---------|
| B1 | Deterministic risk gate | Draw the box the LLM lives in | (a) — pre-emptive action-check is the recommended control (E3) | none | Total — it is the safety authority | **KEEP** |
| B5 | Hard caps in `.env` | Values LLM/tuner can never touch | (a) — sizing frame + illiquid-coin restriction both evidenced (E6, E8) | none | Total | **KEEP** |
| B6 | Tunable surface + `clamp()` | Make self-tuning safe | (a) — clamp-before-use is correct defense-in-depth | none | High (a bad tune reaching the order path) | **KEEP** |
| A3 | Drop-on-invalid LLM output | Never guess a verdict | (a) — counters "fabricated parameter" failure mode (E3) | none | Med | **KEEP** |
| C4 | Idempotent fire (atomic claim) | Restart-safe, no double-fire | (a) — textbook; matches recommended pattern (E9) | none | High (duplicate risk) | **KEEP** |
| C2 | Tick/size rounding | Valid orders on the wire | (a) — matches HL docs exactly (E7) | none | Med | **KEEP** |
| B9 | Graduation gate | Earn mainnet with real expectancy | (a) — sample-gated readiness is sound | none — **never run** | High (guards mainnet entry) | **KEEP** (but *use* it — see Q3) |
| D5 | Mainnet gate (3 conditions) | Don't let mainnet be a default | (a) — high-stakes actions need explicit confirm (E4) | none | Total | **KEEP** |
| D6 | Propose→approve tuning | Human signs off on config changes | (a) — exactly the recommended human-in-loop split (E4) | none | Med | **KEEP** |
| B5·coins | `ALLOWED_COINS=BTC,ETH,SOL` | Trade only liquid majors | (a) — direct mitigation of JELLY-class risk (E8) | none | High if expanded to illiquid | **KEEP** (treat expansion as a risk decision) |
| D1 | Agent-wallet + `0600` keystore | Trade-not-withdraw, locked perms | (a) for scoping/perms; (d) for plaintext-at-rest (E10) | none | Med (key leak → positions, not balance) | **STRENGTHEN** (encrypt at rest) |
| D4 | Decision log / audit trail | Attribution + tuner data | (a) — audit logging is best practice | none | Low direct; high strategic | **KEEP** |
| C6 | Native SL/TP + emergency close | A crashed process must not leave a naked position | (a) — sound and load-bearing for mainnet | none | **Catastrophic** on the failure path | **DANGEROUS — FIX BEFORE NEXT TRADE** (emergency-close success is unconfirmed; see D-1) |
| C7 | Rate-limit / 429 handling | *(absent)* | (a) — HL enforces address-based limits (E7) | none | High (a failed write during protection → naked position) | **DANGEROUS — FIX BEFORE NEXT TRADE** |
| B7 | Daily-loss breaker | Halt after a bad day | (a) for having one; behavior is new-fire-only | none | Med-High (open positions can breach the limit between passes) | **STRENGTHEN** (real-time portfolio bound) |
| C8 | State reconciliation | Catch exchange↔ledger drift | (a) — reconciliation is best practice, but alert-only is partial (E9) | none | High (a naked position only alerts, doesn't self-heal) | **STRENGTHEN** |
| C1 | Market-order entry | An accepted order = a filled, protected one | (a) — but pays taker+slippage; inconsistent with "wait for entry" (E5) | none | Med (expectancy drag, amplified by leverage) | **CONSTRAIN** (slippage-capped marketable-limit; reconcile prompt) |
| C2·min | `$10` min-notional check | *(absent)* | (a) — HL enforces a min notional (E7) | none | Low (silent reject, not a loss) | **STRENGTHEN** |
| A1 | LLM entry arbiter (core Mode B) | The design thesis: LLM owns judgment | (b), (d) on sizing — unstable/uncalibrated; unproven vs a rule (E1, E4) | none | Med, **bounded by the gate** | **CONSTRAIN** (A/B vs rule-based filter; prove it adds value) |
| A2 | Decision prompt (thesis-as-untrusted) | Guardrail the model's role | (a)/(b) mitigation, (d) residual sycophancy/injection (E2, E3) | none | Med | **STRENGTHEN** (injection hardening; sycophancy test) |
| B3 | conviction→size scaling | Bet more when edge is higher | (c)/(d) — LLM conviction is uncalibrated/unstable (E1, E6) | none | Low-Med (moves size within a conservative band) | **CONSTRAIN** (flat size until conviction is shown to predict R) |
| A5 | Sentry in-trade LLM manager | Judgment the trail rules can't add | (b) — same instability; higher authority (E1) | none — never run | Med-High (`add` increases risk) | **CONSTRAIN** (keep `add` off until baseline proven) |
| A6/A7 | Config / prompt / journal tuners | Self-improve from logged outcomes | (a) — out-of-path, human-approved (E4) | none | Low (out-of-path, clamped) | **KEEP** |
| B8 | Sentry management gate | Box the in-trade LLM | (a) — same role as B1 | none | High | **KEEP** |
| A4 | Enrichment context | What the model reasons over | (b) — recency/framing can bias (E1) | none | Med | **KEEP** (monitor for recency bias) |

No component earned **REMOVE**: the complexity is generally justified by the safety story. The closest call is the whole LLM layer (A1/A5), which is **CONSTRAIN, not REMOVE** — but only because it is boxed tightly enough that its cost is bounded and it can be A/B'd against a rule-based alternative behind a flag. If that A/B shows no edge over a rule, the LLM layer should be demoted to the rule.

---

## Plain-language summary — the three questions

### 1. Does the evidence support having an LLM in the order path at all, in its current form?

**Qualified yes — because of how tightly it is boxed, not because the LLM is trusted.** The literature is clear that LLM financial decisions are unstable, uncalibrated, and sycophantic toward a well-argued input (`Evidence.md` E1, E2), and that high-stakes financial calls should keep a human signing off (E4). Taken at face value, that is an argument *against* an LLM in the order path.

What rescues the design is that the LLM here is not really "in the order path" in the dangerous sense. It can do exactly three things: **skip** a setup, **act** on a setup that the deterministic gate has *independently* validated, or **nudge size within a conservative 0.25×–1.0× band of an already-conservative 0.5% risk**. It cannot pick a coin, set a stop, exceed a notional/leverage cap, or bypass the gate; malformed output is dropped, not guessed. That containment neutralizes the two documented agent-failure modes (fabricated parameters, unbounded harmful actions — E3). So the honest verdict is: **the box is sound and evidence-aligned; the LLM's judgment inside it is unproven.**

Two things must change before "yes" is unconditional: (i) the `conviction`→size scaling should be **turned off** (flat sizing) until this tool's own logs show conviction predicts realized R — the one place the design leans on an uncalibrated LLM scalar; and (ii) the LLM arbiter should be **A/B'd against a trivial rule-based filter** ("act on every setup that clears the gate," or "act if regime + RR-at-mark agree"). Everything needed for that A/B is already logged. If the LLM does not beat the rule on shadow/paper expectancy, the evidence says use the rule.

### 2. What is the single most dangerous thing in this tool today?

**The emergency-close path does not confirm the position actually closed, so a double-failure leaves a naked, unprotected, mislabeled mainnet position** (`runner.py:444 _secure` → `_fire_and_reconcile:344`). Sequence: entry fills → both native SL/TP triggers must place → if either fails, the code fires **one** emergency market close and then resolves the ledger row as `"aborted"` **regardless of whether that close succeeded**. If the emergency close also fails (a transient error, or the HL **address rate limit** the tool does not handle — E7, C7), the exchange still holds an **open, unprotected, leveraged** position while the ledger says it is closed. The only backstop is `_alert_unmanaged`, which merely alerts a human on the *next* fire-enabled pass. This directly defeats the native-SL/TP-as-mainnet-prerequisite that the whole mainnet story rests on. It is rare (needs two failures) but catastrophic when it hits — the correct blast-radius weighting for a live-money tool. **Fix before the next trade** (see `Improvement-Plan.md` D-1, D-2).

Runner-up / systemic danger: **there is zero validated expectancy.** Both DBs are empty, the graduation gate has never run, and nothing has been shown — even on paper or in shadow — to have positive expectancy. Putting real money behind LLM judgment that has *never once been measured* is the meta-risk that makes every other finding matter.

### 3. What did we build that research says was the wrong direction?

- **Sizing on an uncalibrated LLM scalar (`conviction`).** E1/E6: LLM probability-like outputs are unstable and there is no evidence they predict outcomes. Let the gate size flat until the logs prove otherwise.
- **AI-disposes-without-sign-off in autonomous mode.** E4: high-stakes financial decisions should keep a human in the approval loop; `hl exec run` / `hl agent run` fire with none. (The tuner side gets this right — copy that pattern's spirit: at minimum a per-fire confirm above a notional threshold, or a "shadow-only until graduated" hard default.)
- **Feeding the arbiter the proposer's persuasive thesis.** E2: LLMs mirror well-argued inputs. The single-turn structure helps, but the `reasoning`/`news` fields are the most likely lever for a spurious `act` — and a prompt-injection vector.
- **Market-taker entry under a "wait for a clean entry" prompt.** E5: this pays taker + slippage every time and the LLM's timing judgment can't actually improve the fill — a systematic expectancy drag and an internal inconsistency.
- **Plaintext private key at rest.** E10: unanimous best practice is encrypt-at-rest; agent-wallet scoping bounds the damage but doesn't excuse it.

What we got *right* against the research, worth stating so it isn't lost in the fixes: the deterministic first-failure gate as the real authority (E3), idempotency done textbook-correctly (E9), liquid-majors-only as a direct JELLY-class mitigation (E8), agent-wallets that can't withdraw (E10), and propose→approve human-in-the-loop tuning (E4).
