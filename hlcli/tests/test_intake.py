"""Intake: content-derived ids make batch re-imports idempotent; caps path anchoring."""

from pathlib import Path

import pytest

from hlcli.executor.intake import candidate_from_dict, make_candidate, parse_batch


def _item(**over):
    return {"coin": "BTC", "entry": 100.0, "tp": 120.0, "sl": 90.0, **over}


def test_batch_item_without_id_gets_a_content_id():
    a = candidate_from_dict(_item())
    b = candidate_from_dict(_item())
    assert a.id == b.id  # same content → same id → enqueue dedupes the re-import


def test_different_content_gets_different_ids():
    assert candidate_from_dict(_item()).id != candidate_from_dict(_item(entry=101.0)).id
    assert candidate_from_dict(_item()).id != candidate_from_dict(_item(reasoning="breakout")).id


def test_explicit_id_wins():
    assert candidate_from_dict(_item(id="mine")).id == "mine"


def test_cli_propose_still_gets_random_ids():
    # Re-proposing the same levels tomorrow is a new thesis, not a duplicate.
    a = make_candidate("BTC", 100.0, 120.0, 90.0)
    b = make_candidate("BTC", 100.0, 120.0, 90.0)
    assert a.id != b.id


def test_batch_parses_aliases_and_infers_side():
    from hlcli.core.types import Side

    [c] = parse_batch([{"pair": "eth", "entry": 1500, "tp": 1400, "sl": 1600, "reason": "fade"}])
    assert c.coin == "ETH" and c.side is Side.SHORT and c.reasoning == "fade"


def test_incoherent_levels_rejected_at_intake():
    with pytest.raises(ValueError):
        make_candidate("BTC", 100.0, 95.0, 90.0)  # tp below entry on a long geometry


def test_relative_config_path_anchors_to_data_dir(tmp_path):
    from hlcli.core.config import Caps

    c = Caps(data_dir=tmp_path, config_path=Path("config/active_config.json"))
    assert c.config_path == tmp_path / "config/active_config.json"
    absolute = Caps(data_dir=tmp_path, config_path=Path("/etc/hl/config.json"))
    assert absolute.config_path == Path("/etc/hl/config.json")  # explicit absolute wins
