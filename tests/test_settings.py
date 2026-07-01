"""
tests/test_settings.py

Config validation: a bad `.env` value should fail fast with a clear
`ConfigError` at startup, instead of silently disabling a detector (e.g.
IMBALANCE_MIN_RATIO > 1) or crashing it later at runtime (e.g.
SPOOF_MIN_PRICE_MOVE == 0).
"""

import pytest

from config.settings import ConfigError, Settings


def make_settings(**overrides):
    base = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=["SOL-PERP"],
    )
    base.update(overrides)
    return Settings(**base)


def test_defaults_are_valid():
    make_settings()  # should not raise


@pytest.mark.parametrize(
    "overrides",
    [
        {"markets": []},
        {"imbalance_min_ratio": 1.5},
        {"imbalance_min_ratio": 0.0},
        {"spoof_min_price_move": 0.0},
        {"spoof_min_price_move": -1.0},
        {"spoof_wall_ratio": 1.0},
        {"risk_smoothing": 0.0},
        {"risk_smoothing": 1.5},
        {"risk_clear_threshold": 0.6, "risk_alert_threshold": 0.6},
        {"risk_clear_threshold": 0.7, "risk_alert_threshold": 0.6},
        {"repeated_min_count": 1},
        {"orderbook_depth": 0},
        {"update_frequency_ms": 0},
        {"alert_format": "xml"},
    ],
)
def test_invalid_config_raises(overrides):
    with pytest.raises(ConfigError):
        make_settings(**overrides)


def test_error_message_lists_every_problem():
    with pytest.raises(ConfigError) as exc_info:
        make_settings(imbalance_min_ratio=2.0, spoof_min_price_move=0.0)
    message = str(exc_info.value)
    assert "IMBALANCE_MIN_RATIO" in message
    assert "SPOOF_MIN_PRICE_MOVE" in message
