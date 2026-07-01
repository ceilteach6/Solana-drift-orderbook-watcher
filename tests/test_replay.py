"""
tests/test_replay.py

Verifies detector replay/backtesting over persisted snapshots: the same
known-positive pattern used by the algorithmic self-test, fed through the
store instead of live, must still fire its detector on replay.
"""

from config.settings import Settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay_main, run_replay
from src.storage import SQLiteStore


def settings(db_path, **overrides):
    kwargs = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        db_path=db_path,
        storage_enabled=True,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def repeated_size_snapshot(ts=0.0):
    # Same shape as selftest's repeated_size scenario: 7 orders share a size.
    return OrderbookSnapshot(
        market="SOL-PERP",
        timestamp=ts,
        bids=[Level(100.0, 10.0), Level(99.9, 10.0), Level(99.8, 10.0), Level(99.7, 10.0)],
        asks=[Level(100.1, 10.0), Level(100.2, 10.0), Level(100.3, 10.0)],
    )


def seed_store(db_path, snapshot):
    store = SQLiteStore(db_path)
    store.connect()
    store.record_tick("SOL-PERP", snapshot, [], risk=None, persist_snapshot=True)
    store.close()


def test_run_replay_fires_the_matching_detector(tmp_path):
    db_path = str(tmp_path / "replay.db")
    seed_store(db_path, repeated_size_snapshot())

    stats = run_replay(settings(db_path))
    assert len(stats) == 1
    assert stats[0].market == "SOL-PERP"
    assert stats[0].snapshots == 1
    assert stats[0].detections.get("repeated_size", 0) >= 1


def test_run_replay_with_no_snapshots_returns_empty(tmp_path):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    assert run_replay(settings(db_path)) == []


def test_run_replay_filters_by_market(tmp_path):
    db_path = str(tmp_path / "multi.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_tick("SOL-PERP", repeated_size_snapshot(), [], risk=None, persist_snapshot=True)
    other = OrderbookSnapshot(market="BTC-PERP", timestamp=0.0, bids=[], asks=[])
    store.record_tick("BTC-PERP", other, [], risk=None, persist_snapshot=True)
    store.close()

    stats = run_replay(settings(db_path), market="SOL-PERP")
    assert [s.market for s in stats] == ["SOL-PERP"]


def test_replay_main_requires_storage_enabled(tmp_path, capsys):
    db_path = str(tmp_path / "replay.db")
    rc = replay_main(settings(db_path, storage_enabled=False), [])
    assert rc == 1
    assert "STORAGE_ENABLED" in capsys.readouterr().out


def test_replay_main_reports_replayed_snapshots(tmp_path, capsys):
    db_path = str(tmp_path / "replay.db")
    seed_store(db_path, repeated_size_snapshot())

    rc = replay_main(settings(db_path), [])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SOL-PERP" in out
    assert "repeated_size" in out


def test_replay_main_rejects_non_integer_limit(tmp_path, capsys):
    db_path = str(tmp_path / "replay.db")
    seed_store(db_path, repeated_size_snapshot())

    rc = replay_main(settings(db_path), ["--replay-limit", "not-a-number"])
    assert rc == 1
    assert "integer" in capsys.readouterr().out
