"""Market-regime classification + a compact candle summary for the decision context.

Pure functions over a candle window — no exchange calls, no LLM. `classify` reduces
recent price action to the gate's vocabulary ("trend"/"range"), or `None` when there
isn't enough history to judge (the gate treats `None` as "unknown, skip the check").

The regime is computed in code, not asked of the LLM: a deterministic signal is more
reliable than a guessed one, and it revives the gate's otherwise-dead regime check.
`summarize` hands the model a short tail of raw OHLC so it can read the recent swing
itself when judging timing (now vs wait) and conviction.
"""

from __future__ import annotations

from hlcli.core.types import Candle

# Kaufman efficiency ratio = net move / total path length. ~1.0 is a clean directional
# move; ~0.0 is chop. At or above the threshold we call it a trend, below it a range.
# The fast candle frame the decision + sentry contexts are read at. One definition so
# the executor tail, the sentry ATR feed, and the sentry fast frame can't drift apart.
DECISION_INTERVAL = "15m"

_ER_TREND_THRESHOLD = 0.35
_MIN_CANDLES = 20  # below this the ratio is too noisy to trust → unknown (None)
_SUMMARY_TAIL = 12  # recent bars handed to the model — enough to read the swing, cheap on tokens


def classify(candles: list[Candle]) -> str | None:
    closes = [c.c for c in candles]
    if len(closes) < _MIN_CANDLES:
        return None
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path == 0:
        return "range"  # flat line — no directional efficiency
    return "trend" if (net / path) >= _ER_TREND_THRESHOLD else "range"


def summarize(candles: list[Candle]) -> list[dict] | None:
    """The most recent bars as compact OHLCV rows for the prompt + log, or None if empty.
    Volume is included: it lets the model read participation behind a move (is the swing
    that justifies the entry backed by flow, or thin?), which OHLC alone can't show."""
    if not candles:
        return None
    return [{"o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v} for c in candles[-_SUMMARY_TAIL:]]
