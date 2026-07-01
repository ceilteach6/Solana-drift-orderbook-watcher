"""
tests/test_settings.py

Guards the config invariants that detector math depends on: several settings
are used as divisors in score formulas (e.g. ``count / (min_count * 2)``), so
a zero or negative value must be rejected at config load time rather than
reaching a ZeroDivisionError deep in the detector stack (including from the
periodic health-check, which would otherwise crash the whole watcher).
"""

import pytest

from config.settings import Settings

_REQUIRED = dict(rpc_url="x", drift_env="x", keypair_path="")


@pytest.mark.parametrize(
    "field",
    ["repeated_min_count", "layering_min_levels", "flicker_min_events", "spoof_min_price_move"],
)
@pytest.mark.parametrize("bad_value", [0, -1])
def test_zero_or_negative_divisor_settings_are_rejected(field, bad_value):
    with pytest.raises(ValueError):
        Settings(**_REQUIRED, **{field: bad_value})


def test_valid_settings_construct_cleanly():
    Settings(**_REQUIRED)
