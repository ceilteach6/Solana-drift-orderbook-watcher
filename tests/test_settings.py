"""
tests/test_settings.py

Tests for ``Settings.validate()``: bad configuration must fail fast at
startup with a clear message, instead of crashing deep into a run (e.g. a
``ZeroDivisionError`` in spoof_pull) or silently misbehaving (e.g. a risk
alert that can never clear). Also guards the ``markets`` immutability fix.
"""

import os

import pytest

from config.settings import Settings, load_settings


def base_kwargs(**overrides):
    kwargs = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
    )
    kwargs.update(overrides)
    return kwargs


def test_default_settings_are_valid():
    Settings(**base_kwargs()).validate()  # must not raise


def test_markets_field_is_immutable_tuple():
    settings = Settings(**base_kwargs())
    assert isinstance(settings.markets, tuple)
    with pytest.raises(AttributeError):
        settings.markets.append("HACK")  # tuples have no .append


def test_empty_markets_rejected():
    with pytest.raises(ValueError, match="MARKETS"):
        Settings(**base_kwargs(markets=())).validate()


def test_zero_spoof_min_price_move_rejected():
    # A guaranteed ZeroDivisionError in spoof_pull.py if this slipped through.
    with pytest.raises(ValueError, match="SPOOF_MIN_PRICE_MOVE"):
        Settings(**base_kwargs(spoof_min_price_move=0.0)).validate()


def test_risk_clear_threshold_must_be_below_alert_threshold():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        Settings(
            **base_kwargs(risk_clear_threshold=0.9, risk_alert_threshold=0.6)
        ).validate()


def test_risk_clear_threshold_equal_to_alert_threshold_rejected():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        Settings(
            **base_kwargs(risk_clear_threshold=0.6, risk_alert_threshold=0.6)
        ).validate()


def test_storage_enabled_without_db_path_rejected():
    with pytest.raises(ValueError, match="DB_PATH"):
        Settings(**base_kwargs(storage_enabled=True, db_path="")).validate()


def test_multiple_errors_are_all_reported():
    with pytest.raises(ValueError) as exc_info:
        Settings(
            **base_kwargs(markets=(), spoof_min_price_move=0.0)
        ).validate()
    message = str(exc_info.value)
    assert "MARKETS" in message
    assert "SPOOF_MIN_PRICE_MOVE" in message


def test_load_settings_raises_on_bad_env(monkeypatch):
    monkeypatch.setenv("RPC_URL", "https://example.invalid")
    monkeypatch.setenv("SPOOF_MIN_PRICE_MOVE", "0")
    with pytest.raises(ValueError, match="SPOOF_MIN_PRICE_MOVE"):
        load_settings()


def test_load_settings_succeeds_on_clean_env(monkeypatch):
    for key in list(os.environ):
        if key in (
            "SPOOF_MIN_PRICE_MOVE",
            "RISK_CLEAR_THRESHOLD",
            "RISK_ALERT_THRESHOLD",
            "MARKETS",
        ):
            monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert isinstance(settings.markets, tuple)
    assert settings.markets
