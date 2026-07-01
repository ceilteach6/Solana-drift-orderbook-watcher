"""
tests/test_settings.py

Tests the config validation gate (``config.settings._validate``). The module
loads a singleton at import time from the real environment, so these tests
build ``Settings`` instances directly (via ``dataclasses.replace`` on the
already-loaded singleton) rather than re-invoking ``load_settings()`` with
mutated env vars, which would mean re-importing the module.
"""

import dataclasses

import pytest

from config.settings import SettingsError, _validate, settings as base_settings


def valid(**overrides):
    return dataclasses.replace(base_settings, **overrides)


def test_baseline_settings_are_valid():
    assert _validate(valid()) is not None


def test_rejects_zero_update_frequency():
    with pytest.raises(SettingsError, match="UPDATE_FREQUENCY_MS"):
        _validate(valid(update_frequency_ms=0))


def test_rejects_negative_update_frequency():
    with pytest.raises(SettingsError, match="UPDATE_FREQUENCY_MS"):
        _validate(valid(update_frequency_ms=-500))


def test_rejects_out_of_range_smoothing():
    with pytest.raises(SettingsError, match="RISK_SMOOTHING"):
        _validate(valid(risk_smoothing=2.0))
    with pytest.raises(SettingsError, match="RISK_SMOOTHING"):
        _validate(valid(risk_smoothing=0.0))


def test_rejects_inverted_risk_thresholds():
    with pytest.raises(SettingsError, match="RISK_CLEAR_THRESHOLD"):
        _validate(valid(risk_alert_threshold=0.4, risk_clear_threshold=0.6))


def test_rejects_equal_risk_thresholds():
    with pytest.raises(SettingsError, match="RISK_CLEAR_THRESHOLD"):
        _validate(valid(risk_alert_threshold=0.5, risk_clear_threshold=0.5))


def test_rejects_out_of_range_imbalance_ratio():
    with pytest.raises(SettingsError, match="IMBALANCE_MIN_RATIO"):
        _validate(valid(imbalance_min_ratio=1.5))


def test_rejects_empty_markets():
    with pytest.raises(SettingsError, match="MARKETS"):
        _validate(valid(markets=[]))


def test_rejects_invalid_dashboard_port():
    with pytest.raises(SettingsError, match="DASHBOARD_PORT"):
        _validate(valid(dashboard_port=0))
    with pytest.raises(SettingsError, match="DASHBOARD_PORT"):
        _validate(valid(dashboard_port=70000))


def test_reports_multiple_issues_at_once():
    with pytest.raises(SettingsError) as exc_info:
        _validate(valid(update_frequency_ms=0, risk_smoothing=5.0))
    message = str(exc_info.value)
    assert "UPDATE_FREQUENCY_MS" in message
    assert "RISK_SMOOTHING" in message
