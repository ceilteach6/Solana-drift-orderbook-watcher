"""
tests/test_risk_aggregator.py

Unit tests for the RiskAggregator and SpoofPullDetector.
Network-free and driftpy-free.
"""

import time
from types import SimpleNamespace

import pytest

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector.spoof_pull import SpoofPullDetector
from src.risk_aggregator import RiskAggregator


def make_settings(**overrides):
    base = dict(
        risk_ema_alpha=0.5,
        risk_composite_threshold=0.70,
        spoof_pull_levels=5,
        spoof_pull_vol_ratio=0.50,
        spoof_pull_price_delta=0.001,
        alert_min_score=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def snap(bids=None, asks=None, market="SOL-PERP", ts=None):
    return OrderbookSnapshot(
        market=market,
        timestamp=ts if ts is not None else time.time(),
        bids=[Level(p, s) for p, s in (bids or [])],
        asks=[Level(p, s) for p, s in (asks or [])],
    )


# ---------------------------------------------------------------------------
# RiskAggregator
# ---------------------------------------------------------------------------

class TestRiskAggregator:
    def test_initial_score_is_zero(self):
        agg = RiskAggregator(make_settings())
        assert agg.score("SOL-PERP") == 0.0

    def test_ema_rises_on_high_score(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=0.5))
        det = SimpleNamespace(score=1.0)
        s1 = agg.update("SOL-PERP", [det])
        assert s1 == pytest.approx(0.5)  # alpha * 1.0 + (1-alpha) * 0.0
        s2 = agg.update("SOL-PERP", [det])
        assert s2 == pytest.approx(0.75)

    def test_ema_decays_without_detections(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=0.5))
        det = SimpleNamespace(score=1.0)
        agg.update("SOL-PERP", [det])
        # No detections → tick_score = 0 → EMA decays
        s = agg.update("SOL-PERP", [])
        assert s == pytest.approx(0.25)

    def test_is_elevated_at_threshold(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=1.0, risk_composite_threshold=0.7))
        det = SimpleNamespace(score=0.8)
        agg.update("SOL-PERP", [det])
        assert agg.is_elevated("SOL-PERP") is True

    def test_is_not_elevated_below_threshold(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=1.0, risk_composite_threshold=0.7))
        det = SimpleNamespace(score=0.5)
        agg.update("SOL-PERP", [det])
        assert agg.is_elevated("SOL-PERP") is False

    def test_max_score_used_per_tick(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=1.0))
        dets = [SimpleNamespace(score=0.3), SimpleNamespace(score=0.9)]
        s = agg.update("SOL-PERP", dets)
        assert s == pytest.approx(0.9)

    def test_independent_markets(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=1.0))
        agg.update("SOL-PERP", [SimpleNamespace(score=0.8)])
        agg.update("BTC-PERP", [SimpleNamespace(score=0.2)])
        assert agg.score("SOL-PERP") == pytest.approx(0.8)
        assert agg.score("BTC-PERP") == pytest.approx(0.2)

    def test_all_scores_returns_snapshot(self):
        agg = RiskAggregator(make_settings(risk_ema_alpha=1.0))
        agg.update("SOL-PERP", [SimpleNamespace(score=0.5)])
        scores = agg.all_scores()
        assert "SOL-PERP" in scores
        assert scores["SOL-PERP"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# SpoofPullDetector
# ---------------------------------------------------------------------------

class TestSpoofPullDetector:
    def test_fires_on_bid_wall_pull_with_price_drop(self):
        det = SpoofPullDetector(make_settings())
        mid = 100.0
        # Previous snapshot: heavy bid wall
        prev = snap(
            bids=[(mid - i * 0.1, 200.0) for i in range(5)],
            asks=[(mid + i * 0.1, 5.0) for i in range(1, 6)],
            ts=1000.0,
        )
        # Current snapshot: bid wall collapsed, price dropped
        new_mid = mid * 0.998  # -0.2% drop
        curr = snap(
            bids=[(new_mid - i * 0.1, 10.0) for i in range(5)],
            asks=[(new_mid + i * 0.1, 5.0) for i in range(1, 6)],
            ts=1001.0,
        )
        result = det.analyze(curr, [prev])
        assert len(result) == 1
        assert result[0].details["side"] == "bid"
        assert result[0].score > 0
        assert result[0].details["collapse_ratio"] > 0.5

    def test_fires_on_ask_wall_pull_with_price_rise(self):
        det = SpoofPullDetector(make_settings())
        mid = 100.0
        prev = snap(
            bids=[(mid - i * 0.1, 5.0) for i in range(1, 6)],
            asks=[(mid + i * 0.1, 200.0) for i in range(5)],
            ts=1000.0,
        )
        new_mid = mid * 1.002  # +0.2% rise
        curr = snap(
            bids=[(new_mid - i * 0.1, 5.0) for i in range(1, 6)],
            asks=[(new_mid + i * 0.1, 10.0) for i in range(5)],
            ts=1001.0,
        )
        result = det.analyze(curr, [prev])
        assert len(result) == 1
        assert result[0].details["side"] == "ask"

    def test_quiet_when_no_history(self):
        det = SpoofPullDetector(make_settings())
        s = snap(bids=[(100.0, 5.0)], asks=[(101.0, 5.0)])
        assert det.analyze(s, []) == []

    def test_quiet_when_wall_stays(self):
        """No collapse → no spoof-pull."""
        det = SpoofPullDetector(make_settings())
        mid = 100.0
        prev = snap(
            bids=[(mid - i * 0.1, 200.0) for i in range(5)],
            asks=[(mid + i * 0.1, 5.0) for i in range(1, 6)],
            ts=1000.0,
        )
        new_mid = mid * 0.998
        curr = snap(
            bids=[(new_mid - i * 0.1, 195.0) for i in range(5)],
            asks=[(new_mid + i * 0.1, 5.0) for i in range(1, 6)],
            ts=1001.0,
        )
        assert det.analyze(curr, [prev]) == []

    def test_quiet_when_price_doesnt_move(self):
        """Wall collapses but price stays flat → not a confirmed spoof-pull."""
        det = SpoofPullDetector(make_settings())
        mid = 100.0
        prev = snap(
            bids=[(mid - i * 0.1, 200.0) for i in range(5)],
            asks=[(mid + i * 0.1, 5.0) for i in range(1, 6)],
            ts=1000.0,
        )
        curr = snap(
            bids=[(mid - i * 0.1, 10.0) for i in range(5)],
            asks=[(mid + i * 0.1, 5.0) for i in range(1, 6)],
            ts=1001.0,
        )
        assert det.analyze(curr, [prev]) == []

    def test_quiet_on_empty_book(self):
        det = SpoofPullDetector(make_settings())
        prev = snap()
        curr = snap()
        assert det.analyze(curr, [prev]) == []
