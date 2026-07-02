"""ANTHROPIC_API_KEY sourcing + masking (`core/llm.py`) — fully keyless.

Only the settings class and the mask are exercised; `make_client` (the lazy
`anthropic` import) stays untouched so the suite runs without the SDK.
"""

from __future__ import annotations

from hlcli.core.llm import api_key, masked_api_key


def _isolate(monkeypatch, tmp_path):
    """No inherited key, no repo `.env`: run from an empty tmp cwd."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def test_unset_key_is_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert api_key() is None
    assert masked_api_key() is None


def test_key_from_shell_env(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    assert api_key() == "sk-ant-from-shell"


def test_key_from_dotenv_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-from-dotenv\n")
    assert api_key() == "sk-ant-from-dotenv"


def test_shell_env_wins_over_dotenv(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-from-dotenv\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    assert api_key() == "sk-ant-from-shell"


def test_mask_shows_only_first_and_last_four(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-secretmiddle-WXYZ")
    assert masked_api_key() == "sk-a…WXYZ"


def test_mask_never_reveals_a_short_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tiny-key")  # 8 chars: ends would leak it all
    assert masked_api_key() == "…"
