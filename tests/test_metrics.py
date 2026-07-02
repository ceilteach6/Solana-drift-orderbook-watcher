"""
tests/test_metrics.py

Covers the Prometheus text-exposition rendering (format, HELP/TYPE dedup,
label handling) and the tiny HTTP server end-to-end.
"""

import threading
import urllib.error
import urllib.request

import pytest

from src.metrics import MetricsRegistry, start_metrics_server


def test_counter_accumulates_by_label_set():
    reg = MetricsRegistry()
    reg.inc("watcher_ticks_total", {"market": "SOL-PERP"})
    reg.inc("watcher_ticks_total", {"market": "SOL-PERP"})
    reg.inc("watcher_ticks_total", {"market": "BTC-PERP"})

    body = reg.render()
    assert 'watcher_ticks_total{market="SOL-PERP"} 2.0' in body
    assert 'watcher_ticks_total{market="BTC-PERP"} 1.0' in body


def test_gauge_is_overwritten_not_accumulated():
    reg = MetricsRegistry()
    reg.set("watcher_risk_score", {"market": "SOL-PERP"}, 0.3)
    reg.set("watcher_risk_score", {"market": "SOL-PERP"}, 0.7)

    body = reg.render()
    assert 'watcher_risk_score{market="SOL-PERP"} 0.7' in body
    assert "0.3" not in body


def test_help_and_type_lines_appear_exactly_once_per_metric_name():
    reg = MetricsRegistry()
    reg.inc("watcher_detections_total", {"market": "SOL-PERP", "detector": "flicker"})
    reg.inc("watcher_detections_total", {"market": "SOL-PERP", "detector": "imbalance"})

    body = reg.render()
    assert body.count("# TYPE watcher_detections_total counter") == 1
    assert body.count("# HELP watcher_detections_total") == 1


def test_render_is_valid_even_when_empty():
    assert MetricsRegistry().render() == ""


def test_unlabeled_metric_omits_braces():
    reg = MetricsRegistry()
    reg.inc("watcher_ticks_total")
    assert "watcher_ticks_total 1.0" in reg.render()


@pytest.fixture
def server():
    reg = MetricsRegistry()
    srv = start_metrics_server(reg, "127.0.0.1", 0)
    try:
        yield reg, srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_metrics_endpoint_serves_the_registry(server):
    reg, srv = server
    reg.inc("watcher_ticks_total", {"market": "SOL-PERP"}, 5)

    with urllib.request.urlopen(
        f"http://127.0.0.1:{srv.server_port}/metrics", timeout=5
    ) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/plain")
        body = resp.read().decode("utf-8")
    assert 'watcher_ticks_total{market="SOL-PERP"} 5.0' in body


def test_unknown_path_is_404(server):
    _, srv = server
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/other", timeout=5)
    assert excinfo.value.code == 404


def test_registry_is_thread_safe_under_concurrent_increments():
    reg = MetricsRegistry()

    def bump():
        for _ in range(200):
            reg.inc("watcher_ticks_total", {"market": "SOL-PERP"})

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert 'watcher_ticks_total{market="SOL-PERP"} 1600.0' in reg.render()
