"""The single place `anthropic` is imported — kept lazy on purpose.

The decision layer and both tuners need an Anthropic client, but paper mode and the
test suite must import the package with no key and no SDK installed. Importing
`anthropic` only inside this function (and injecting a fake `client` in tests) is
what keeps the top-level import path free of it.
"""

from __future__ import annotations


def make_client():
    import anthropic  # noqa: PLC0415 — lazy by design (paper + tests run without it)

    return anthropic.Anthropic()
