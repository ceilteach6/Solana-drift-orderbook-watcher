"""
tests/test_settings.py

Config validation must fail fast and loudly on values that would otherwise
misbehave silently at runtime (see config/settings.py:_validate).
"""

import pytest

from config.settings import load_settings


def test_default_env_loads_cleanly():
    settings = load_settings()
    assert settings.markets
    assert settings.snapshot_timeout_sec > 0
    assert settings.feed_connect_retries >= 1


def test_rejects_inverted_risk_hysteresis(monkeypatch):
    monkeypatch.setenv("RISK_ALERT_THRESHOLD", "0.6")
    monkeypatch.setenv("RISK_CLEAR_THRESHOLD", "0.7")
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        load_settings()


def test_rejects_zero_update_frequency(monkeypatch):
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "0")
    with pytest.raises(ValueError, match="UPDATE_FREQUENCY_MS"):
        load_settings()


def test_rejects_zero_orderbook_depth(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_DEPTH", "0")
    with pytest.raises(ValueError, match="ORDERBOOK_DEPTH"):
        load_settings()


def test_rejects_out_of_range_risk_smoothing(monkeypatch):
    monkeypatch.setenv("RISK_SMOOTHING", "1.5")
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        load_settings()


def test_rejects_invalid_alert_format(monkeypatch):
    monkeypatch.setenv("ALERT_FORMAT", "xml")
    with pytest.raises(ValueError, match="ALERT_FORMAT"):
        load_settings()


def test_rejects_non_positive_snapshot_timeout(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_TIMEOUT_SEC", "0")
    with pytest.raises(ValueError, match="SNAPSHOT_TIMEOUT_SEC"):
        load_settings()


def test_rejects_zero_feed_connect_retries(monkeypatch):
    monkeypatch.setenv("FEED_CONNECT_RETRIES", "0")
    with pytest.raises(ValueError, match="FEED_CONNECT_RETRIES"):
        load_settings()
