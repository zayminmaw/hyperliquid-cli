"""Wire rounding: sizes floor to szDecimals; prices obey 5-sig-fig / max-decimals."""

import pytest

from hlcli.exchange.rounding import round_price, round_size


@pytest.mark.parametrize("size,decimals,expected", [
    (0.123456789, 5, 0.12345),   # floored, never rounded up
    (0.999999, 2, 0.99),
    (1.0, 3, 1.0),
    (0.0000009, 5, 0.0),         # too small to represent → zero (caller rejects)
    (5.0, 0, 5.0),               # whole-unit assets
    (5.7, 0, 5.0),
])
def test_round_size_floors(size, decimals, expected):
    assert round_size(size, decimals) == expected


@pytest.mark.parametrize("px,decimals,expected", [
    (1234.5678, 4, 1234.6),      # 5 significant figures
    (0.00123456, 2, 0.0012),     # 5 sig figs, then capped at 6−2=4 decimals
    (60000.4, 5, 60000.0),       # 5 sig figs on a big number
    (99.99999, 4, 100.0),
])
def test_round_price_examples(px, decimals, expected):
    assert round_price(px, decimals) == expected
