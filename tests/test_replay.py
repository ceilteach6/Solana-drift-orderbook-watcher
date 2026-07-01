"""
tests/test_replay.py

Tests the replay/backtesting engine: snapshot reconstruction from stored rows,
a full replay pass over recorded manipulation patterns, time-range filtering,
and the parameter sweep used for threshold tuning.
"""

import dataclasses

import pytest

from config.settings import Settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import ReplayEngine, run_replay, snapshot_from_row
from src.replay.engine import _parse_sweep, _parse_when
from src.storage import SQLiteStore

MARKET = "SOL-PERP"


def make_settings(**overrides) -> Settings:
    base = dict(
        rpc_url="http://unused",
        drift_env="mainnet",
        keypair_path="",
        markets=(MARKET,),
    )
    base.update(overrides)
    return Settings(**base)


def make_store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(str(tmp_path / "replay.db"))
    store.connect()
    return store


def snap(ts, bids, asks, market=MARKET) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        market=market,
        timestamp=ts,
        bids=[Level(p, s) for p, s in bids],
        asks=[Level(p, s) for p, s in asks],
    )


def imbalanced_snap(ts, market=MARKET) -> OrderbookSnapshot:
    # Heavily bid-stacked book: fires the imbalance detector every tick,
    # which drives the risk aggregator's EMA up deterministically.
    return snap(
        ts,
        bids=[(100.0 - i, 100.0) for i in range(5)],
        asks=[(101.0 + i, 1.0) for i in range(5)],
        market=market,
    )


def quiet_snap(ts, market=MARKET) -> OrderbookSnapshot:
    # Balanced two-level book: no detector has anything to say.
    return snap(ts, bids=[(100.0, 5.0), (99.0, 4.0)],
                asks=[(101.0, 5.0), (102.0, 4.0)], market=market)


def store_snaps(store, snapshots) -> None:
    for s in snapshots:
        store.record_snapshot(s)


# --------------------------------------------------------------------------- #
# Row -> snapshot reconstruction
# --------------------------------------------------------------------------- #
def test_snapshot_roundtrip_through_store(tmp_path):
    store = make_store(tmp_path)
    original = snap(12.5, bids=[(100.0, 5.0), (99.9, 3.0)], asks=[(100.1, 4.0)])
    store.record_snapshot(original)

    row = next(store.iter_snapshots(MARKET))
    rebuilt = snapshot_from_row(row)

    assert rebuilt.market == MARKET
    assert rebuilt.timestamp == 12.5
    assert [(l.price, l.size) for l in rebuilt.bids] == [(100.0, 5.0), (99.9, 3.0)]
    assert [(l.price, l.size) for l in rebuilt.asks] == [(100.1, 4.0)]
    assert rebuilt.mid == pytest.approx(100.05)
    store.close()


def test_iter_snapshots_respects_range_and_order(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [quiet_snap(float(t)) for t in (3, 1, 2, 4)])

    all_ts = [r["ts"] for r in store.iter_snapshots(MARKET)]
    assert all_ts == [1.0, 2.0, 3.0, 4.0]

    windowed = [r["ts"] for r in store.iter_snapshots(MARKET, start=2.0, end=3.0)]
    assert windowed == [2.0, 3.0]

    assert store.snapshot_markets() == [MARKET]
    store.close()


# --------------------------------------------------------------------------- #
# Replay pass
# --------------------------------------------------------------------------- #
def test_replay_fires_detectors_and_risk_on_recorded_pattern(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(10)])

    report = ReplayEngine(make_settings(), store).run(MARKET)

    assert report.snapshots == 10
    assert report.t_start == 0.0 and report.t_end == 9.0
    assert report.detections["imbalance"].count == 10
    assert report.detections["imbalance"].max_score > 0.6
    # A sustained signal must cross the (default 0.6) alert threshold.
    assert report.risk_alerts >= 1
    assert report.max_risk > 0.6
    assert 0.0 < report.elevated_fraction <= 1.0
    store.close()


def test_replay_quiet_data_produces_no_alerts(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [quiet_snap(float(t)) for t in range(5)])

    report = ReplayEngine(make_settings(), store).run(MARKET)

    assert report.snapshots == 5
    assert report.total_detections == 0
    assert report.risk_alerts == 0
    assert report.max_risk == 0.0
    store.close()


def test_replay_time_range_filters(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(10)])

    report = ReplayEngine(make_settings(), store).run(MARKET, start=3.0, end=6.0)
    assert report.snapshots == 4
    assert report.t_start == 3.0 and report.t_end == 6.0
    store.close()


def test_replay_without_risk_aggregation(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(3)])

    report = ReplayEngine(make_settings(risk_aggregation=False), store).run(MARKET)
    assert report.detections["imbalance"].count == 3
    assert report.risk_alerts is None
    assert report.max_risk is None
    assert "risk" not in report.format()  # no risk line without the aggregator
    store.close()


