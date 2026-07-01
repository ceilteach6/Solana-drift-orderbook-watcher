# 🔭 Drift Orderbook Watcher & Bot Detector

Open-source, **read-only** orderbook watcher and bot detector for the
[Drift Protocol](https://drift.trade) leverage (perpetual) markets on Solana.

Drift is one of the few Solana-native perp DEXes with a **real on-chain orderbook**
(DLOB – Decentralized Limit Order Book), which makes it suitable for genuine
orderbook analysis.

> **⚠️ DISCLAIMER:** Experimental software for educational/research purposes.
> Bot detection is heuristic — false positives/negatives are possible. This is
> NOT financial advice. This version is **read-only** (it only watches and alerts);
> it does not trade and does not move funds.

---

## 🎯 What it does

1. **Orderbook collector** — real-time L2 orderbook from the Drift DLOB (`driftpy` SDK)
2. **Bot detector** — identifies suspicious patterns:
   - Repeated order sizes (bot signature)
   - Layering / spoofing-like walls
   - Order flicker (rapid appear/disappear)
3. **Alert** — console/JSON alerts for suspicious activity (read-only, no intervention)

---

## 🧱 Why Drift and not Jupiter?

| Aspect | Drift | Jupiter Perps |
|---|---|---|
| Orderbook type | Real on-chain DLOB | Pool-based (JLP), no orderbook |
| SDK | `driftpy` (official Python) | Partial (WIP) |
| Orderbook analysis | ✅ Possible | ❌ Nothing to analyze |
| Open source | ✅ Fully | Partial (routing closed) |

For orderbook watching, Drift is the logical choice.

---

## 🏗️ Architecture

```
drift-orderbook-watcher/
├── src/
│   ├── collector/
│   │   ├── drift_client.py      # DriftClient setup + connection
│   │   └── orderbook_feed.py    # DLOB subscribe, L2 orderbook stream
│   ├── detector/
│   │   ├── base.py              # Detector base class
│   │   ├── repeated_size.py     # Repeated-size detector
│   │   ├── layering.py          # Layering/spoofing detector
│   │   └── flicker.py           # Order flicker (rapid appear/disappear)
│   ├── alert/
│   │   └── console_alert.py     # Alert output (console / JSON)
│   └── watcher.py               # Main orchestrator
├── config/
│   └── settings.py              # Configuration (from env)
├── examples/
│   └── quickstart.py            # Minimal example
├── tests/
│   └── test_detectors.py        # Unit tests (mock orderbook)
├── main.py                      # Entry point
├── config.example.env           # Config template
├── requirements.txt
├── LICENSE                      # GPLv3
└── README.md
```

---

## ⚡ Quick start

### Prerequisites
- Python **3.10+** (required by driftpy)
- A Solana RPC endpoint (recommended: free Helius with a verified email)

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
# Edit .env: RPC_URL is required
```

### Run (read-only)
```bash
python main.py
```

### Self-test (no network)
Verify every detector fires on a known manipulation pattern — useful before
going live with a real RPC:
```bash
python main.py --selftest
```
Set `HEALTHCHECK_ENABLED=true` to re-run this check periodically while watching,
alerting if any detector stops firing.

### Persist the time-series (optional)
Set `STORAGE_ENABLED=true` to record detections and risk scores to SQLite
(`DB_PATH`, default `data/watcher.db`). Inspect what was collected:
```bash
python main.py --dbstats
```
This is the foundation for replay, analytics, and a charting dashboard.

### Dashboard (TradingView Lightweight Charts)
With storage enabled and some data collected, serve the chart UI:
```bash
python main.py --dashboard      # then open http://127.0.0.1:8787
```
Shows the mid price with detection markers and a risk-level panel, reading
straight from the SQLite store (the watcher can keep writing concurrently).

### Replay / backtesting (threshold tuning)
With `PERSIST_SNAPSHOTS=true`, the watcher stores the full L2 books, which can
then be replayed offline through the real detector stack — no network, using
the recorded timestamps:
```bash
python main.py --replay                          # every stored market
python main.py --replay --market SOL-PERP --since 2026-07-01
```
To tune a threshold, sweep any numeric setting over several values and compare
what each would have produced on the same recorded data:
```bash
python main.py --replay --sweep risk_alert_threshold=0.4,0.5,0.6,0.7
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

## 🗺️ Roadmap (extension ideas)

- [ ] Watch multiple markets in parallel (SOL-PERP, BTC-PERP, ETH-PERP)
- [ ] Telegram/Discord alerts
- [x] Time-series storage (SQLite) and replay/backtesting with threshold sweeps
- [ ] ML-based anomaly detection alongside the heuristics
- [ ] Wallet-level reputation / blocklist (optional module)
- [ ] Prometheus metrics (modeled on the Drift-style exporter)

---

## 🤝 Contributing

See `CONTRIBUTING.md`. Fork → feature branch → PR.

## 📜 License

GNU General Public License v3.0 — see `LICENSE`.
The Drift Protocol is itself open source, which makes further development easier.
