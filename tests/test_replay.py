"""
tests/test_replay.py

Replay re-runs stored L2 snapshots through the live detector stack — it must
reproduce what a live run would have found, and degrade cleanly (exit 1, no
crash) when nothing was persisted yet.
"""

from config.settings import load_settings
from src.collector.orderbook_feed import OrderbookSnapshot
from src.replay import replay_main, run_replay
from src.storage import SQLiteStore


def make_settings(**overrides):
    settings = load_settings()
    return settings.__class__(**{**settings.__dict__, **overrides})


def _snap(market, ts, bids, asks):
    from src.collector.orderbook_feed import Level

    return OrderbookSnapshot(
        market=market,
        timestamp=ts,
        bids=[Level(p, s) for p, s in bids],
        asks=[Level(p, s) for p, s in asks],
    )


def test_run_replay_with_no_stored_snapshots_returns_empty_report(tmp_path):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    settings = make_settings(db_path=db_path)
    report = run_replay(settings, "SOL-PERP")
    assert report.snapshot_count == 0
    assert report.detection_counts == {}


def test_replay_main_returns_1_when_db_has_no_snapshots(tmp_path, capsys):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    settings = make_settings(db_path=db_path)
    code = replay_main(settings, "SOL-PERP")
    assert code == 1
    assert "No stored snapshots" in capsys.readouterr().out


def test_run_replay_reproduces_repeated_size_detection(tmp_path):
    db_path = str(tmp_path / "data.db")
    market = "SOL-PERP"
    store = SQLiteStore(db_path)
    store.connect()
    # 5 bids share size 10.0 -> repeated_size fires under default threshold (4).
    snapshot = _snap(
        market,
        1.0,
        bids=[(100.0, 10.0), (99.9, 10.0), (99.8, 10.0), (99.7, 10.0), (99.6, 10.0)],
        asks=[(100.1, 1.0), (100.2, 2.0)],
    )
    store.record_snapshot(snapshot)
    store.close()

    settings = make_settings(db_path=db_path)
    report = run_replay(settings, market)
    assert report.snapshot_count == 1
    assert report.detection_counts.get("repeated_size", 0) >= 1


def test_run_replay_respects_limit_as_most_recent_n(tmp_path):
    db_path = str(tmp_path / "data.db")
    market = "SOL-PERP"
    store = SQLiteStore(db_path)
    store.connect()
    for ts in range(5):
        store.record_snapshot(
            _snap(market, float(ts), bids=[(100.0, 1.0)], asks=[(100.1, 1.0)])
        )
    store.close()

    settings = make_settings(db_path=db_path)
    report = run_replay(settings, market, limit=2)
    assert report.snapshot_count == 2
