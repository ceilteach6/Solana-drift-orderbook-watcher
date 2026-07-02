"""
tests/test_replay.py

Verifies replay/backtesting: it must reconstruct persisted L2 snapshots and
re-run the *current* detector + risk stack over them, without touching the
store (read-only), and report cleanly when nothing was persisted to replay.
"""

import dataclasses

from config.settings import load_settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import format_report, replay_main, replay_market
from src.storage import SQLiteStore


def settings_with_db(tmp_path, **overrides):
    return dataclasses.replace(load_settings(), db_path=str(tmp_path / "replay.db"), **overrides)


def repeated_size_snapshot(ts, market="SOL-PERP"):
    # 4 bids share the exact same size -> repeated_size's known-positive shape
    # (mirrors src/selftest.py's _sc_repeated_size scenario).
    return OrderbookSnapshot(
        market=market,
        timestamp=ts,
        bids=[Level(100.0 - i * 0.1, 10.0) for i in range(4)],
        asks=[Level(100.1 + i * 0.1, 1.0 + i) for i in range(3)],
    )


def test_replay_with_no_persisted_data_returns_empty_report(tmp_path):
    settings = settings_with_db(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.close()

    report = replay_market(settings, "SOL-PERP")
    assert report.ticks == 0
    assert "No persisted snapshots" in format_report(report)
    assert replay_main(settings, "SOL-PERP") == 1


def test_replay_reruns_detectors_against_persisted_snapshots(tmp_path):
    settings = settings_with_db(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.connect()
    for i in range(3):
        store.record_tick(
            "SOL-PERP", repeated_size_snapshot(float(i)), [], persist_snapshot=True
        )
    store.close()

    report = replay_market(settings, "SOL-PERP")
    assert report.ticks == 3
    assert report.detector_stats["repeated_size"].fired == 3
    assert report.detector_stats["repeated_size"].max_score > 0
    assert "repeated_size" in format_report(report)


def test_replay_only_touches_the_requested_market(tmp_path):
    settings = settings_with_db(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.record_tick(
        "BTC-PERP", repeated_size_snapshot(0.0, market="BTC-PERP"), [], persist_snapshot=True
    )
    store.close()

    report = replay_market(settings, "SOL-PERP")
    assert report.ticks == 0


def test_replay_never_writes_back_to_the_store(tmp_path):
    settings = settings_with_db(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.record_tick(
        "SOL-PERP", repeated_size_snapshot(0.0), [], persist_snapshot=True
    )
    before = store.counts()

    replay_market(settings, "SOL-PERP")

    after = store.counts()
    store.close()
    assert after == before


def test_replay_main_returns_zero_when_data_found(tmp_path):
    settings = settings_with_db(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.record_tick(
        "SOL-PERP", repeated_size_snapshot(0.0), [], persist_snapshot=True
    )
    store.close()

    assert replay_main(settings, "SOL-PERP") == 0
