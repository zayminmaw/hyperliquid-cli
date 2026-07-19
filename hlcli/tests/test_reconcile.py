"""Order+position reconciliation (wave-2 G): the safe / requires-halt verdict."""

from hlcli.core.types import Network, OpenOrder, Position, Side
from hlcli.executor.reconcile import reconcile
from hlcli.state.store import StateStore
from hlcli.tests.test_protect import FakeLiveExchange

NOW = 1_000_000.0


def _ledger(state, coin="BTC", size=1.0):
    return state.open_trade("c1", coin, Side.LONG, 100.0, 90.0, 120.0, size, 0.8, None, NOW)


def _trigger(coin="BTC"):
    return OpenOrder(coin=coin, oid=1, side=Side.SHORT, size=1.0, price=90.0,
                     reduce_only=True, is_trigger=True)


def _pos(coin="BTC", size=1.0):
    return Position(coin=coin, side=Side.LONG, size=size, entry_price=100.0)


def test_matched_and_protected_book_is_safe(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _ledger(state)
    ex = FakeLiveExchange(Network.MAINNET, positions=[_pos()], open_orders=[_trigger()])
    r = reconcile(ex, state)
    assert r.is_safe and not r.requires_halt


def test_unexpected_position_requires_halt(tmp_path):
    state = StateStore(tmp_path / "s.db")  # ledger empty
    ex = FakeLiveExchange(Network.MAINNET, positions=[_pos(coin="ETH")], open_orders=[])
    r = reconcile(ex, state)
    assert r.requires_halt and [d.kind for d in r.divergences] == ["unexpected_position"]


def test_size_mismatch_requires_halt(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _ledger(state, size=1.0)
    ex = FakeLiveExchange(Network.MAINNET, positions=[_pos(size=0.5)], open_orders=[_trigger()])
    r = reconcile(ex, state)
    assert r.requires_halt and any(d.kind == "size_mismatch" for d in r.divergences)


def test_unprotected_live_position_requires_halt(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _ledger(state)
    ex = FakeLiveExchange(Network.MAINNET, positions=[_pos()], open_orders=[])  # no trigger
    r = reconcile(ex, state)
    assert any(d.kind == "unprotected_position" for d in r.divergences)


def test_protection_not_checked_on_paper(tmp_path):
    # Paper protects via the resolver, not native triggers — an unprotected paper position
    # is not a divergence.
    state = StateStore(tmp_path / "s.db")
    _ledger(state)
    ex = FakeLiveExchange(Network.PAPER, positions=[_pos()], open_orders=[])
    r = reconcile(ex, state)
    assert r.is_safe


def test_vanished_ledger_row_is_not_a_divergence(tmp_path):
    # Ledger open, exchange flat → the resolver books it; reconcile must not halt.
    state = StateStore(tmp_path / "s.db")
    _ledger(state)
    ex = FakeLiveExchange(Network.MAINNET, positions=[], open_orders=[])
    r = reconcile(ex, state)
    assert r.is_safe


def test_shadow_ledger_row_does_not_satisfy_a_live_position(tmp_path):
    # A shadow (hypothetical) trade claims nothing on the exchange, so a real position on
    # that coin is still unexpected.
    state = StateStore(tmp_path / "s.db")
    state.open_trade("s1", "BTC", Side.LONG, 100.0, 90.0, 120.0, 1.0, 0.8, None, NOW, shadow=True)
    ex = FakeLiveExchange(Network.MAINNET, positions=[_pos()], open_orders=[_trigger()])
    r = reconcile(ex, state)
    assert any(d.kind == "unexpected_position" for d in r.divergences)
