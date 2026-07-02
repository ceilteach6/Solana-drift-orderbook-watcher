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


@pytest.mark.parametrize(
    "field",
    ["repeated_min_count", "layering_min_levels", "flicker_min_events", "spoof_min_price_move"],
)
@pytest.mark.parametrize("bad_value", [0, -1])
def test_rejects_non_positive_detector_thresholds(field, bad_value):
    # Regression: these thresholds are used as divisors when a detector
    # scores a finding — 0 or negative silently turned every tick into a
    # ZeroDivisionError inside that detector instead of failing at startup.
    with pytest.raises(ValueError, match="must be > 0"):
        Settings(**base_kwargs(**{field: bad_value}))
