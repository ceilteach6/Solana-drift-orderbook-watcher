"""
tests/test_hyperliquid_feed.py

Unit tests for the Hyperliquid REST feed and its wiring into create_feed().
Network-free: urllib.request.urlopen is monkeypatched, never actually called.
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.collector.hyperliquid_feed import HyperliquidOrderbookFeed, _parse_l2
from src.collector.orderbook_feed import SyntheticOrderbookFeed, create_feed


def _settings(**overrides):
    base = dict(
        network="hyperliquid",
        markets=["BTC", "ETH"],
        snapshot_timeout_sec=5.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_parse_l2_orders_asks_ascending_and_bids_descending():
    raw = {
        "levels": [
            [{"px": "43010", "sz": "1"}, {"px": "43000", "sz": "2"}],  # asks, out of order
            [{"px": "42990", "sz": "3"}, {"px": "43000", "sz": "1"}],  # bids, out of order
        ]
    }
    snap = _parse_l2("BTC", raw)
    assert [lvl.price for lvl in snap.asks] == [43000.0, 43010.0]
    assert [lvl.price for lvl in snap.bids] == [43000.0, 42990.0]


def test_parse_l2_returns_none_on_malformed_payload():
    assert _parse_l2("BTC", {}) is None
    assert _parse_l2("BTC", {"levels": [[]]}) is None


def test_parse_l2_skips_unparsable_levels_without_raising():
    raw = {"levels": [[{"px": "bad", "sz": "1"}], [{"sz": "1"}]]}
    snap = _parse_l2("BTC", raw)
    assert snap.asks == []
    assert snap.bids == []


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_snapshot_strips_perp_suffix_and_hits_the_api(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        import json

        captured["payload"] = json.loads(req.data)
        return _FakeResponse({"levels": [[{"px": "100", "sz": "1"}], [{"px": "99", "sz": "1"}]]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    feed = HyperliquidOrderbookFeed(_settings())
    snap = asyncio.run(feed.get_snapshot("BTC-PERP"))
    asyncio.run(feed.close())

    assert captured["payload"] == {"type": "l2Book", "coin": "BTC"}
    assert snap is not None
    assert snap.market == "BTC-PERP"


def test_get_snapshot_returns_none_and_does_not_raise_on_request_failure(monkeypatch):
    def fake_urlopen(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    feed = HyperliquidOrderbookFeed(_settings())
    snap = asyncio.run(feed.get_snapshot("BTC"))
    asyncio.run(feed.close())

    assert snap is None


def test_connect_raises_when_api_unreachable(monkeypatch):
    def fake_urlopen(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    feed = HyperliquidOrderbookFeed(_settings())
    with pytest.raises(RuntimeError, match="unreachable"):
        asyncio.run(feed.connect())


def test_create_feed_falls_back_to_synthetic_when_hyperliquid_unreachable(monkeypatch):
    def fake_urlopen(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    feed = asyncio.run(create_feed(_settings()))
    assert isinstance(feed, SyntheticOrderbookFeed)


def test_create_feed_uses_hyperliquid_feed_when_reachable(monkeypatch):
    def fake_urlopen(req, timeout):
        return _FakeResponse({"universe": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    feed = asyncio.run(create_feed(_settings()))
    assert isinstance(feed, HyperliquidOrderbookFeed)
    asyncio.run(feed.close())
