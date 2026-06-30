"""
tests/test_config.py

Settings validation: a misconfigured risk hysteresis (clear >= alert) would
make the aggregator's "stay quiet until cleared" gate never clear while still
flapping alerts every cooldown period — catch that at startup, not 3am in
production. See src/risk/aggregator.py for the hysteresis this protects.
"""

import pytest

from config.settings import Settings


def make_settings(**overrides):
    base = dict(rpc_url="https://example.invalid", drift_env="mainnet", keypair_path="")
    base.update(overrides)
    return Settings(**base)


def test_default_settings_construct_cleanly():
    make_settings()


def test_rejects_clear_threshold_at_or_above_alert_threshold():
    with pytest.raises(ValueError):
        make_settings(risk_clear_threshold=0.6, risk_alert_threshold=0.6)
    with pytest.raises(ValueError):
        make_settings(risk_clear_threshold=0.7, risk_alert_threshold=0.6)


def test_rejects_out_of_range_smoothing():
    with pytest.raises(ValueError):
        make_settings(risk_smoothing=0.0)
    with pytest.raises(ValueError):
        make_settings(risk_smoothing=1.5)


def test_rejects_negative_cooldown():
    with pytest.raises(ValueError):
        make_settings(risk_alert_cooldown_sec=-1.0)


def test_rejects_non_positive_reconnect_failure_threshold():
    with pytest.raises(ValueError):
        make_settings(feed_reconnect_after_failures=0)


def test_rejects_non_positive_storage_circuit_breaker():
    with pytest.raises(ValueError):
        make_settings(storage_failure_circuit_breaker=0)
