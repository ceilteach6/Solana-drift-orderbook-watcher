"""
tests/test_metrics.py

Unit tests for MetricsExporter.

All tests are network-free. When prometheus_client is available we exercise the
real counters/gauges; we also verify the no-op path (port=0) works regardless.
"""

from types import SimpleNamespace

import pytest

from src.metrics.prometheus import MetricsExporter, _AVAILABLE


def make_settings(**kw):
    base = dict(metrics_port=0)
    base.update(kw)
    return SimpleNamespace(**base)


def make_detection(**kw):
    return SimpleNamespace(
        detector=kw.get("detector", "flicker"),
        market=kw.get("market", "SOL-PERP"),
        score=kw.get("score", 0.75),
    )


# ---------------------------------------------------------------------------
# No-op path (port=0 or prometheus_client absent)
# ---------------------------------------------------------------------------

class TestNoop:
    def test_inactive_when_port_zero(self):
        exp = MetricsExporter(make_settings(metrics_port=0))
        assert exp._active is False

    def test_start_is_safe_when_inactive(self):
        exp = MetricsExporter(make_settings(metrics_port=0))
        exp.start()  # must not raise

    def test_record_methods_are_safe_when_inactive(self):
        exp = MetricsExporter(make_settings(metrics_port=0))
        exp.record_detections([make_detection()])
        exp.record_risk_score("SOL-PERP", 0.5)
        exp.record_alerts("SOL-PERP", 3)
        exp.record_tick("SOL-PERP")  # must not raise


# ---------------------------------------------------------------------------
# Active path (only when prometheus_client is installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _AVAILABLE, reason="prometheus_client not installed")
class TestActive:
    def _exporter(self, **kw):
        # Port 0 would disable; use a non-zero port but don't actually start
        # the HTTP server (we only test counter/gauge logic here).
        return MetricsExporter(make_settings(metrics_port=kw.get("port", 9999)))

    def test_active_when_port_nonzero(self):
        exp = self._exporter()
        assert exp._active is True

    def test_record_detection_increments_counter(self):
        exp = self._exporter()
        d = make_detection(detector="layering", market="BTC-PERP")
        exp.record_detections([d, d])
        val = exp._detections_total.labels(detector="layering", market="BTC-PERP")._value.get()
        assert val == pytest.approx(2.0)

    def test_record_multiple_detectors(self):
        exp = self._exporter()
        exp.record_detections([
            make_detection(detector="flicker", market="SOL-PERP"),
            make_detection(detector="imbalance", market="SOL-PERP"),
        ])
        flicker_val = exp._detections_total.labels(detector="flicker", market="SOL-PERP")._value.get()
        imbal_val = exp._detections_total.labels(detector="imbalance", market="SOL-PERP")._value.get()
        assert flicker_val == pytest.approx(1.0)
        assert imbal_val == pytest.approx(1.0)

    def test_record_risk_score_sets_gauge(self):
        exp = self._exporter()
        exp.record_risk_score("SOL-PERP", 0.42)
        val = exp._risk_score.labels(market="SOL-PERP")._value.get()
        assert val == pytest.approx(0.42)

    def test_record_risk_score_overwrites(self):
        exp = self._exporter()
        exp.record_risk_score("SOL-PERP", 0.1)
        exp.record_risk_score("SOL-PERP", 0.9)
        val = exp._risk_score.labels(market="SOL-PERP")._value.get()
        assert val == pytest.approx(0.9)

    def test_record_alerts_increments_counter(self):
        exp = self._exporter()
        exp.record_alerts("SOL-PERP", 2)
        exp.record_alerts("SOL-PERP", 1)
        val = exp._alerts_total.labels(market="SOL-PERP")._value.get()
        assert val == pytest.approx(3.0)

    def test_record_alerts_zero_is_noop(self):
        exp = self._exporter()
        exp.record_alerts("SOL-PERP", 0)
        val = exp._alerts_total.labels(market="SOL-PERP")._value.get()
        assert val == pytest.approx(0.0)

    def test_record_tick_increments_counter(self):
        exp = self._exporter()
        exp.record_tick("ETH-PERP")
        exp.record_tick("ETH-PERP")
        val = exp._ticks_total.labels(market="ETH-PERP")._value.get()
        assert val == pytest.approx(2.0)

    def test_markets_are_independent(self):
        exp = self._exporter()
        exp.record_tick("SOL-PERP")
        exp.record_tick("SOL-PERP")
        exp.record_tick("BTC-PERP")
        sol_val = exp._ticks_total.labels(market="SOL-PERP")._value.get()
        btc_val = exp._ticks_total.labels(market="BTC-PERP")._value.get()
        assert sol_val == pytest.approx(2.0)
        assert btc_val == pytest.approx(1.0)
