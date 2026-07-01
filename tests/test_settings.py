"""
tests/test_settings.py

Guards the config validation added to ``Settings.__post_init__``: several
detector thresholds are used as divisors downstream (repeated-size, layering,
flicker, spoof-pull), so a zero/negative value must fail loudly at startup
instead of raising ``ZeroDivisionError`` deep inside a detector on every tick.
"""

import pytest

from config.settings import ConfigError, Settings, load_settings


def _base(**overrides):
    kwargs = dict(rpc_url="https://example.invalid", drift_env="mainnet", keypair_path="")
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_default_settings_are_valid():
    assert load_settings() is not None  # would raise ConfigError if invalid


@pytest.mark.parametrize(
    "field",
    ["repeated_min_count", "layering_min_levels", "flicker_min_events"],
)
@pytest.mark.parametrize("value", [0, -1])
def test_zero_or_negative_count_threshold_rejected(field, value):
    with pytest.raises(ConfigError):
        _base(**{field: value})


@pytest.mark.parametrize("value", [0, -0.001])
def test_zero_or_negative_spoof_min_price_move_rejected(value):
    with pytest.raises(ConfigError):
        _base(spoof_min_price_move=value)


@pytest.mark.parametrize("field,value", [
    ("orderbook_depth", 0),
    ("update_frequency_ms", 0),
])
def test_zero_feed_parameters_rejected(field, value):
    with pytest.raises(ConfigError):
        _base(**{field: value})


@pytest.mark.parametrize("value", [0, 1.5, -0.1])
def test_risk_smoothing_out_of_range_rejected(value):
    with pytest.raises(ConfigError):
        _base(risk_smoothing=value)


def test_valid_overrides_are_accepted():
    settings = _base(repeated_min_count=1, flicker_min_events=1, spoof_min_price_move=0.0001)
    assert settings.repeated_min_count == 1