def test_replay_uses_stored_timestamps_for_windows(tmp_path):
    # A level flickering at 0.5s cadence must trigger the flicker detector
    # from stored timestamps alone (replay runs much faster than real time).
    store = make_store(tmp_path)
    snaps = []
    for i in range(6):
        bids = [(100.0, 5.0)] if i % 2 == 0 else [(99.0, 5.0)]
        snaps.append(snap(i * 0.5, bids, [(101.0, 1.0)]))
    store_snaps(store, snaps)

    report = ReplayEngine(make_settings(), store).run(MARKET)
    assert report.detections["flicker"].count >= 1
    store.close()


def test_replay_report_format_mentions_key_numbers(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(4)])

    text = ReplayEngine(make_settings(), store).run(MARKET).format()
    assert MARKET in text
    assert "snapshots : 4" in text
    assert "imbalance" in text
    assert "risk" in text
    store.close()


# --------------------------------------------------------------------------- #
# Parameter sweep (threshold tuning)
# --------------------------------------------------------------------------- #
def test_sweep_varies_alert_counts(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(10)])

    engine = ReplayEngine(make_settings(), store)
    # The imbalanced book drives the noisy-OR instant risk to ~0.99, so the
    # EMA can approach but never reach 0.995.
    results = engine.sweep(MARKET, "risk_alert_threshold", [0.5, 0.995])

    assert [v for v, _ in results] == [0.5, 0.995]
    low, high = results[0][1], results[1][1]
    assert low.risk_alerts >= 1
    assert high.risk_alerts == 0
    assert low.elevated_fraction > high.elevated_fraction
    store.close()


def test_sweep_reports_invalid_combination_instead_of_crashing(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [quiet_snap(1.0)])

    engine = ReplayEngine(make_settings(), store)
    # 0.3 is below the default clear threshold (0.4) -> Settings rejects it;
    # the sweep must carry on with the remaining values.
    results = engine.sweep(MARKET, "risk_alert_threshold", [0.3, 0.7])

    assert isinstance(results[0][1], str)  # the validation error text
    assert results[1][1].snapshots == 1
    store.close()


def test_sweep_rejects_unknown_and_non_numeric_params(tmp_path):
    store = make_store(tmp_path)
    engine = ReplayEngine(make_settings(), store)
    with pytest.raises(AttributeError):
        engine.sweep(MARKET, "no_such_setting", [1.0])
    with pytest.raises(ValueError):
        engine.sweep(MARKET, "alert_format", [1.0])
    with pytest.raises(ValueError):
        engine.sweep(MARKET, "risk_aggregation", [1.0])  # bool is not tunable
    store.close()


def test_sweep_casts_to_int_fields(tmp_path):
    store = make_store(tmp_path)
    store_snaps(store, [imbalanced_snap(1.0)])

    engine = ReplayEngine(make_settings(), store)
    results = engine.sweep(MARKET, "imbalance_min_levels", [3.0])
    value, report = results[0]
    assert value == 3 and isinstance(value, int)
    assert report.snapshots == 1
    store.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_run_replay_cli_with_data(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    store = SQLiteStore(db)
    store.connect()
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(5)])
    store.close()

    code = run_replay(make_settings(db_path=db), [])
    out = capsys.readouterr().out
    assert code == 0
    assert MARKET in out and "imbalance" in out


def test_run_replay_cli_empty_db_exits_nonzero(tmp_path, capsys):
    db = str(tmp_path / "empty.db")
    code = run_replay(make_settings(db_path=db), [])
    out = capsys.readouterr().out
    assert code == 1
    assert "PERSIST_SNAPSHOTS" in out


def test_run_replay_cli_sweep_table(tmp_path, capsys):
    db = str(tmp_path / "sweep.db")
    store = SQLiteStore(db)
    store.connect()
    store_snaps(store, [imbalanced_snap(float(t)) for t in range(6)])
    store.close()

    code = run_replay(make_settings(db_path=db),
                      ["--sweep", "risk_alert_threshold=0.5,0.7"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Sweep — risk_alert_threshold" in out
    assert "0.5" in out and "0.7" in out


def test_run_replay_cli_invalid_sweep_exits_2(tmp_path, capsys):
    db = str(tmp_path / "bad.db")
    store = SQLiteStore(db)
    store.connect()
    store_snaps(store, [quiet_snap(1.0)])
    store.close()

    assert run_replay(make_settings(db_path=db), ["--sweep", "nonsense"]) == 2
    assert run_replay(make_settings(db_path=db),
                      ["--sweep", "no_such_setting=1,2"]) == 2


# --------------------------------------------------------------------------- #
# CLI argument parsing helpers
# --------------------------------------------------------------------------- #
def test_parse_when_accepts_epoch_and_iso():
    assert _parse_when("1234.5") == 1234.5
    from datetime import datetime
    assert _parse_when("2026-07-01") == datetime(2026, 7, 1).timestamp()


def test_parse_sweep():
    assert _parse_sweep("risk_smoothing=0.2,0.4") == ("risk_smoothing", [0.2, 0.4])
    with pytest.raises(ValueError):
        _parse_sweep("missing-equals")
    with pytest.raises(ValueError):
        _parse_sweep("param=")
