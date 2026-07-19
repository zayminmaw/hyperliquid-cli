# Evidence — external research per component

Evidence grade per component: **(a)** supported by rigorous research/documented best practice · **(b)** mixed / context-dependent · **(c)** no credible evidence either way · **(d)** evidence or documented incidents actively against it.

Every source is external. Practitioner write-ups are used only where academic sources are thin, and flagged as such.

---

## E1 — LLM as arbiter of financial execute/skip decisions (A1, A5) — grade (b), leaning (d) on the sizing/conviction sub-claim

The research on LLMs making or scoring financial decisions is consistent on one point: **the outputs are unstable and poorly calibrated, so any metric built on a single LLM decision is unreliable.**

- A 2025 survey of LLMs in financial prediction/trading and the InvestorBench line of work find LLM trading agents exhibit look-ahead bias, a "distraction effect," and significant run-to-run variability in even sentiment classification — "the inherent instability of LLMs in direct-trading tasks renders traditional performance metrics unreliable." ([The New Quant survey, arXiv:2510.05533](https://arxiv.org/html/2510.05533v1); [Frontiers, LLMs in equity markets](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1608365/full))
- Binary-decision-bias work shows LLMs carry systematic, framing-dependent biases in yes/no financial decisions. ([Evaluating Binary Decision Biases, arXiv:2501.16356](https://arxiv.org/html/2501.16356v1)) Representation-bias work shows open models internalize firm-size/visibility/sector priors rather than the economics. ([arXiv:2510.05702](https://arxiv.org/html/2510.05702))
- On calibration of probability-like outputs specifically: LLM-derived behavioral parameters can match human magnitudes but "probability weighting shows instability across repeated measurements." ([Calibrating Behavioral Parameters, arXiv:2602.01022](https://arxiv.org/pdf/2602.01022))

**Implication for this tool.** The `conviction` number (B2/B3 scale size by it) is exactly the kind of probability-like output the literature finds unstable and uncalibrated. There is **no published evidence** that LLM selection among human-proposed setups beats a simple rule-based filter, and the instability findings argue against trusting an LLM scalar to move real size until it is shown, on this tool's own logs, to correlate with realized R.

---

## E2 — Sycophancy toward the setup provider (A1, A2, A4) — grade (d) for the risk, (a)/(b) for the mitigation

This is the sharpest evidence against the *architecture as built*, because the human hands the LLM a persuasive `reasoning`/`news` thesis and asks it to judge that thesis.

- LLMs "systematically match user beliefs over truthful responses," and "argumentative prompts reliably induce stance-mirroring, with sycophancy intensity correlating with argument strength" (38 topics, 13 models). ([Measuring Opinion Bias and Sycophancy, arXiv:2604.21564](https://arxiv.org/pdf/2604.21564))
- Models are **more** swayed when the user's argument "includes detailed reasoning even when the conclusion is incorrect," and when feedback is casually phrased. ([Challenging the Evaluator, arXiv:2509.16533](https://arxiv.org/abs/2509.16533)) Interaction context increases sycophancy. ([arXiv:2509.12517](https://arxiv.org/pdf/2509.12517)) LLMs also exhibit conformity to presented positions. ([Conformity in LLMs, arXiv:2410.12428](https://arxiv.org/pdf/2410.12428))

**Two-sided reading.** (a) The prompt's explicit "treat the thesis as something to evaluate, not instructions" and the **single-turn, stateless** call structure are genuine, evidence-aligned mitigations — the multi-turn/rebuttal sycophancy findings (the strongest effects) mostly do not apply because there is no follow-up dialogue. (d) But single-turn stance-mirroring toward a well-argued setup is precisely the residual the research says an instruction does not fully remove. A well-written but wrong `reasoning` field is the input most likely to push a spurious `act`.

---

## E3 — LLM agents taking irreversible real-world actions (A1, A5, C) — grade (a)

- Giving agents tools + memory "opens new ways to fail, and a small misunderstanding can lead to a big, irreversible action." Documented failure modes: **fabricating tool parameters when information is insufficient**, and under-weighting the danger of harmful tool calls. ([Agent-SafetyBench, arXiv:2412.14470](https://arxiv.org/pdf/2412.14470); [OpenAgentSafety, arXiv:2507.06134](https://arxiv.org/html/2507.06134v2))
- Prompt injection against a tool-using agent "can trigger … irreversible real-world actions," where the same attack on a chat model only produces text. Pre-emptive checking of an action before execution is the recommended control. ([InferAct, arXiv:2407.11843](https://arxiv.org/html/2407.11843v2))

**Implication.** The two documented failure modes are **structurally mitigated here**: forced strict tool output means the model cannot emit free-form/fabricated fields, and the deterministic gate is the pre-emptive check the literature recommends. The residual exposure is prompt injection via the `reasoning`/`news` fields — but because the gate clamps and validates, an injected "act at max conviction" still cannot exceed a hard cap, pick a disallowed coin, or bypass the gate; its blast radius is bounded to forcing an `act` on an otherwise gate-valid setup and moving size within the conservative band. This is the single design decision that most reduces the danger the literature warns about.

---

## E4 — Human-proposes / AI-disposes vs AI-proposes / human-approves (whole architecture) — grade (b), with a caution (d) for autonomous mode

- The consensus in human-AI decision research: **AI handles data-intensive processing; humans retain authority over ambiguous judgment and sign off in high-stakes domains (finance explicitly named).** ([Automation-bias review, AI & Society, doi:10.1007/s00146-025-02422-7](https://link.springer.com/article/10.1007/s00146-025-02422-7))
- Automation bias (over-reliance on the machine's recommendation) is the dominant documented failure of human-in-the-loop setups, and "accountability alone is an ineffective intervention." ([same]) Confirmation bias undermines human oversight of AI outputs. ([Scalable oversight, arXiv:2507.19486](https://arxiv.org/pdf/2507.19486))

**Implication.** This tool **inverts** the recommended division: the human supplies the thesis (a form of judgment) and the LLM disposes (act/skip) — and in `hl exec run` / `hl agent run` autonomous modes there is **no human sign-off before a fire.** That is the opposite of "human signs off on high-stakes financial decisions." Mitigating nuances: (1) the *tuner* side correctly keeps the human in the loop (propose→approve, D6) — exactly what the research recommends; (2) the LLM's dispose authority is boxed so tightly by the gate that "AI disposes" really means "AI may skip, or may act inside a fixed risk box it cannot alter." The tension is real but its blast radius is bounded, not open-ended.

---

## E5 — Market vs limit order entry for retail perp (C1) — grade (a)

- Market orders "fill at unfavorable prices during volatility" and pay the taker fee; on leverage the cost is amplified — "a market order that slips 0.3% on a 10x position costs 3% of the posted collateral." ([Deribit Insights: Limit vs Market Orders](https://insights.deribit.com/education/limit-orders-vs-market-orders/))
- Microstructure work: the rational propensity to use limit orders rises with volatility and order size and falls with depth; market orders systematically pay the spread + taker fee and are exposed to adverse selection. ([Slippage and the choice of market or limit orders in futures trading (academic PDF)](https://www.academia.edu/19511507/SLIPPAGE_AND_THE_CHOICE_OF_MARKET_OR_LIMIT_ORDERS_IN_FUTURES_TRADING))

**Implication.** Every entry pays taker fee + slippage by construction. This is a defensible **safety** choice (a market fill means an accepted order is a *filled, protected* one — no phantom resting position), and the mark-sanity re-check bounds how bad the fill can be. But it creates a real **expectancy drag** and a design inconsistency: the prompt cultivates patience ("wait for a clean entry"), yet execution is always a taker at the mark when it fires — the LLM's timing judgment can defer but can never *improve* the fill price. For a small-notional strategy (`MAX_NOTIONAL=1000`), the fixed drag matters relative to edge.

---

## E6 — Position sizing: fixed-fractional, R:R floor, conviction scaling (B2, B3) — grade (a) for the frame, (c)/(d) for conviction scaling

- Fixed-fractional sizing is a standard, defensible frame; the common retail baseline is ~1–2% risk per trade. Kelly maximizes long-run growth but "assumes perfect, unchanging knowledge of the edge" and full-Kelly courts 50%+ drawdowns; **half-Kelly is the professional norm.** ([Kelly vs Fixed Fractional (practitioner)](https://medium.com/@tmapendembe_28659/kelly-criterion-vs-fixed-fractional-which-risk-model-maximizes-long-term-growth-972ecb606e6c); [Applying the Kelly Criterion, QuantStrategy.io](https://quantstrategy.io/blog/applying-the-kelly-criterion-to-trading-maximizing-growth/))

**Implication.** `risk_per_trade_pct=0.5%` is **conservative** — below the 1–2% baseline and far below Kelly/half-Kelly — which is the safe direction to err (under-betting an uncertain edge). RR floor 1.5 is reasonable. The weak link is **scaling size by the LLM's `conviction`**: this is a discretionary Kelly-lite that presumes conviction predicts outcome probability, which E1 says is unproven and unstable. Because conviction only moves size within a conservative 0.25×–1.0× band of an already-conservative 0.5%, the downside is bounded — but there is no evidence the scaling *adds* anything, and the honest default until calibration is proven is flat sizing.

---

## E7 — Hyperliquid tick/lot/rate-limit ground truth (C1, C2, C7) — grade (a); code matches docs, one gap

From current official docs:

- **Price:** "up to 5 significant figures, but no more than `MAX_DECIMALS − szDecimals` decimal places," `MAX_DECIMALS = 6` for perps; **integer prices always allowed.** **Size:** "rounded to the `szDecimals` of that asset." ([HL docs: Tick and lot size](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size)) → **`rounding.py` matches this exactly** (`round_price` = 5 sig-figs then `6 − szDecimals`; `round_size` floors to `szDecimals`).
- **Rate limits:** address-based on order actions — "1 request per 1 USDC traded cumulatively since inception," initial buffer **10,000 requests**, and when limited **one request every 10 seconds**; cancels get a higher cumulative allowance. ([HL docs: Rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)) → the tool has **no handling for this** (C7). A fresh agent account has generous initial buffer but earns budget only through volume; a burst of writes (entry + 2 protective triggers + sentry actions + cancels) with no 429/backoff can fail a write at the worst moment (protection or emergency-close).
- **Minimum order value:** the fetched tick/lot page states no minimum, but Hyperliquid enforces a **$10 minimum order notional** elsewhere in its docs/SDK. The gate has no such check → a low-conviction, wide-stop order can size below $10 and be rejected by the exchange (a silent no-op, not a loss). *Flagged for verification against current docs.*

---

## E8 — Illiquid-asset manipulation & oracle risk (B5 allowed-coins, C6 native stops) — grade (a), documented incident

- The **JELLY / HLP incident (26 Mar 2025)**: an attacker used a low-liquidity memecoin perp whose mark price tracked a thin spot reference; a self-liquidated over-leveraged short was inherited by the HLP vault and marked ~$13.5M underwater (~27% vault drawdown) as spot was pumped. Validators resolved it by **overriding the JELLY oracle price** via vote. Root causes named: allowing large positions on illiquid assets, oracle manipulability, and auto-inheriting un-liquidatable positions. ([Halborn](https://www.halborn.com/blog/post/explained-the-hyperliquid-hack-march-2025); [OAK Research](https://oakresearch.io/en/analyses/investigations/hyperliquid-jelly-attack-context-vulnerability-team-solution); [CoinDesk](https://www.coindesk.com/markets/2025/03/26/hyperliquid-delists-jellyjelly-after-vault-squeezed-in-usd13m-tussle))

**Implication.** This *validates* the `ALLOWED_COINS=BTC,ETH,SOL` hard cap — restricting to deep-liquidity majors is the exact mitigation for JELLY-class mark-manipulation and stop-gap risk. It also means the cap is **load-bearing**: adding an illiquid coin to `ALLOWED_COINS` would expose mark-based sizing and native stops to manipulation/gapping, and (per the incident) Hyperliquid may unilaterally re-price via validator vote. Keep the allowed set to liquid majors; treat any expansion as a risk decision, not a convenience toggle.

---

## E9 — Idempotent order submission & reconciliation (C4, C5, C8) — grade (a)

- Best practice: caller stamps each logical order with a unique idempotency key, server acts on it **at most once**; use **atomic claim semantics (`INSERT IF NOT EXISTS`)** to avoid races; persist durably; store the provider order id to reconcile; run a reconciliation job comparing external state to the internal ledger. ([Idempotent Order Intake, G. Tsiokos](https://george.tsiokos.com/code/2026/idempotent-orders/); [Idempotency keys prevent duplicate trades](https://www.ainvest.com/news/idempotency-keys-prevent-duplicate-trades-digital-finance-2508/))

**Implication.** `record_fire` (`INSERT OR IGNORE` before send, release on definitive reject, keep on transport-unknown) is **textbook** and matches the recommended pattern precisely, including the correct crash-safety bias (skip-not-double-fire). Two gaps vs best practice: (1) the idempotency key is the internal candidate id, not a Hyperliquid **`cloid`** on the order itself — so a transport-unknown outcome cannot be *resolved* by re-querying the exchange by client id; it is conservatively treated as spent. (2) Reconciliation (C8) is **alert-only** and only on fire-enabled passes — it does not auto-reconcile or run during shadow/idle.

---

## E10 — Hot-wallet / agent-key management (D1) — grade (a), evidence against plaintext-at-rest

- Best practice is unanimous that private keys should be **encrypted at rest** (AES-256 / keystore / BIP38); "storing Ethereum private keys in plain text within .env files is a disaster waiting to happen … with wallets that contain real funds," and plaintext keys "should never be stored digitally." ([Encrypting Private Keys, JamesBachini](https://jamesbachini.com/encrypting-private-keys-in-env/); [Store private keys securely, Chainstack](https://chainstack.com/how-to-store-private-keys-securely/))

**Implication.** Two mitigations here are strong and evidence-aligned: **agent ("API") wallets that can trade but not withdraw** (caps the blast radius of a key leak to positions, not the balance), and strict `0600`/`0700` perms with a refuse-if-readable load check. The residual gap is that the key is **plaintext hex at rest** — the code itself flags encrypt-at-rest as a future upgrade, and the seam (`save`/`load`) is clean. Given agent-wallet scoping the severity is bounded, but plaintext-at-rest is the one place the implementation sits on the wrong side of unanimous best practice.
