"""
tests/test_settings.py

Unit tests for Settings.validate(). Network-free.
"""

import pytest

from config.settings import Settings


def make_settings(**overrides):
    base = dict(rpc_url="", drift_env="mainnet", keypair_path="", markets=("SOL-PERP",))
    base.update(overrides)
    return Settings(**base)


def test_default_settings_are_valid():
    make_settings().validate()  # must not raise


def test_zero_repeated_size_tolerance_is_rejected():
    # tolerance <= 0 collapses cluster_sizes' comparison to float equality,
    # silently turning every level into its own singleton cluster — a total,
    # silent kill of both repeated_size and layering (they share this
    # setting) with no error at runtime. Must be caught at config load time.
    with pytest.raises(ValueError, match="REPEATED_SIZE_TOLERANCE"):
        make_settings(repeated_size_tolerance=0.0).validate()


def test_negative_repeated_size_tolerance_is_rejected():
    with pytest.raises(ValueError, match="REPEATED_SIZE_TOLERANCE"):
        make_settings(repeated_size_tolerance=-0.001).validate()
