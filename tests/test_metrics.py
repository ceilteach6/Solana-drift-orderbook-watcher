"""
tests/test_metrics.py

Unit tests for the Prometheus registry (text exposition format, label
validation) and an end-to-end check that the ``/metrics`` HTTP server serves
what the registry has recorded.
"""

import urllib.error
import urllib.request

import pytest

from src.metrics.registry import Counter, Gauge, Registry
from src.metrics.server import MetricsServer
from src.metrics.watcher_metrics import WatcherMetrics


def test_counter_accumulates_per_label_combination():
    c = Counter("requests_total", "help", ("market",))
    c.inc(market="SOL-PERP")
    c.inc(2.0, market="SOL-PERP")
    c.inc(market="BTC-PERP")
    lines = c.render()
    assert 'requests_total{market="SOL-PERP"} 3.0' in lines
    assert 'requests_total{market="BTC-PERP"} 1.0' in lines


def test_counter_rejects_negative_increment():
    c = Counter("x", "help")
    with pytest.raises(ValueError):
        c.inc(-1.0)


def test_gauge_overwrites_rather_than_accumulates():
    g = Gauge("risk", "help", ("market",))
    g.set(0.3, market="SOL-PERP")
    g.set(0.7, market="SOL-PERP")
    lines = g.render()
    assert 'risk{market="SOL-PERP"} 0.7' in lines


def test_metric_rejects_missing_or_unexpected_labels():
    c = Counter("x", "help", ("market",))
    with pytest.raises(ValueError):
        c.inc()  # missing "market"
    with pytest.raises(ValueError):
        c.inc(market="SOL-PERP", extra="oops")


def test_label_values_are_escaped_in_the_exposition_format():
    # Regression: an unescaped quote/backslash/newline in a label value would
    # corrupt the line framing of the whole exposition payload, not just this
    # metric — any consumer's parser breaks on a single tainted sample.
    c = Counter("x", "help", ("msg",))
    c.inc(msg='has "quotes" and \\backslash\\ and\nnewline')
    line = c.render()[-1]
    assert '\\"quotes\\"' in line
    assert "\\\\backslash\\\\" in line
    assert "\\n" in line
    assert "\n" not in line.split(" ", 1)[0]  # no literal newline in the label section


def test_registry_render_includes_help_and_type_and_is_stable():
    registry = Registry()
    counter = registry.counter("c_total", "a counter", ("market",))
    counter.inc(market="SOL-PERP")
    text = registry.render()
    assert "# HELP c_total a counter" in text
    assert "# TYPE c_total counter" in text
    assert 'c_total{market="SOL-PERP"} 1.0' in text
    assert text.endswith("\n")


def test_empty_registry_renders_empty_string():
    assert Registry().render() == ""


def test_watcher_metrics_registers_expected_series():
    m = WatcherMetrics()
    m.ticks_total.inc(market="SOL-PERP")
    m.detections_total.inc(detector="flicker", market="SOL-PERP")
    m.risk_score.set(0.42, market="SOL-PERP")
    m.healthcheck_failures_total.inc()
    text = m.registry.render()
    assert 'watcher_ticks_total{market="SOL-PERP"} 1.0' in text
    assert 'watcher_detections_total{detector="flicker",market="SOL-PERP"} 1.0' in text
    assert 'watcher_risk_score{market="SOL-PERP"} 0.42' in text
    assert "watcher_healthcheck_failures_total 1.0" in text


class _Settings:
    metrics_host = "127.0.0.1"
    metrics_port = 0  # let the OS assign a free port


def test_metrics_server_serves_the_registry_over_http():
    registry = Registry()
    counter = registry.counter("served_total", "help", ())
    counter.inc(3.0)

    server = MetricsServer(_Settings(), registry)
    server.start()
    try:
        with urllib.request.urlopen(server.url, timeout=5) as resp:
            assert resp.status == 200
            assert "text/plain" in resp.headers["Content-Type"]
            body = resp.read().decode("utf-8")
        assert "served_total 3.0" in body
    finally:
        server.stop()


def test_metrics_server_404s_on_unknown_paths():
    server = MetricsServer(_Settings(), Registry())
    server.start()
    try:
        base = server.url.rsplit("/metrics", 1)[0]
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}/nope", timeout=5)
        assert excinfo.value.code == 404
    finally:
        server.stop()
