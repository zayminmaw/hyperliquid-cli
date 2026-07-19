"""Multi-account SQLite store (PLAN.md §8).

Holds *metadata only* — alias, the main account address being traded, network,
type (`trade` / `read-only`), and a key reference (the alias under which the
agent key is kept in the keystore). The key itself never lives here.

Aliases are globally unique (the primary key); the same wallet used on testnet and
mainnet needs two aliases (different agent approvals), each with its own default
flag per network. `resolve` refuses an alias whose network doesn't match the
requested one — a testnet account must never sign a mainnet action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hlcli.core.config import Caps
from hlcli.core.types import Network


class AccountType(StrEnum):
    TRADE = "trade"
    READ_ONLY = "read-only"


@dataclass
class Account:
    alias: str
    address: str
    network: Network
    type: AccountType
    key_ref: str | None = None
    is_default: bool = False


class AccountError(RuntimeError):
    """An account operation failed (missing, duplicate, etc.)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    alias      TEXT PRIMARY KEY,
    address    TEXT NOT NULL,
    network    TEXT NOT NULL,
    type       TEXT NOT NULL,
    key_ref    TEXT,
    is_default INTEGER NOT NULL DEFAULT 0
);
"""


class AccountStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(self, account: Account) -> Account:
        if self.get(account.alias) is not None:
            raise AccountError(f"account '{account.alias}' already exists.")
        # First account on a network becomes its default automatically.
        make_default = account.is_default or self.get_default(account.network) is None
        with self._conn:
            if make_default:
                self._clear_default(account.network)
            self._conn.execute(
                "INSERT INTO accounts(alias, address, network, type, key_ref, is_default)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (
                    account.alias,
                    account.address,
                    account.network.value,
                    account.type.value,
                    account.key_ref,
                    int(make_default),
                ),
            )
        account.is_default = make_default
        return account

    def get(self, alias: str) -> Account | None:
        row = self._conn.execute("SELECT * FROM accounts WHERE alias = ?", (alias,)).fetchone()
        return _to_account(row) if row else None

    def list(self, network: Network | None = None) -> list[Account]:
        if network is not None:
            rows = self._conn.execute(
                "SELECT * FROM accounts WHERE network = ? ORDER BY alias", (network.value,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM accounts ORDER BY network, alias").fetchall()
        return [_to_account(r) for r in rows]

    def remove(self, alias: str) -> Account:
        account = self.get(alias)
        if account is None:
            raise AccountError(f"no account '{alias}'.")
        with self._conn:
            self._conn.execute("DELETE FROM accounts WHERE alias = ?", (alias,))
        return account

    def set_address(self, alias: str, address: str) -> Account:
        """Re-point an existing account at a different main address (wave-2 P) — the alias,
        default flag, and key reference are untouched."""
        account = self.get(alias)
        if account is None:
            raise AccountError(f"no account '{alias}'.")
        with self._conn:
            self._conn.execute("UPDATE accounts SET address = ? WHERE alias = ?", (address, alias))
        account.address = address
        return account

    def set_default(self, alias: str) -> Account:
        account = self.get(alias)
        if account is None:
            raise AccountError(f"no account '{alias}'.")
        with self._conn:
            self._clear_default(account.network)
            self._conn.execute("UPDATE accounts SET is_default = 1 WHERE alias = ?", (alias,))
        account.is_default = True
        return account

    def get_default(self, network: Network) -> Account | None:
        row = self._conn.execute(
            "SELECT * FROM accounts WHERE network = ? AND is_default = 1", (network.value,)
        ).fetchone()
        return _to_account(row) if row else None

    def resolve(self, alias: str | None, network: Network) -> Account | None:
        """Explicit `--account` alias wins; otherwise the network's default. An alias
        registered for a different network is an error, not a silent cross-network use."""
        if alias is not None:
            account = self.get(alias)
            if account is None:
                raise AccountError(f"no account '{alias}'.")
            if account.network is not network:
                raise AccountError(
                    f"account '{alias}' is registered for {account.network.value}, "
                    f"not {network.value}."
                )
            return account
        return self.get_default(network)

    def _clear_default(self, network: Network) -> None:
        self._conn.execute(
            "UPDATE accounts SET is_default = 0 WHERE network = ?", (network.value,)
        )


def _to_account(row: sqlite3.Row) -> Account:
    return Account(
        alias=row["alias"],
        address=row["address"],
        network=Network(row["network"]),
        type=AccountType(row["type"]),
        key_ref=row["key_ref"],
        is_default=bool(row["is_default"]),
    )


def open_store(caps: Caps) -> AccountStore:
    return AccountStore(caps.data_dir / "accounts.db")
