"""Deterministic regime classifier + compact candle summary (pure, no I/O)."""

from hlcli.core.types import Candle
from hlcli.executor.regime import classify, summarize


def _candles(closes):
    return [Candle(t=i, o=px, h=px, l=px, c=px, v=1.0) for i, px in enumerate(closes)]


def test_classify_trend_on_clean_directional_move():
    assert classify(_candles([100 + i for i in range(24)])) == "trend"


def test_classify_range_on_chop():
    assert classify(_candles([100 + (i % 2) for i in range(24)])) == "range"  # 100,101,100,...


def test_classify_none_when_too_few_candles():
    assert classify(_candles([100 + i for i in range(10)])) is None


def test_classify_range_on_flat_line():
    assert classify(_candles([100.0] * 24)) == "range"  # zero path, no efficiency


def test_summarize_returns_compact_recent_tail():
    out = summarize(_candles([100 + i for i in range(20)]))
    assert len(out) == 12  # _SUMMARY_TAIL
    assert set(out[0]) == {"o", "h", "l", "c"}
    assert out[-1]["c"] == 119


def test_summarize_none_when_empty():
    assert summarize([]) is None
