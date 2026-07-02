"""Agent-wallet private key storage (PLAN.md §8).

Keys are agent ("API") wallet keys — they can trade but not withdraw, so holding
them locally is far safer than a main-wallet key. Even so:

  - one file per account at `<data_dir>/keys/<alias>.key`, created `0600`,
  - the directory is `0700`,
  - the key is never logged and never enters the LLM decision context,
  - `accounts.db` stores only a *reference* (the alias), never the key.

Encrypt-at-rest is a clean later upgrade (PLAN.md §8) — the seam is `save`/`load`.
`eth_account` is only needed to *derive* an address from a key, so it stays
lazy-imported; plain storage + format validation need no signing libs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from hlcli._lazy import require

# 32-byte private key as hex, with or without 0x.
_HEX_KEY = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


class KeystoreError(RuntimeError):
    """A key was missing, malformed, or could not be stored."""


class Keystore:
    def __init__(self, keys_dir: Path) -> None:
        self._dir = keys_dir

    def path_for(self, alias: str) -> Path:
        return self._dir / f"{alias}.key"

    def save(self, alias: str, private_key: str) -> str:
        """Persist a key with locked perms. Returns the key reference (the alias)."""
        key = private_key.strip()
        if not _HEX_KEY.match(key):
            # Deliberately does not echo the value.
            raise KeystoreError("private key must be 32 bytes of hex (64 hex chars, optional 0x).")
        if not key.startswith("0x"):
            key = "0x" + key

        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        path = self.path_for(alias)
        # Open at 0600 from creation so the key is never briefly world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key)
        return alias

    def load(self, alias: str) -> str:
        path = self.path_for(alias)
        if not path.exists():
            raise KeystoreError(f"no key stored for account '{alias}'.")
        if path.stat().st_mode & 0o077:
            raise KeystoreError(
                f"key file {path} is readable by group/other — refusing to use it. "
                f"Fix with: chmod 600 {path}"
            )
        return path.read_text().strip()

    def delete(self, alias: str) -> None:
        self.path_for(alias).unlink(missing_ok=True)


def agent_address(private_key: str) -> str:
    """Derive the agent wallet's address from its key (for display/verification)."""
    eth_account = require("eth_account")
    return eth_account.Account.from_key(private_key.strip()).address
