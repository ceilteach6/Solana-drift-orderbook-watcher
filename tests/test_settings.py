"""
tests/test_settings.py

Guards the fail-fast config validation in ``config/settings.py``: a
misconfigured threshold (e.g. ``FLICKER_MIN_EVENTS=0``) must raise a clear
``ValueError`` at startup, never reach the detectors, and never silently
divide by zero mid-run.
"""

import pytest

from config.settings import load_settings


def test_default_settings_load_without_error():
    settings = load_settings()
    assert settings.markets


@pytest.mark.parametrize(
    "env_name, env_value",
    [
        ("REPEATED_MIN_COUNT", "0"),
        ("LAYERING_MIN_LEVELS", "0"),
        ("FLICKER_MIN_EVENTS", "0"),
        ("FLICKER_WINDOW_SEC", "0"),
        ("IMBALANCE_MIN_LEVELS", "-1"),
        ("SPOOF_WINDOW_SEC", "0"),
        ("ORDERBOOK_DEPTH", "0"),
        ("UPDATE_FREQUENCY_MS", "0"),
        ("RISK_SMOOTHING", "0"),
        ("RISK_SMOOTHING", "1.5"),
        ("ALERT_FORMAT", "xml"),
    ],
)
def test_invalid_threshold_raises_at_load_time(monkeypatch, env_name, env_value):
    monkeypatch.setenv(env_name, env_value)
    with pytest.raises(ValueError):
        load_settings()


def test_clear_threshold_must_be_below_alert_threshold(monkeypatch):
    monkeypatch.setenv("RISK_CLEAR_THRESHOLD", "0.7")
    monkeypatch.setenv("RISK_ALERT_THRESHOLD", "0.6")
    with pytest.raises(ValueError):
        load_settings()
