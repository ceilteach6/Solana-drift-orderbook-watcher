"""
tests/test_replay.py

Replay re-runs the real detector stack + risk aggregator over snapshots
already persisted by the watcher. These tests exercise it end-to-end against
a real (temp-file) SQLite store, the same way test_storage.py does.
"""

import dataclasses

from config.settings import load_settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay_main, replay_market
from src.storage import SQLiteStore


def make_store(tmp_path):
    store = SQLiteStore(str(tmp_path / "test.db"))
    store.connect()
    return store


def _flicker_snapshots(market="SOL-PERP"):
    # Same known-positive pattern as src/selftest.py's flicker scenario: a
    # level toggles present/absent across snapshots within the window.
    snaps = []
    for i in range(6):
        present = i % 2 == 0
        bids = [Level(100.0, 5.0)] if present else [Level(99.0, 5.0)]
        snaps.append(OrderbookSnapshot(market=market, timestamp=i * 0.5, bids=bids,
                                        asks=[Level(101.0, 1.0)]))
    return snaps


def test_replay_market_reproduces_detections_from_persisted_snapshots(tmp_path):
    store = make_store(tmp_path)
    for snap in _flicker_snapshots():
        store.record_snapshot(snap)

    settings = load_settings()
    summary = replay_market(settings, store, "SOL-PERP")

    assert summary.ticks == 6
    assert summary.detections_by_detector.get("flicker", 0) >= 1
    store.close()


def test_replay_market_respects_time_range(tmp_path):
    store = make_store(tmp_path)
    for snap in _flicker_snapshots():
        store.record_snapshot(snap)
    settings = load_settings()

    summary = replay_market(settings, store, "SOL-PERP", start_ts=10.0, end_ts=20.0)
    assert summary.ticks == 0


def test_replay_market_is_isolated_per_call(tmp_path):
    # A second replay of the same data must produce the same result as the
    # first — no state leaking from a shared aggregator/history between runs.
    store = make_store(tmp_path)
    for snap in _flicker_snapshots():
        store.record_snapshot(snap)
    settings = load_settings()

    first = replay_market(settings, store, "SOL-PERP")
    second = replay_market(settings, store, "SOL-PERP")
    assert first.detections_by_detector == second.detections_by_detector
    assert first.risk_alerts == second.risk_alerts


def test_replay_market_can_evaluate_a_different_threshold(tmp_path):
    # The whole point of replay: check a candidate threshold change against
    # history without waiting for the pattern to happen live again.
    store = make_store(tmp_path)
    for snap in _flicker_snapshots():
        store.record_snapshot(snap)
    settings = load_settings()

    lenient = replay_market(settings, store, "SOL-PERP")
    strict = replay_market(
        dataclasses.replace(settings, flicker_min_events=99), store, "SOL-PERP"
    )
    assert lenient.detections_by_detector.get("flicker", 0) > 0
    assert "flicker" not in strict.detections_by_detector


def test_replay_main_requires_storage_enabled(tmp_path):
    settings = dataclasses.replace(load_settings(), storage_enabled=False)
    assert replay_main(settings, []) == 1


def test_replay_main_requires_an_existing_db(tmp_path):
    settings = dataclasses.replace(
        load_settings(), storage_enabled=True, db_path=str(tmp_path / "missing.db")
    )
    assert replay_main(settings, []) == 1


def test_replay_main_reports_no_data_without_persisted_snapshots(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5)  # risk row, but no snapshot rows
    store.close()

    settings = dataclasses.replace(load_settings(), storage_enabled=True, db_path=db_path)
    assert replay_main(settings, []) == 1
    assert "No persisted snapshots" in capsys.readouterr().out


def test_replay_main_runs_end_to_end(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    store.connect()
    for snap in _flicker_snapshots():
        store.record_snapshot(snap)
    store.close()

    settings = dataclasses.replace(load_settings(), storage_enabled=True, db_path=db_path)
    assert replay_main(settings, ["--market=SOL-PERP"]) == 0
    assert "flicker" in capsys.readouterr().out


def test_replay_main_rejects_from_after_to(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()
    settings = dataclasses.replace(load_settings(), storage_enabled=True, db_path=db_path)
    assert replay_main(settings, ["--from=2026-06-30T12:00:00", "--to=2026-06-30T00:00:00"]) == 1
