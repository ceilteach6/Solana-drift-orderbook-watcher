"""
tests/test_settings.py

Validates that Settings fails fast on misconfiguration instead of silently
misbehaving at runtime (broken risk hysteresis, unsafe webhook targets).
"""

import pytest

from config.settings import Settings


def base_kwargs(**overrides):
    kwargs = dict(rpc_url="https://example.invalid", drift_env="mainnet", keypair_path="")
    kwargs.update(overrides)
    return kwargs


def test_default_settings_are_valid():
    Settings(**base_kwargs())  # must not raise


def test_rejects_clear_threshold_above_alert_threshold():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        Settings(**base_kwargs(risk_clear_threshold=0.7, risk_alert_threshold=0.6))


def test_rejects_clear_threshold_equal_to_alert_threshold():
    with pytest.raises(ValueError, match="RISK_CLEAR_THRESHOLD"):
        Settings(**base_kwargs(risk_clear_threshold=0.6, risk_alert_threshold=0.6))


def test_rejects_non_http_webhook_scheme():
    with pytest.raises(ValueError, match="http"):
        Settings(**base_kwargs(alert_webhook_url="file:///etc/passwd"))


def test_rejects_webhook_pointed_at_cloud_metadata_service():
    with pytest.raises(ValueError, match="private/internal"):
        Settings(**base_kwargs(alert_webhook_url="http://169.254.169.254/latest/meta-data/"))


def test_rejects_webhook_pointed_at_loopback():
    with pytest.raises(ValueError, match="private/internal"):
        Settings(**base_kwargs(alert_webhook_url="http://127.0.0.1:8080/hook"))


def test_allows_private_webhook_host_when_explicitly_opted_in():
    Settings(
        **base_kwargs(
            alert_webhook_url="http://127.0.0.1:8080/hook",
            alert_webhook_allow_private_host=True,
        )
    )  # must not raise


def test_allows_public_https_webhook():
    Settings(**base_kwargs(alert_webhook_url="https://discord.com/api/webhooks/x/y"))


def test_rejects_zero_spoof_min_price_move():
    # spoof_pull.py divides by 2 * SPOOF_MIN_PRICE_MOVE when scoring a
    # detected wall-pull; zero guarantees a ZeroDivisionError the first time
    # that code path fires.
    with pytest.raises(ValueError, match="SPOOF_MIN_PRICE_MOVE"):
        Settings(**base_kwargs(spoof_min_price_move=0))


def test_rejects_zero_risk_smoothing():
    # alpha=0 in the EMA freezes the smoothed risk score at its initial
    # value (0) forever, silently disabling risk-aggregated alerting.
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        Settings(**base_kwargs(risk_smoothing=0))


def test_rejects_zero_update_frequency():
    # UPDATE_FREQUENCY_MS=0 would turn the poll loop into a tight busy-loop.
    with pytest.raises(ValueError, match="UPDATE_FREQUENCY_MS"):
        Settings(**base_kwargs(update_frequency_ms=0))


def test_rejects_out_of_range_dashboard_port():
    with pytest.raises(ValueError, match="DASHBOARD_PORT"):
        Settings(**base_kwargs(dashboard_port=70000))


def test_rejects_invalid_alert_format():
    with pytest.raises(ValueError, match="ALERT_FORMAT"):
        Settings(**base_kwargs(alert_format="xml"))


def test_reports_multiple_invalid_fields_together():
    # Fixing config one error at a time (fix, rerun, hit the next ValueError)
    # is needlessly slow — every invalid field should be reported in one pass.
    with pytest.raises(ValueError) as excinfo:
        Settings(**base_kwargs(spoof_min_price_move=0, risk_smoothing=0))
    message = str(excinfo.value)
    assert "SPOOF_MIN_PRICE_MOVE" in message
    assert "RISK_SMOOTHING" in message
