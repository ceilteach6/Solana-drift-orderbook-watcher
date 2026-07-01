"""
tests/test_settings.py

Covers the env-var boolean parsing and startup config validation added to
``config/settings.py``: every boolean flag must resolve unrecognized values
the same way (fall back to its default, not silently flip), and clearly
invalid numeric config must fail fast at load time rather than degrade
silently while the watcher runs unattended.
"""

import pytest

from config.settings import load_settings

_BOOL_ENV_VARS = [
    ("RISK_AGGREGATION", True),
    ("HEALTHCHECK_ENABLED", False),
    ("STORAGE_ENABLED", False),
    ("PERSIST_SNAPSHOTS", False),
]


@pytest.mark.parametrize("env_var,default", _BOOL_ENV_VARS)
def test_unrecognized_bool_value_falls_back_to_default(monkeypatch, env_var, default):
    monkeypatch.setenv(env_var, "disabled")  # typo-like, not a real true/false spelling
    settings = load_settings()
    assert getattr(settings, env_var.lower()) is default


@pytest.mark.parametrize("env_var,default", _BOOL_ENV_VARS)
def test_true_and_false_spellings_are_consistent_across_flags(monkeypatch, env_var, default):
    monkeypatch.setenv(env_var, "true")
    assert getattr(load_settings(), env_var.lower()) is True

    monkeypatch.setenv(env_var, "false")
    assert getattr(load_settings(), env_var.lower()) is False


def test_risk_smoothing_out_of_range_is_rejected(monkeypatch):
    monkeypatch.setenv("RISK_SMOOTHING", "1.5")
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        load_settings()


def test_risk_smoothing_zero_is_rejected(monkeypatch):
    # alpha=0 would freeze the smoothed risk score at its initial value
    # forever (see src/risk/aggregator.py) — silently dead, not just noisy.
    monkeypatch.setenv("RISK_SMOOTHING", "0")
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        load_settings()


def test_risk_clear_threshold_must_be_below_alert_threshold(monkeypatch):
    monkeypatch.setenv("RISK_ALERT_THRESHOLD", "0.4")
    monkeypatch.setenv("RISK_CLEAR_THRESHOLD", "0.6")
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        load_settings()


def test_non_positive_update_frequency_is_rejected(monkeypatch):
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "0")
    with pytest.raises(ValueError, match="UPDATE_FREQUENCY_MS"):
        load_settings()


def test_non_positive_orderbook_depth_is_rejected(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_DEPTH", "-1")
    with pytest.raises(ValueError, match="ORDERBOOK_DEPTH"):
        load_settings()


def test_healthcheck_interval_must_be_positive_when_enabled(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_ENABLED", "true")
    monkeypatch.setenv("HEALTHCHECK_INTERVAL_SEC", "0")
    with pytest.raises(ValueError, match="HEALTHCHECK_INTERVAL_SEC"):
        load_settings()


def test_default_settings_are_valid():
    load_settings()  # no env overrides -> must not raise
