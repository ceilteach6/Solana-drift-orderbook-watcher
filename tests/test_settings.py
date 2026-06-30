"""
tests/test_settings.py

Unit tests for the Settings fail-fast validation (config/settings.py). The
goal is to catch a misconfigured .env at startup with a clear error instead
of letting it surface later as a busy-loop, a flapping alert, or a crash.
"""

import pytest

from config.settings import Settings, load_settings


def base_kwargs(**overrides):
    kwargs = dict(
        rpc_url="https://example.com",
        drift_env="mainnet",
        keypair_path="",
        markets=["SOL-PERP"],
    )
    kwargs.update(overrides)
    return kwargs


def test_default_settings_are_valid():
    assert load_settings() is not None


def test_settings_accepts_explicit_valid_overrides():
    Settings(**base_kwargs())  # should not raise


def test_rejects_empty_markets():
    with pytest.raises(ValueError, match="MARKETS"):
        Settings(**base_kwargs(markets=[]))


def test_rejects_zero_update_frequency():
    # UPDATE_FREQUENCY_MS=0 would turn the watcher's poll loop into a tight,
    # RPC-hammering busy loop (asyncio.sleep(0)) instead of failing loudly.
    with pytest.raises(ValueError, match="UPDATE_FREQUENCY_MS"):
        Settings(**base_kwargs(update_frequency_ms=0))


def test_rejects_negative_orderbook_depth():
    with pytest.raises(ValueError, match="ORDERBOOK_DEPTH"):
        Settings(**base_kwargs(orderbook_depth=-1))


def test_rejects_risk_clear_threshold_at_or_above_alert_threshold():
    # Without a hysteresis gap, an elevated risk state could never clear.
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        Settings(**base_kwargs(risk_clear_threshold=0.6, risk_alert_threshold=0.6))


def test_risk_threshold_check_skipped_when_aggregation_disabled():
    Settings(**base_kwargs(
        risk_aggregation=False, risk_clear_threshold=0.9, risk_alert_threshold=0.1,
    ))  # should not raise — the gate is unused in raw mode


def test_rejects_invalid_alert_format():
    with pytest.raises(ValueError, match="ALERT_FORMAT"):
        Settings(**base_kwargs(alert_format="xml"))


def test_rejects_out_of_range_dashboard_port():
    with pytest.raises(ValueError, match="DASHBOARD_PORT"):
        Settings(**base_kwargs(dashboard_port=70000))


def test_rejects_negative_run_duration():
    with pytest.raises(ValueError, match="RUN_DURATION_SEC"):
        Settings(**base_kwargs(run_duration_sec=-5))


def test_collects_multiple_errors_in_one_message():
    with pytest.raises(ValueError) as exc_info:
        Settings(**base_kwargs(orderbook_depth=0, update_frequency_ms=0))
    message = str(exc_info.value)
    assert "ORDERBOOK_DEPTH" in message
    assert "UPDATE_FREQUENCY_MS" in message
