"""MarksFeed parses the public /info endpoint and caches within its TTL."""

import httpx
import pytest

from hlcli.exchange.marks import MarksFeed


def _feed(handler, *, sleeps: list | None = None, **kw) -> MarksFeed:
    kw.setdefault("sleep_fn", sleeps.append if sleeps is not None else (lambda _d: None))
    feed = MarksFeed("https://api.test", **kw)
    feed._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return feed


def test_all_marks_parses_to_floats():
    feed = _feed(lambda req: httpx.Response(200, json={"BTC": "60000.5", "ETH": "1500"}))
    marks = feed.all_marks()
    assert marks == {"BTC": 60000.5, "ETH": 1500.0}
    assert feed.mark("BTC") == 60000.5
    assert feed.mark("DOGE") is None


def test_persistent_5xx_reraises_after_retries():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    feed = _feed(handler, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):  # exhausts retries then re-raises, not parse garbage
        feed.all_marks()
    assert calls["n"] == 4  # 1 initial + 3 retries


def test_retries_on_429_then_succeeds():
    seq = iter([429, 429, 200])
    sleeps: list = []

    def handler(req):
        code = next(seq)
        return httpx.Response(code, json={"BTC": "60000"} if code == 200 else {"error": "rate"})

    feed = _feed(handler, sleeps=sleeps, max_retries=3)
    assert feed.all_marks() == {"BTC": 60000.0}
    assert len(sleeps) == 2  # two 429s → two backoff waits, then the 200


def test_retries_on_transport_error_then_succeeds():
    state = {"first": True}

    def handler(req):
        if state["first"]:
            state["first"] = False
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json={"BTC": "1"})

    feed = _feed(handler, max_retries=3)
    assert feed.all_marks() == {"BTC": 1.0}  # transient drop retried, not surfaced


def test_4xx_other_than_429_fails_fast():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(422, json={"error": "bad request"})

    feed = _feed(handler, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        feed.all_marks()
    assert calls["n"] == 1  # a bad request won't fix itself — no retry


def test_retry_after_header_is_honored_and_clamped():
    sleeps: list = []
    seq = iter([429, 200])

    def handler(req):
        code = next(seq)
        if code == 429:
            return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": "rate"})
        return httpx.Response(200, json={"BTC": "1"})

    feed = _feed(handler, sleeps=sleeps, max_retries=3, retry_max_delay=4.0)
    assert feed.all_marks() == {"BTC": 1.0}
    assert sleeps == [4.0]  # Retry-After (30) clamped to retry_max_delay so a read can't stall


def test_all_marks_returns_a_copy_of_the_cache():
    feed = _feed(lambda req: httpx.Response(200, json={"BTC": "1"}), ttl_seconds=100)
    feed.all_marks()["BTC"] = 999.0  # caller mutation must not poison the cache
    assert feed.all_marks() == {"BTC": 1.0}


def test_sz_decimals_from_meta_universe_fetched_once():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"universe": [
            {"name": "BTC", "szDecimals": 5}, {"name": "ETH", "szDecimals": 4},
        ]})

    feed = _feed(handler)
    assert feed.sz_decimals("BTC") == 5
    assert feed.sz_decimals("ETH") == 4
    assert feed.sz_decimals("DOGE") is None  # unknown coin
    assert calls["n"] == 1  # static metadata — one fetch per session


def test_marks_cached_within_ttl():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"BTC": "1"})

    feed = _feed(handler, ttl_seconds=100)
    feed.all_marks()
    feed.all_marks()
    assert calls["n"] == 1  # second read served from cache


def test_book_posts_l2_request():
    seen = {}

    def handler(req):
        import json
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"coin": "BTC", "levels": [[], []]})

    feed = _feed(handler)
    feed.book("BTC")
    assert seen == {"type": "l2Book", "coin": "BTC"}


def test_candles_parses_ohlcv():
    sample = [
        {"t": 1000, "T": 1900, "s": "BTC", "i": "15m", "o": "100.5", "h": "101", "l": "100", "c": "100.8", "v": "12.3", "n": 5},
        {"t": 2000, "T": 2900, "s": "BTC", "i": "15m", "o": "100.8", "h": "102", "l": "100.5", "c": "101.5", "v": "8.0", "n": 3},
    ]
    feed = _feed(lambda req: httpx.Response(200, json=sample))
    bars = feed.candles("BTC", interval="15m", lookback=2)
    assert len(bars) == 2
    assert (bars[0].o, bars[0].c) == (100.5, 100.8)
    assert bars[-1].h == 102.0


def test_candles_posts_candle_snapshot_request_with_derived_window():
    seen = {}

    def handler(req):
        import json
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=[])

    feed = _feed(handler)
    feed.candles("ETH", interval="15m", lookback=10)
    assert seen["type"] == "candleSnapshot"
    assert (seen["req"]["coin"], seen["req"]["interval"]) == ("ETH", "15m")
    assert seen["req"]["endTime"] - seen["req"]["startTime"] == 10 * 900_000  # lookback × 15m


def test_candles_rejects_unknown_interval():
    feed = _feed(lambda req: httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        feed.candles("BTC", interval="7m")  # not in _INTERVAL_MS → clear error, not a raw KeyError
