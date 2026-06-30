"""
tests/test_settings.py

Validates the early-fail config checks in :func:`config.settings._validate`
and the markets-as-tuple immutability fix.
"""

import pytest

from config.settings import Settings, _validate, load_settings


def base_kwargs(**overrides):
    kw = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
    )
    kw.update(overrides)
    return kw


def test_default_settings_pass_validation():
    _validate(Settings(**base_kwargs()))  # must not raise


def test_load_settings_returns_valid_defaults(monkeypatch):
    for key in (
        "SPOOF_MIN_PRICE_MOVE", "RISK_SMOOTHING", "RISK_ALERT_THRESHOLD",
        "RISK_CLEAR_THRESHOLD", "MARKETS",
    ):
        monkeypatch.delenv(key, raising=False)
    s = load_settings()
    assert s.markets == ("SOL-PERP",)
    assert isinstance(s.markets, tuple)


def test_zero_spoof_min_price_move_rejected():
    with pytest.raises(ValueError, match="SPOOF_MIN_PRICE_MOVE"):
        _validate(Settings(**base_kwargs(spoof_min_price_move=0.0)))


def test_risk_smoothing_out_of_range_rejected():
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        _validate(Settings(**base_kwargs(risk_smoothing=0.0)))
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        _validate(Settings(**base_kwargs(risk_smoothing=1.5)))


def test_risk_clear_threshold_must_be_below_alert_threshold():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        _validate(Settings(**base_kwargs(risk_clear_threshold=0.6, risk_alert_threshold=0.6)))
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        _validate(Settings(**base_kwargs(risk_clear_threshold=0.7, risk_alert_threshold=0.6)))


def test_empty_markets_rejected():
    with pytest.raises(ValueError, match="MARKETS"):
        _validate(Settings(**base_kwargs(markets=())))


def test_orderbook_depth_and_update_frequency_rejected():
    with pytest.raises(ValueError, match="ORDERBOOK_DEPTH"):
        _validate(Settings(**base_kwargs(orderbook_depth=0)))
    with pytest.raises(ValueError, match="UPDATE_FREQUENCY_MS"):
        _validate(Settings(**base_kwargs(update_frequency_ms=1)))


def test_markets_field_is_immutable_tuple():
    s = Settings(**base_kwargs(markets=("SOL-PERP", "BTC-PERP")))
    assert isinstance(s.markets, tuple)
    with pytest.raises(AttributeError):
        s.markets = ("ETH-PERP",)
