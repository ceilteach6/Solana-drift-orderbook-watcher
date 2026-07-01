"""
tests/test_dashboard_server.py

Regression tests for the dashboard HTTP server:

1. The handler must reuse one shared SQLiteStore connection across requests
   instead of opening a fresh connection (and re-running the full schema DDL)
   on every single request.
2. BoundedThreadingHTTPServer must cap how many requests can be processed
   concurrently, since the stdlib ThreadingHTTPServer spawns one OS thread
   per connection with no limit of its own.
"""

import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from src.dashboard.server import BoundedThreadingHTTPServer, _make_handler
from src.storage import SQLiteStore


def test_handler_reuses_one_shared_connection(tmp_path, monkeypatch):
    db_path = str(tmp_path / "watcher.db")
    store = SQLiteStore(db_path)

    connect_calls = []
    orig_connect = SQLiteStore.connect

    def counting_connect(self):
        connect_calls.append(1)
        orig_connect(self)

    monkeypatch.setattr(SQLiteStore, "connect", counting_connect)
    store.connect()
    assert len(connect_calls) == 1

    handler = _make_handler(store, threading.Lock())
    server = BoundedThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        for _ in range(5):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/markets", timeout=2
            ) as resp:
                assert resp.status == 200
        # The handler must not open a new connection per request.
        assert len(connect_calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_bounded_threading_http_server_limits_concurrent_processing(monkeypatch):
    max_concurrent = 2
    server = BoundedThreadingHTTPServer.__new__(BoundedThreadingHTTPServer)
    server._concurrency = threading.BoundedSemaphore(max_concurrent)

    lock = threading.Lock()
    state = {"current": 0, "max_seen": 0}

    def fake_process_request_thread(self, request, client_address):
        with lock:
            state["current"] += 1
            state["max_seen"] = max(state["max_seen"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1

    monkeypatch.setattr(ThreadingHTTPServer, "process_request_thread", fake_process_request_thread)

    threads = [
        threading.Thread(target=server.process_request_thread, args=(None, None))
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert state["max_seen"] <= max_concurrent
