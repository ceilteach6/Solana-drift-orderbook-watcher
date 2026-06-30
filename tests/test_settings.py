"""
tests/test_settings.py

Detector thresholds double as score denominators (``count / (threshold * 2)``).
A value <= 0 doesn't make a detector "more sensitive" — it crashes the
detector (or silences a comparison guard that can never trigger again). These
tests ensure such values are rejected at config construction time, before
they can reach a detector deep inside the watcher loop.
"""

import pytest

from config.settings import Settings, load_settings


def _base_kwargs():
    return dict(
        rpc_url="https://api.mainnet-beta.solana.com",
        drift_env="mainnet",
        keypair_path="",
        markets=["SOL-PERP"],
    )


def test_default_settings_are_valid():
    load_settings()  # should not raise


@pytest.mark.parametrize(
    "field",
    [
        "repeated_min_count",
        "layering_min_levels",
        "flicker_min_events",
        "imbalance_min_levels",
    ],
)
def test_zero_or_negative_count_threshold_rejected(field):
    for bad in (0, -1):
        with pytest.raises(ValueError):
            Settings(**_base_kwargs(), **{field: bad})


@pytest.mark.parametrize(
    "field",
    [
        "spoof_min_price_move",
        "spoof_wall_ratio",
        "spoof_pull_fraction",
        "repeated_size_tolerance",
        "flicker_window_sec",
        "spoof_window_sec",
    ],
)
def test_zero_or_negative_float_threshold_rejected(field):
    for bad in (0.0, -0.5):
        with pytest.raises(ValueError):
            Settings(**_base_kwargs(), **{field: bad})


def test_update_frequency_must_be_positive():
    with pytest.raises(ValueError):
        Settings(**_base_kwargs(), update_frequency_ms=0)


def test_risk_smoothing_must_be_in_unit_interval():
    with pytest.raises(ValueError):
        Settings(**_base_kwargs(), risk_smoothing=0.0)
    with pytest.raises(ValueError):
        Settings(**_base_kwargs(), risk_smoothing=1.5)
    Settings(**_base_kwargs(), risk_smoothing=1.0)  # boundary is valid
