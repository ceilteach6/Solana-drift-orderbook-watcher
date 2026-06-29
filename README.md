# 🔭 Perp Orderbook Watcher & Bot Detector

Open-source, **read-only** orderbook watcher and bot detector for perpetual
markets — currently supporting **Drift Protocol** (Solana) and
**Hyperliquid** (their own L1).

Both venues expose a real L2 orderbook, which makes them suitable for genuine
orderbook-pattern analysis. A single normalized detector stack runs on both.

> **⚠️ DISCLAIMER:** Experimental software for educational/research purposes.
> Bot detection is heuristic — false positives/negatives are possible. This is
> NOT financial advice. This version is **read-only** (it only watches and alerts);
> it does not trade and does not move funds.

---

## 🎯 What it does

1. **Orderbook collector** — L2 orderbook from Drift DLOB (`driftpy` SDK) or Hyperliquid REST API
2. **Bot detector** — identifies suspicious patterns:
   - Repeated order sizes (bot signature)
   - Layering / spoofing-like walls
   - Order flicker (rapid appear/disappear)
   - Bid/ask imbalance surges
   - Spoof-and-pull moves
3. **Alert** — console / Telegram / Discord / webhook alerts (read-only, no intervention)
4. **Persistence** — optional SQLite storage for replay and analysis

---

## 🌐 Supported networks

| Network | Venue | Orderbook type | Integration | Extras needed |
|---|---|---|---|---|
| Solana | Drift Protocol | Real on-chain DLOB | `driftpy` SDK (WebSocket) | Solana RPC URL |
| Hyperliquid L1 | Hyperliquid | Central limit order book | Public REST API | Nothing — stdlib only |

**Why not Jupiter Perps?** Pool-based (JLP) — no real orderbook to analyze.

Switch networks by setting `NETWORK=hyperliquid` (or `NETWORK=drift`, the default) in `.env`.

---

## 🏗️ Architecture

```
drift-orderbook-watcher/
├── src/
│   ├── collector/
│   │   ├── drift_client.py        # DriftClient setup + DLOB connection
│   │   ├── hyperliquid_feed.py    # Hyperliquid REST feed (NEW)
│   │   └── orderbook_feed.py      # Normalized L2 model + feed factory
│   ├── detector/
│   │   ├── base.py                # Detector base class
│   │   ├── repeated_size.py       # Repeated-size detector
│   │   ├── layering.py            # Layering/spoofing detector
│   │   ├── flicker.py             # Order flicker detector
│   │   ├── imbalance.py           # Bid/ask imbalance detector
│   │   └── spoof_pull.py          # Spoof-and-pull detector
│   ├── alert/
│   │   ├── base.py                # Alert sink base class
│   │   ├── console_alert.py       # Console / JSON output
│   │   └── webhook_alert.py       # Telegram / Discord / generic webhook
│   ├── storage/
│   │   └── sqlite_store.py        # SQLite detection persistence
│   ├── risk_aggregator.py         # EMA-smoothed composite risk score
│   └── watcher.py                 # Main orchestrator
├── config/
│   └── settings.py                # Configuration (from env / .env)
├── main.py                        # Entry point
├── config.example.env             # Config template
├── requirements.txt
├── LICENSE                        # GPLv3
└── README.md
```

---

## ⚡ Quick start

### Prerequisites
- Python **3.10+**
- For **Drift**: a Solana RPC endpoint (e.g. free Helius)
- For **Hyperliquid**: nothing extra — uses the public REST API

### Install
```bash
git clone https://github.com/<username>/drift-orderbook-watcher.git
cd drift-orderbook-watcher
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configure
```bash
cp config.example.env .env
# Drift (default): set RPC_URL
# Hyperliquid:     set NETWORK=hyperliquid (no RPC URL needed)
```

### Run (read-only)
```bash
# Drift (default)
python main.py

# Hyperliquid — watch BTC, ETH and SOL perps
NETWORK=hyperliquid MARKETS=BTC,ETH,SOL python main.py
```

---

## 🔧 How the Drift integration works

The `driftpy` SDK provides the orderbook in two layers:

1. **OrderSubscriber** — subscribes to all open user orders over WebSocket.
   This is the raw data feed the DLOB is built from.
2. **DLOBSubscriber** — builds and maintains the aggregated L2 orderbook from it.

```python
from driftpy.dlob.dlob_subscriber import DLOBSubscriber
from driftpy.dlob.client_types import DLOBClientConfig
from driftpy.slot.slot_subscriber import SlotSubscriber

slot_subscriber = SlotSubscriber(drift_client)
config = DLOBClientConfig(drift_client, user_map, slot_subscriber, 1)
dlob_subscriber = DLOBSubscriber(config=config)
await dlob_subscriber.subscribe()

# Fetch the L2 orderbook (e.g. SOL-PERP)
l2 = dlob_subscriber.get_l2_orderbook_sync("SOL-PERP")
```

The watcher feeds this L2 snapshot to the detectors for analysis.

---

## 🧩 Extensibility — writing your own detector

Every detector derives from `BaseDetector`. A new detector:

```python
from src.detector.base import BaseDetector, Detection

class MyDetector(BaseDetector):
    name = "my_detector"

    def analyze(self, snapshot, history) -> list[Detection]:
        detections = []
        # ... your logic ...
        return detections
```

Then register it in `watcher.py`. That's it.

---

## 🛡️ Security

- **Read-only**: the watcher never writes to the chain and holds no trading key.
- Watching does **not** require a private key — an empty/read wallet is enough to subscribe.
- If you do supply a key (for future extensions), it goes in `.env`, which `.gitignore` excludes.

---

## 🗺️ Roadmap

- [x] Watch multiple markets in parallel (SOL-PERP, BTC-PERP, ETH-PERP)
- [x] Telegram/Discord / generic webhook alerts
- [x] SQLite detection persistence and replay queries
- [x] **Hyperliquid network support** (BTC, ETH, SOL, ARB, … via public REST API)
- [ ] ML-based anomaly detection alongside the heuristics
- [ ] Wallet-level reputation / blocklist (optional module)
- [ ] Prometheus metrics endpoint
- [ ] dYdX v4 / Phoenix DEX feed adapters

---

## 🤝 Contributing

See `CONTRIBUTING.md`. Fork → feature branch → PR.

## 📜 License

GNU General Public License v3.0 — see `LICENSE`.
The Drift Protocol is itself open source, which makes further development easier.
