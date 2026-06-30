"""
tests/test_metrics.py

Tests the optional Prometheus exporter: the in-process registry, the text
exposition rendering, and the HTTP server end-to-end (bound to an ephemeral
port so the test never collides with a real watcher).
"""

import urllib.request

from src.metrics.format import render
from src.metrics.registry import MetricsRegistry
from src.metrics.server import start_metrics_server


def test_counter_accumulates_per_label_set():
    reg = MetricsRegistry()
    reg.inc("drift_watcher_snapshots_total", {"market": "SOL-PERP"})
    reg.inc("drift_watcher_snapshots_total", {"market": "SOL-PERP"})
    reg.inc("drift_watcher_snapshots_total", {"market": "BTC-PERP"})
    counters, _ = reg.snapshot()
    assert counters[("drift_watcher_snapshots_total", (("market", "SOL-PERP"),))] == 2
    assert counters[("drift_watcher_snapshots_total", (("market", "BTC-PERP"),))] == 1


def test_counter_inc_accepts_explicit_value():
    reg = MetricsRegistry()
    reg.inc("drift_watcher_detections_total", {"market": "SOL-PERP"}, value=3)
    counters, _ = reg.snapshot()
    assert counters[("drift_watcher_detections_total", (("market", "SOL-PERP"),))] == 3


def test_gauge_overwrites_not_accumulates():
    reg = MetricsRegistry()
    reg.set("drift_watcher_risk_score", {"market": "SOL-PERP"}, value=0.4)
    reg.set("drift_watcher_risk_score", {"market": "SOL-PERP"}, value=0.7)
    _, gauges = reg.snapshot()
    assert gauges[("drift_watcher_risk_score", (("market", "SOL-PERP"),))] == 0.7


def test_render_includes_help_type_and_labels():
    reg = MetricsRegistry()
    reg.inc("drift_watcher_snapshots_total", {"market": "SOL-PERP"}, value=5)
    reg.set("drift_watcher_risk_score", {"market": "SOL-PERP"}, value=0.42)
    text = render(reg)
    assert "# TYPE drift_watcher_snapshots_total counter" in text
    assert "# TYPE drift_watcher_risk_score gauge" in text
    assert 'drift_watcher_snapshots_total{market="SOL-PERP"} 5' in text
    assert 'drift_watcher_risk_score{market="SOL-PERP"} 0.42' in text


def test_render_escapes_label_values():
    reg = MetricsRegistry()
    reg.inc("drift_watcher_snapshots_total", {"market": 'weird"market\\x'})
    text = render(reg)
    assert 'market="weird\\"market\\\\x"' in text


def test_render_empty_registry_is_empty_string():
    assert render(MetricsRegistry()) == ""


def test_metrics_http_server_serves_current_state():
    reg = MetricsRegistry()
    reg.inc("drift_watcher_snapshots_total", {"market": "SOL-PERP"})
    server = start_metrics_server(reg, "127.0.0.1", 0)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
            assert resp.status == 200
            assert "text/plain" in resp.headers["Content-Type"]
            body = resp.read().decode("utf-8")
        assert 'drift_watcher_snapshots_total{market="SOL-PERP"} 1' in body

        # The server reflects live registry state, not a snapshot taken at
        # start_metrics_server() time.
        reg.inc("drift_watcher_snapshots_total", {"market": "SOL-PERP"})
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert 'drift_watcher_snapshots_total{market="SOL-PERP"} 2' in body
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_http_server_404_on_unknown_path():
    server = start_metrics_server(MetricsRegistry(), "127.0.0.1", 0)
    try:
        port = server.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
