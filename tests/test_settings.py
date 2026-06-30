"""
tests/test_settings.py

Detector thresholds like FLICKER_MIN_EVENTS are used as score divisors in
src/detector/*.py — a misconfigured 0 raises ZeroDivisionError deep inside a
detector, mid-run. Settings.__post_init__ should reject that at startup
instead, with a clear, actionable error.
"""

import pytest

from config.settings import Settings


def base_kwargs(**overrides):
    kwargs = dict(rpc_url="https://x", drift_env="mainnet", keypair_path="")
    kwargs.update(overrides)
    return kwargs


def test_defaults_are_valid():
    Settings(**base_kwargs())  # must not raise


@pytest.mark.parametrize(
    "field,value",
    [
        ("flicker_min_events", 0),
        ("repeated_min_count", 0),
        ("layering_min_levels", 0),
        ("imbalance_min_levels", 0),
        ("spoof_min_price_move", 0),
        ("flicker_window_sec", 0),
        ("spoof_window_sec", -1),
        ("orderbook_depth", 0),
        ("update_frequency_ms", 0),
    ],
)
def test_zero_or_negative_threshold_rejected(field, value):
    with pytest.raises(ValueError, match="Invalid configuration"):
        Settings(**base_kwargs(**{field: value}))


def test_out_of_range_ratio_rejected():
    with pytest.raises(ValueError, match="IMBALANCE_MIN_RATIO"):
        Settings(**base_kwargs(imbalance_min_ratio=1.5))
    with pytest.raises(ValueError, match="RISK_SMOOTHING"):
        Settings(**base_kwargs(risk_smoothing=0.0))
