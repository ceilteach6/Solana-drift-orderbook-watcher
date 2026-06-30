"""
tests/test_alerts.py

Webhook delivery must never spawn unbounded OS threads — a burst of
qualifying detections (the exact scenario this tool exists to catch) used to
spawn one raw thread per call with no cap. The bounded pool must still
actually deliver every request.
"""

import http.server
import json
import threading
from types import SimpleNamespace

from src.alert.webhook_alert import WebhookAlert
from src.detector.base import Detection


def _start_capture_server():
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append(json.loads(body))
            self.send_response(200)
            self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, received


def test_webhook_pool_is_bounded():
    settings = SimpleNamespace(
        alert_webhook_url="http://127.0.0.1:1/unused",
        telegram_bot_token="", telegram_chat_id="",
    )
    alert = WebhookAlert(settings)
    assert alert._executor._max_workers == WebhookAlert._MAX_WORKERS


def test_webhook_delivers_a_burst_via_the_bounded_pool():
    server, received = _start_capture_server()
    try:
        port = server.server_address[1]
        settings = SimpleNamespace(
            alert_webhook_url=f"http://127.0.0.1:{port}/hook",
            telegram_bot_token="", telegram_chat_id="",
        )
        alert = WebhookAlert(settings)
        detection = Detection(detector="flicker", market="SOL-PERP", score=0.9, message="x")

        for _ in range(10):  # burst — must not raise or block the caller
            alert.deliver(detection)
        alert._executor.shutdown(wait=True)

        assert len(received) == 10
        assert received[0]["content"] == alert._text(detection)
    finally:
        server.shutdown()
