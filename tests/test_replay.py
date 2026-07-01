"""
tests/test_replay.py

Replay/backtesting works off L2 snapshots already persisted in SQLite
(the same table PERSIST_SNAPSHOTS=true writes to) and re-runs the current
detector stack over them, independent of any live feed.
"""

from config.settings import load_settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay, replay_main
from src.storage import SQLiteStore


def _snap(ts, bids, asks):
    return OrderbookSnapshot(
        market="SOL-PERP",
        timestamp=ts,
        bids=[Level(p, s) for p, s in bids],
        asks=[Level(p, s) for p, s in asks],
    )


def _seed_layering_wall(store, market="SOL-PERP"):
    # A single tick with a 7-level one-sided wall of equal sizes — the exact
    # shape src/selftest.py uses to deterministically trigger `layering`.
    snapshot = _snap(
        0.0,
        bids=[(100.0 - i * 0.1, 25.0) for i in range(7)],
        asks=[(100.1, 1.0), (100.2, 2.0)],
    )
    store.record_snapshot(snapshot)


def test_replay_reproduces_a_known_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_AGGREGATION", "false")  # inspect raw detections
    settings = load_settings()

    db_path = str(tmp_path / "replay.db")
    store = SQLiteStore(db_path)
    store.connect()
    _seed_layering_wall(store)
    store.close()

    monkeypatch.setenv("DB_PATH", db_path)
    settings = load_settings()

    results = replay(settings, "SOL-PERP")
    assert len(results) == 1
    _, detections = results[0]
    assert any(d.detector == "layering" for d in detections)


def test_replay_is_chronological_across_multiple_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_AGGREGATION", "false")
    db_path = str(tmp_path / "replay.db")
    store = SQLiteStore(db_path)
    store.connect()
    for i, ts in enumerate([2.0, 0.0, 1.0]):  # inserted out of order
        store.record_snapshot(_snap(ts, [(100.0, float(i) + 1)], [(100.1, 1.0)]))
    store.close()

    monkeypatch.setenv("DB_PATH", db_path)
    settings = load_settings()

    results = replay(settings, "SOL-PERP")
    assert [ts for ts, _ in results] == [0.0, 1.0, 2.0]


def test_replay_with_no_persisted_snapshots_returns_empty(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    monkeypatch.setenv("DB_PATH", db_path)
    settings = load_settings()

    assert replay(settings, "SOL-PERP") == []


def test_replay_main_reports_failure_when_market_has_no_data(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    monkeypatch.setenv("DB_PATH", db_path)
    settings = load_settings()

    assert replay_main(settings, "SOL-PERP") == 1
    assert "No persisted snapshots" in capsys.readouterr().out


def test_replay_main_reports_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RISK_AGGREGATION", "false")
    db_path = str(tmp_path / "replay.db")
    store = SQLiteStore(db_path)
    store.connect()
    _seed_layering_wall(store)
    store.close()

    monkeypatch.setenv("DB_PATH", db_path)
    settings = load_settings()

    assert replay_main(settings, "SOL-PERP") == 0
    out = capsys.readouterr().out
    assert "layering" in out
