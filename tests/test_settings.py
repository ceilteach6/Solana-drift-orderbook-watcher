"""
tests/test_settings.py

Tests config/settings.py's startup validation: a misconfigured threshold must
fail fast with a clear error instead of crashing (or silently misbehaving)
deep inside a live run.
"""

import pytest

from config.settings import Settings


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
    Settings(**base_kwargs())  # must not raise


def test_markets_is_a_tuple_not_a_mutable_list():
    settings = Settings(**base_kwargs())
    assert isinstance(settings.markets, tuple)


def test_empty_markets_rejected():
    with pytest.raises(ValueError, match="markets"):
        Settings(**base_kwargs(markets=()))


def test_zero_spoof_min_price_move_rejected():
    # Would otherwise divide by zero inside SpoofPullDetector.analyze() every tick.
    with pytest.raises(ValueError, match="spoof_min_price_move"):
        Settings(**base_kwargs(spoof_min_price_move=0.0))


def test_inverted_risk_hysteresis_rejected():
    # clear_threshold >= alert_threshold means an elevated alert never clears.
    with pytest.raises(ValueError, match="risk_clear_threshold"):
        Settings(**base_kwargs(risk_clear_threshold=0.9, risk_alert_threshold=0.6))


def test_risk_smoothing_zero_rejected():
    # alpha=0 freezes the EMA at its initial value forever.
    with pytest.raises(ValueError, match="risk_smoothing"):
        Settings(**base_kwargs(risk_smoothing=0.0))


def test_bad_alert_format_rejected():
    with pytest.raises(ValueError, match="alert_format"):
        Settings(**base_kwargs(alert_format="xml"))


def test_dashboard_port_out_of_range_rejected():
    with pytest.raises(ValueError, match="dashboard_port"):
        Settings(**base_kwargs(dashboard_port=70000))


def test_multiple_errors_reported_together():
    with pytest.raises(ValueError) as exc_info:
        Settings(**base_kwargs(spoof_min_price_move=0.0, risk_smoothing=0.0))
    message = str(exc_info.value)
    assert "spoof_min_price_move" in message
    assert "risk_smoothing" in message
