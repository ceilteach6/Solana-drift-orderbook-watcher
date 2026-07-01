"""
tests/test_settings.py

Settings must clamp out-of-range numeric knobs rather than accepting values
that would silently degrade behavior (e.g. update_frequency_ms=0 dividing by
zero elsewhere) or balloon memory (a tiny interval combined with a huge
flicker window).
"""

from config.settings import Settings


def test_zero_update_frequency_is_clamped():
    s = Settings(rpc_url="x", drift_env="mainnet", keypair_path="", update_frequency_ms=0)
    assert s.update_frequency_ms >= 1


def test_alert_min_score_is_clamped_to_unit_range():
    s = Settings(rpc_url="x", drift_env="mainnet", keypair_path="", alert_min_score=5.0)
    assert s.alert_min_score == 1.0

    s = Settings(rpc_url="x", drift_env="mainnet", keypair_path="", alert_min_score=-1.0)
    assert s.alert_min_score == 0.0


def test_in_range_values_pass_through_unchanged():
    s = Settings(
        rpc_url="x", drift_env="mainnet", keypair_path="",
        update_frequency_ms=500, alert_min_score=0.7,
    )
    assert s.update_frequency_ms == 500
    assert s.alert_min_score == 0.7
