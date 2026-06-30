"""
tests/test_settings.py

Several detectors divide by their threshold settings (or index into a result
that only exists once the threshold is met). A threshold of 0 or out of range
isn't "more sensitive" -- it's a ZeroDivisionError/IndexError waiting to
happen the next time that detector runs. Settings.__post_init__ should catch
this at startup instead of letting it crash mid-run.
"""

import dataclasses

import pytest

from config.settings import Settings, load_settings


def _base_kwargs():
    return dataclasses.asdict(load_settings())


@pytest.mark.parametrize(
    "field",
    [
        "repeated_min_count",
        "orderbook_depth",
        "update_frequency_ms",
        "layering_min_levels",
        "flicker_min_events",
        "imbalance_min_levels",
    ],
)
def test_rejects_non_positive_int_thresholds(field):
    kwargs = _base_kwargs()
    kwargs[field] = 0
    with pytest.raises(ValueError):
        Settings(**kwargs)


@pytest.mark.parametrize(
    "field",
    ["spoof_min_price_move", "spoof_wall_ratio", "flicker_window_sec", "spoof_window_sec"],
)
def test_rejects_non_positive_float_thresholds(field):
    kwargs = _base_kwargs()
    kwargs[field] = 0
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_rejects_clear_threshold_above_alert_threshold():
    kwargs = _base_kwargs()
    kwargs["risk_clear_threshold"] = kwargs["risk_alert_threshold"]
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_default_settings_are_valid():
    load_settings()  # must not raise
