"""The single place `anthropic` is imported — kept lazy on purpose.

The decision layer and both tuners need an Anthropic client, but paper mode and the
test suite must import the package with no key and no SDK installed. Importing
`anthropic` only inside this function (and injecting a fake `client` in tests) is
what keeps the top-level import path free of it.

The API key comes from the shell environment or `.env` (shell wins). It is kept off
the `Caps` object so it can never ride along when caps are dumped or logged;
`masked_api_key()` is the only display-safe form.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class _LLMEnv(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str | None = None


def api_key() -> str | None:
    """The Anthropic API key from the shell env or `.env`, or None if unset."""
    return _LLMEnv().anthropic_api_key


def masked_api_key() -> str | None:
    """Display-safe key: first 4 + last 4 chars. None if unset."""
    key = api_key()
    if key is None:
        return None
    if len(key) <= 8:  # too short for ends to be safe to reveal
        return "…"
    return f"{key[:4]}…{key[-4:]}"


def make_client():
    import anthropic  # noqa: PLC0415 — lazy by design (paper + tests run without it)

    return anthropic.Anthropic(api_key=api_key())


# Model families that reject sampling params (temperature/top_p/top_k) with a 400.
# Sonnet 5 rejects *non-default* values, which a tunable temperature would be.
_NO_SAMPLING_PARAMS = ("claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-5", "claude-fable", "claude-mythos")


def supports_temperature(model: str) -> bool:
    """Whether `model` accepts an explicit temperature — env-overridable model names
    make this a runtime question, not a constant."""
    return not model.startswith(_NO_SAMPLING_PARAMS)
