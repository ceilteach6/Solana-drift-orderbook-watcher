"""
tests/test_settings.py

Regression tests for config validation and immutability. These lock in fixes
for configs that previously either crashed deep into a run (ZeroDivisionError,
hysteresis that never clears) or silently broke `frozen=True` (a mutable list
field in a frozen dataclass).
"""

import os

import pytest

from config.settings import ConfigError, Settings, load_settings


def base_kwargs(**overrides):
    kwargs = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
    )
    kwargs.update(overrides)
    return kwargs


def test_markets_is_an_actual_tuple():
    s = Settings(**base_kwargs())
    assert isinstance(s.markets, tuple)
    with pytest.raises(Exception):
        s.markets.append("BTC-PERP")  # tuples have no .append; frozen holds


def test_valid_settings_pass():
    Settings(**base_kwargs()).validate()  # should not raise


def test_spoof_min_price_move_zero_rejected():
    with pytest.raises(ConfigError, match="SPOOF_MIN_PRICE_MOVE"):
        Settings(**base_kwargs(spoof_min_price_move=0.0)).validate()


def test_risk_clear_threshold_must_be_below_alert_threshold():
    with pytest.raises(ConfigError, match="RISK_CLEAR_THRESHOLD"):
        Settings(
            **base_kwargs(risk_alert_threshold=0.6, risk_clear_threshold=0.8)
        ).validate()

    with pytest.raises(ConfigError, match="RISK_CLEAR_THRESHOLD"):
        Settings(
            **base_kwargs(risk_alert_threshold=0.6, risk_clear_threshold=0.6)
        ).validate()


def test_risk_thresholds_not_checked_when_aggregation_disabled():
    # Same invalid ordering, but risk_aggregation=False means it's dead
    # config that shouldn't block startup.
    Settings(
        **base_kwargs(
            risk_aggregation=False, risk_alert_threshold=0.6, risk_clear_threshold=0.8
        )
    ).validate()


def test_empty_markets_rejected():
    with pytest.raises(ConfigError, match="MARKETS"):
        Settings(**base_kwargs(markets=())).validate()


def test_update_frequency_zero_rejected():
    with pytest.raises(ConfigError, match="UPDATE_FREQUENCY_MS"):
        Settings(**base_kwargs(update_frequency_ms=0)).validate()


def test_dashboard_port_out_of_range_rejected():
    with pytest.raises(ConfigError, match="DASHBOARD_PORT"):
        Settings(**base_kwargs(dashboard_port=70000)).validate()


def test_load_settings_from_env_validates(monkeypatch):
    monkeypatch.setenv("SPOOF_MIN_PRICE_MOVE", "0")
    with pytest.raises(ConfigError):
        load_settings()


def test_load_settings_default_env_is_valid(monkeypatch):
    for key in list(os.environ):
        if key in (
            "SPOOF_MIN_PRICE_MOVE", "RISK_CLEAR_THRESHOLD", "RISK_ALERT_THRESHOLD",
        ):
            monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert isinstance(settings.markets, tuple)
