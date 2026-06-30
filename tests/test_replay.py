"""
tests/test_replay.py

Tests the replay engine: in-memory snapshot replay and a full round-trip through
the SQLite store (write snapshots -> run_replay reads them back).
"""

from config.settings import load_settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay.engine import replay_snapshots, run_replay
from src.storage import SQLiteStore


def _snap(ts, bids, asks, market="SOL-PERP"):
    return OrderbookSnapshot(
        market=market,
        timestamp=ts,
        bids=[Level(p, s) for p, s in bids],
        asks=[Level(p, s) for p, s in asks],
    )


def _repeated_size_snap(ts):
    # 7 identical sizes -> repeated_size fires.
    return _snap(
        ts,
        bids=[(100.0, 10.0), (99.9, 10.0), (99.8, 10.0), (99.7, 10.0)],
        asks=[(100.1, 10.0), (100.2, 10.0), (100.3, 10.0)],
    )


def test_replay_counts_snapshots_and_detections():
    settings = load_settings()
    snaps = [_repeated_size_snap(float(i)) for i in range(5)]
    result = replay_snapshots(settings, "SOL-PERP", snaps)

    assert result.snapshots == 5
    assert result.detections_by_detector.get("repeated_size", 0) == 5
    assert result.start_ts == 0.0
    assert result.end_ts == 4.0


def test_replay_empty_sequence():
    result = replay_snapshots(load_settings(), "SOL-PERP", [])
    assert result.snapshots == 0
    assert result.detections_by_detector == {}


def test_run_replay_roundtrip_through_store(tmp_path, capsys):
    settings = load_settings()
    settings = settings.__class__(**{**settings.__dict__, "db_path": str(tmp_path / "r.db")})

    store = SQLiteStore(settings.db_path)
    store.connect()
    for i in range(4):
        store.record_snapshot(_repeated_size_snap(float(i)))
    store.close()

    code = run_replay(settings)
    out = capsys.readouterr().out
    assert code == 0
    assert "Replay" in out
    assert "repeated_size" in out


def test_run_replay_no_snapshots(tmp_path):
    settings = load_settings()
    settings = settings.__class__(**{**settings.__dict__, "db_path": str(tmp_path / "empty.db")})
    # Create an empty DB (schema only).
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.close()

    assert run_replay(settings) == 1
