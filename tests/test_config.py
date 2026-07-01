"""
tests/test_config.py

Guards config/settings.py env-var parsing: a malformed numeric env var must
raise a clear, actionable error instead of a bare ValueError (or worse,
silently coercing to something wrong).
"""

import pytest

from config.settings import ConfigError, load_settings


def test_invalid_int_env_var_raises_clear_error(monkeypatch):
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "not-a-number")
    with pytest.raises(ConfigError, match="UPDATE_FREQUENCY_MS"):
        load_settings()


def test_invalid_float_env_var_raises_clear_error(monkeypatch):
    monkeypatch.setenv("FLICKER_WINDOW_SEC", "not-a-number")
    with pytest.raises(ConfigError, match="FLICKER_WINDOW_SEC"):
        load_settings()


def test_blank_env_var_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "")
    settings = load_settings()
    assert settings.update_frequency_ms == 1000


def test_valid_env_var_parses(monkeypatch):
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "250")
    settings = load_settings()
    assert settings.update_frequency_ms == 250
