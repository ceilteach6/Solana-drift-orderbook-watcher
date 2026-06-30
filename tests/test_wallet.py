"""
tests/test_wallet.py

Tests the wallet monitor: churn, repeated-size, multi-market scoring, the alert
gate (threshold + cooldown), activity ranking, and the storage upsert/top query.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import Order
from src.storage import SQLiteStore
from src.wallet.monitor import WalletMonitor


def make_settings(**overrides):
    base = dict(
        wallet_window_sec=100.0,
        wallet_churn_ratio=0.8,
        wallet_repeat_min=6,
        wallet_multi_market=3,
        wallet_alert_threshold=0.7,
        wallet_alert_cooldown_sec=60.0,
        repeated_size_tolerance=0.001,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_churn_raises_suspicion():
    mon = WalletMonitor(make_settings())
    # A wallet that re-prices every tick -> placements + cancels (churn).
    for t in range(6):
        orders = [Order("BOT", "bid", 100.0 + t, 10.0),
                  Order("BOT", "ask", 200.0 + t, 10.0)]
        mon.update("SOL-PERP", float(t), orders)
    stat = mon._stats["BOT"]
    assert stat.cancels  # orders from prior ticks were "cancelled"
    assert stat.score > 0


def test_stable_wallet_low_suspicion():
    mon = WalletMonitor(make_settings(wallet_alert_threshold=0.7))
    # Identical two orders every tick -> no churn, single market.
    for t in range(6):
        orders = [Order("HODL", "bid", 100.0, 1.0),
                  Order("HODL", "ask", 101.0, 2.0)]
        alerts = mon.update("SOL-PERP", float(t), orders)
    assert mon._stats["HODL"].score < 0.7
    assert alerts == []


def test_alert_fires_and_cooldown_suppresses():
    mon = WalletMonitor(make_settings(wallet_alert_threshold=0.1,
                                      wallet_alert_cooldown_sec=50))
    fired = []
    for t in range(4):
        orders = [Order("BOT", "bid", 100.0 + t, 10.0)]
        fired += mon.update("SOL-PERP", float(t), orders)
    assert len(fired) == 1  # one alert, rest suppressed by cooldown
    assert fired[0].details["wallet"] == "BOT"


def test_top_wallets_ranks_by_activity():
    mon = WalletMonitor(make_settings())
    for t in range(5):
        mon.update("SOL-PERP", float(t), [
            Order("BUSY", "bid", 100.0 + t, 5.0),
            Order("BUSY", "ask", 200.0 + t, 5.0),
        ])
        mon.update("SOL-PERP", float(t), [Order("QUIET", "bid", 50.0, 1.0)])
    top = mon.top_wallets(2)
    assert top[0].wallet == "BUSY"


def test_multi_market_contributes():
    mon = WalletMonitor(make_settings(wallet_multi_market=3))
    for t in range(3):
        mon.update("SOL-PERP", float(t), [Order("MM", "bid", 100.0, 1.0)])
        mon.update("BTC-PERP", float(t), [Order("MM", "bid", 200.0, 1.0)])
        mon.update("ETH-PERP", float(t), [Order("MM", "bid", 300.0, 1.0)])
    assert len(mon._stats["MM"].markets()) == 3


def test_wallet_storage_roundtrip(tmp_path):
    mon = WalletMonitor(make_settings())
    for t in range(4):
        mon.update("SOL-PERP", float(t), [Order("BOT", "bid", 100.0 + t, 10.0)])

    store = SQLiteStore(str(tmp_path / "w.db"))
    store.connect()
    for stat in mon.top_wallets(10):
        store.upsert_wallet(stat, 4.0)
    rows = store.top_wallets(10)
    assert any(r["wallet"] == "BOT" for r in rows)
    # upsert again -> still one row per wallet
    for stat in mon.top_wallets(10):
        store.upsert_wallet(stat, 5.0)
    assert len(store.top_wallets(10)) == 1
    store.close()
