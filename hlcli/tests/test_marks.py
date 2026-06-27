"""MarksFeed parses the public /info endpoint and caches within its TTL."""

import httpx

from hlcli.exchange.marks import MarksFeed


def _feed(handler, **kw) -> MarksFeed:
    feed = MarksFeed("https://api.test", **kw)
    feed._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return feed


def test_all_marks_parses_to_floats():
    feed = _feed(lambda req: httpx.Response(200, json={"BTC": "60000.5", "ETH": "1500"}))
    marks = feed.all_marks()
    assert marks == {"BTC": 60000.5, "ETH": 1500.0}
    assert feed.mark("BTC") == 60000.5
    assert feed.mark("DOGE") is None


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
