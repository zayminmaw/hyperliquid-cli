"""Agent-wallet private key storage (PLAN.md §8).

Keys are agent ("API") wallet keys — they can trade but not withdraw, so holding
them locally is far safer than a main-wallet key. Even so:

  - one file per account at `<data_dir>/keys/<alias>.key`, created `0600`,
  - the directory is `0700`,
  - the key is never logged and never enters the LLM decision context,
  - `accounts.db` stores only a *reference* (the alias), never the key,
  - **encrypt-at-rest** (2026-07 audit, O-1): with `HL_KEYSTORE_PASSPHRASE` set, keys
    are stored as standard eth_account V3 keystore JSON (scrypt + AES) — a copied
    keys directory or disk backup is useless without the passphrase. Unset, keys are
    plaintext hex (the pre-audit behavior); both formats load transparently.

The passphrase comes from the shell environment only and is kept off the `Caps`
object (like the Anthropic key) so it can never ride along in dumps or logs.
`eth_account` stays lazy-imported: plain storage + format validation need no
signing libs, so paper mode and the test suite still run without the extra.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from hlcli._lazy import require

# 32-byte private key as hex, with or without 0x.
_HEX_KEY = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")

_PASSPHRASE_ENV = "HL_KEYSTORE_PASSPHRASE"


def keystore_passphrase() -> str | None:
    """The encrypt-at-rest passphrase from the shell env, or None (plaintext mode).
    Deliberately not a Caps field — key material config never rides on Caps."""
    return os.environ.get(_PASSPHRASE_ENV) or None


class KeystoreError(RuntimeError):
    """A key was missing, malformed, or could not be stored/decrypted."""


class Keystore:
    def __init__(self, keys_dir: Path) -> None:
        self._dir = keys_dir

    def path_for(self, alias: str) -> Path:
        return self._dir / f"{alias}.key"

    def save(self, alias: str, private_key: str, *, passphrase: str | None = None) -> str:
        """Persist a key with locked perms. Returns the key reference (the alias).

        With a passphrase the key is written as V3 keystore JSON (encrypted at rest);
        without one it is plaintext hex. File perms are identical either way —
        encryption is a second layer, not a replacement for `0600`."""
        key = private_key.strip()
        if not _HEX_KEY.match(key):
            # Deliberately does not echo the value.
            raise KeystoreError("private key must be 32 bytes of hex (64 hex chars, optional 0x).")
        if not key.startswith("0x"):
            key = "0x" + key

        payload = key if passphrase is None else json.dumps(
            require("eth_account").Account.encrypt(key, passphrase)
        )

        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        path = self.path_for(alias)
        # Open at 0600 from creation so the key is never briefly world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        return alias

    def load(self, alias: str, *, passphrase: str | None = None) -> str:
        """The private key for `alias`. Encrypted files (V3 keystore JSON) need the
        passphrase — passed in, or from HL_KEYSTORE_PASSPHRASE; plaintext files load
        as before. Format is detected from the file, so old and new keys coexist."""
        path = self.path_for(alias)
        if not path.exists():
            raise KeystoreError(f"no key stored for account '{alias}'.")
        if path.stat().st_mode & 0o077:
            raise KeystoreError(
                f"key file {path} is readable by group/other — refusing to use it. "
                f"Fix with: chmod 600 {path}"
            )
        content = path.read_text().strip()
        if not content.startswith("{"):
            return content  # plaintext hex (pre-audit format)

        passphrase = passphrase or keystore_passphrase()
        if passphrase is None:
            raise KeystoreError(
                f"key for '{alias}' is encrypted — set {_PASSPHRASE_ENV} to unlock it."
            )
        try:
            decrypted = require("eth_account").Account.decrypt(json.loads(content), passphrase)
        except ValueError as exc:  # wrong passphrase (MAC mismatch) or corrupt keystore
            raise KeystoreError(f"could not decrypt key for '{alias}': {exc}") from None
        return "0x" + bytes(decrypted).hex()

    def delete(self, alias: str) -> None:
        self.path_for(alias).unlink(missing_ok=True)


def agent_address(private_key: str) -> str:
    """Derive the agent wallet's address from its key (for display/verification)."""
    eth_account = require("eth_account")
    return eth_account.Account.from_key(private_key.strip()).address
