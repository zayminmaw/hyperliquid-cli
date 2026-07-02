"""Account store (metadata, per-network defaults) + keystore (key handling, perms)."""

import stat

import pytest

from hlcli.accounts.keystore import Keystore, KeystoreError
from hlcli.accounts.store import Account, AccountError, AccountStore, AccountType
from hlcli.core.types import Network

_KEY = "0x" + "a" * 64


def _store(tmp_path) -> AccountStore:
    return AccountStore(tmp_path / "accounts.db")


def _acct(alias="main", network=Network.TESTNET, type=AccountType.TRADE, **kw) -> Account:
    return Account(alias=alias, address="0xabc", network=network, type=type, **kw)


def test_first_account_on_network_becomes_default(tmp_path):
    s = _store(tmp_path)
    added = s.add(_acct(key_ref="main"))
    assert added.is_default
    assert s.get_default(Network.TESTNET).alias == "main"


def test_duplicate_alias_rejected(tmp_path):
    s = _store(tmp_path)
    s.add(_acct())
    with pytest.raises(AccountError):
        s.add(_acct())


def test_default_is_per_network(tmp_path):
    s = _store(tmp_path)
    s.add(_acct(alias="t", network=Network.TESTNET))
    s.add(_acct(alias="m", network=Network.MAINNET))
    assert s.get_default(Network.TESTNET).alias == "t"
    assert s.get_default(Network.MAINNET).alias == "m"


def test_set_default_moves_flag_within_network(tmp_path):
    s = _store(tmp_path)
    s.add(_acct(alias="a"))
    s.add(_acct(alias="b"))  # a stays default
    assert s.get_default(Network.TESTNET).alias == "a"
    s.set_default("b")
    assert s.get_default(Network.TESTNET).alias == "b"


def test_resolve_prefers_explicit_alias_then_default(tmp_path):
    s = _store(tmp_path)
    s.add(_acct(alias="a"))
    assert s.resolve(None, Network.TESTNET).alias == "a"
    assert s.resolve("a", Network.TESTNET).alias == "a"
    with pytest.raises(AccountError):
        s.resolve("missing", Network.TESTNET)


def test_resolve_rejects_alias_on_the_wrong_network(tmp_path):
    # `--network mainnet --account my-testnet` must fail loudly, never silently
    # sign a mainnet action with a testnet account.
    s = _store(tmp_path)
    s.add(_acct(alias="t", network=Network.TESTNET))
    with pytest.raises(AccountError, match="testnet"):
        s.resolve("t", Network.MAINNET)


def test_remove(tmp_path):
    s = _store(tmp_path)
    s.add(_acct())
    s.remove("main")
    assert s.get("main") is None
    with pytest.raises(AccountError):
        s.remove("main")


# --- keystore ---

def test_keystore_roundtrip_and_perms(tmp_path):
    ks = Keystore(tmp_path / "keys")
    ks.save("main", _KEY)
    assert ks.load("main") == _KEY
    mode = stat.S_IMODE((tmp_path / "keys" / "main.key").stat().st_mode)
    assert mode == 0o600


def test_keystore_normalizes_missing_0x(tmp_path):
    ks = Keystore(tmp_path / "keys")
    ks.save("main", "a" * 64)
    assert ks.load("main") == _KEY


def test_keystore_rejects_bad_key(tmp_path):
    ks = Keystore(tmp_path / "keys")
    with pytest.raises(KeystoreError):
        ks.save("main", "not-a-key")


def test_keystore_load_missing(tmp_path):
    ks = Keystore(tmp_path / "keys")
    with pytest.raises(KeystoreError):
        ks.load("nope")


def test_keystore_refuses_a_loose_key_file(tmp_path):
    import os

    ks = Keystore(tmp_path / "keys")
    ks.save("main", _KEY)
    os.chmod(ks.path_for("main"), 0o644)  # e.g. restored from a backup
    with pytest.raises(KeystoreError, match="chmod 600"):
        ks.load("main")


def test_agent_address_derivation(tmp_path):
    pytest.importorskip("eth_account")
    from hlcli.accounts.keystore import agent_address

    addr = agent_address(_KEY)
    assert addr.startswith("0x") and len(addr) == 42
