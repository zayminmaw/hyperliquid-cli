"""Live-response parsing — pure logic, no SDK/network needed."""

from hlcli.exchange.hyperliquid import _parse_order_response, _parse_simple


def test_resting_order():
    r = _parse_order_response({"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 42}}]}}})
    assert r.accepted and r.status == "resting" and r.order_id == "42"


def test_filled_order():
    r = _parse_order_response(
        {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 7, "totalSz": "1.0", "avgPx": "100"}}]}}}
    )
    assert r.accepted and r.status == "filled" and r.order_id == "7"


def test_per_status_error_is_not_accepted():
    r = _parse_order_response({"status": "ok", "response": {"data": {"statuses": [{"error": "tick size"}]}}})
    assert not r.accepted and "tick size" in r.message


def test_top_level_error_is_not_accepted():
    r = _parse_order_response({"status": "err", "response": "insufficient margin"})
    assert not r.accepted and r.message == "insufficient margin"


def test_simple_ok_and_err():
    assert _parse_simple({"status": "ok"}, "canceled").accepted
    bad = _parse_simple({"status": "err", "response": "no order"}, "canceled")
    assert not bad.accepted and bad.message == "no order"
