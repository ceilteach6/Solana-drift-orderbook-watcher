"""
tests/test_settings.py

Tests for Settings.validate(): config errors must fail fast at startup
instead of crashing deep into a run (e.g. a ZeroDivisionError in
spoof_pull.py, or a risk state that can never clear).
"""

import pytest

from config.settings import Settings


def make_settings(**overrides):
    base = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
    )
    base.update(overrides)
    return Settings(**base)


def test_default_settings_are_valid():
    make_settings().validate()  # must not raise


def test_markets_is_a_tuple_and_immutable():
    settings = make_settings()
    assert isinstance(settings.markets, tuple)
    with pytest.raises(AttributeError):
        settings.markets = ("BTC-PERP",)


def test_empty_markets_rejected():
    with pytest.raises(ValueError, match="MARKETS"):
        make_settings(markets=()).validate()


def test_zero_spoof_min_price_move_rejected():
    with pytest.raises(ValueError, match="SPOOF_MIN_PRICE_MOVE"):
        make_settings(spoof_min_price_move=0.0).validate()


def test_risk_clear_threshold_must_be_below_alert_threshold():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        make_settings(risk_clear_threshold=0.8, risk_alert_threshold=0.6).validate()


def test_risk_thresholds_equal_rejected():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        make_settings(risk_clear_threshold=0.6, risk_alert_threshold=0.6).validate()


def test_risk_validation_skipped_when_aggregation_disabled():
    # These would be invalid if risk_aggregation were on.
    make_settings(
        risk_aggregation=False, risk_clear_threshold=0.9, risk_alert_threshold=0.6,
    ).validate()


def test_bad_dashboard_port_rejected():
    with pytest.raises(ValueError, match="DASHBOARD_PORT"):
        make_settings(dashboard_port=0).validate()


def test_bad_alert_format_rejected():
    with pytest.raises(ValueError, match="ALERT_FORMAT"):
        make_settings(alert_format="xml").validate()


def test_multiple_errors_all_reported():
    with pytest.raises(ValueError) as exc_info:
        make_settings(markets=(), spoof_min_price_move=0.0).validate()
    message = str(exc_info.value)
    assert "MARKETS" in message
    assert "SPOOF_MIN_PRICE_MOVE" in message
