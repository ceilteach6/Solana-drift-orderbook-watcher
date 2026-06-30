"""
tests/test_settings.py

Unit tests for config/settings.py's env parsing: malformed numeric env vars
must raise a clear, actionable ConfigError (naming the offending variable)
instead of a bare ValueError, and valid env vars must parse correctly.
"""

import pytest

from config.settings import ConfigError, load_settings


def test_malformed_int_env_var_raises_config_error(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_DEPTH", "not-an-int")
    with pytest.raises(ConfigError, match="ORDERBOOK_DEPTH"):
        load_settings()


def test_malformed_float_env_var_raises_config_error(monkeypatch):
    monkeypatch.setenv("SPOOF_WALL_RATIO", "5x")
    with pytest.raises(ConfigError, match="SPOOF_WALL_RATIO"):
        load_settings()


def test_valid_numeric_env_vars_parse(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_DEPTH", "42")
    monkeypatch.setenv("SPOOF_WALL_RATIO", "7.5")
    settings = load_settings()
    assert settings.orderbook_depth == 42
    assert settings.spoof_wall_ratio == 7.5


def test_empty_env_var_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_DEPTH", "")
    settings = load_settings()
    assert settings.orderbook_depth == 20
