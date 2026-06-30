"""
tests/test_replay.py

Tests the replay/backtesting path: persisted L2 snapshots round-trip through
SQLite and back through the live detector stack + risk aggregator.
"""

from dataclasses import replace

from config.settings import settings as base_settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay_market, replay_main
from src.storage import SQLiteStore


def make_store(tmp_path):
    store = SQLiteStore(str(tmp_path / "replay.db"))
    store.connect()
    return store


def layering_snapshot(ts: float) -> OrderbookSnapshot:
    # A 7-level one-sided wall of equal sizes -> layering fires deterministically.
    return OrderbookSnapshot(
        market="SOL-PERP",
        timestamp=ts,
        bids=[Level(100.0 - i * 0.1, 25.0) for i in range(7)],
        asks=[Level(100.1, 1.0), Level(100.2, 2.0)],
    )


def test_replay_market_with_no_snapshots_is_empty(tmp_path):
    store = make_store(tmp_path)
    summary = replay_market(store, base_settings, "SOL-PERP")
    assert summary.snapshots == 0
    assert summary.detections == 0
    store.close()


def test_replay_market_refires_known_pattern(tmp_path):
    store = make_store(tmp_path)
    for i in range(5):
        store.record_snapshot(layering_snapshot(float(i)))

    summary = replay_market(store, base_settings, "SOL-PERP")
    assert summary.snapshots == 5
    assert summary.by_detector.get("layering", 0) == 5
    store.close()


def test_replay_market_is_read_only_for_other_market(tmp_path):
    store = make_store(tmp_path)
    store.record_snapshot(layering_snapshot(0.0))

    summary = replay_market(store, base_settings, "BTC-PERP")
    assert summary.snapshots == 0
    store.close()


def test_replay_main_without_db_returns_error(tmp_path):
    settings = replace(base_settings, db_path=str(tmp_path / "missing.db"))
    assert replay_main(settings) == 1


def test_replay_main_without_snapshots_returns_error(tmp_path):
    settings = replace(base_settings, db_path=str(tmp_path / "empty.db"))
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.close()

    assert replay_main(settings) == 1


def test_replay_main_runs_for_specific_market(tmp_path, capsys):
    settings = replace(base_settings, db_path=str(tmp_path / "data.db"))
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.record_snapshot(layering_snapshot(0.0))
    store.close()

    assert replay_main(settings, market="SOL-PERP") == 0
    out = capsys.readouterr().out
    assert "Replay — SOL-PERP" in out
    assert "layering" in out
