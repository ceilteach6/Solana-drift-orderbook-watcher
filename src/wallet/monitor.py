"""
src/wallet/monitor.py

Wallet-level (maker) monitoring. The Drift L2 book is built from individual
wallets' orders; this module attributes activity back to those wallets and
scores how bot-/manipulation-like each one looks.

Per wallet, over a rolling window (``WALLET_WINDOW_SEC``), it tracks:
- **churn**     — order placements vs. cancels (place-then-cancel = spoof-like)
- **repeated**  — fraction of orders sharing one dominant size (bot signature)
- **markets**   — simultaneous presence across markets (coordination)

These combine (noisy-OR) into a 0–1 suspicion score; wallets crossing
``WALLET_ALERT_THRESHOLD`` raise an alert (with a per-wallet cooldown).
``top_wallets`` ranks by raw activity, for a "top N active wallets" view.

Fed one market's per-wallet orders per tick via :meth:`update`. Order identity
is approximated by (side, price, size); an order vanishing between ticks counts
as a cancel, a new one as a placement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from src.detector.base import Detection, cluster_sizes


@dataclass
class WalletStat:
    wallet: str
    places: deque = field(default_factory=deque)        # timestamps
    cancels: deque = field(default_factory=deque)        # timestamps
    sizes: deque = field(default_factory=deque)          # (ts, size)
    market_hits: deque = field(default_factory=deque)    # (ts, market)
    last_sigs: dict = field(default_factory=dict)        # market -> set(sig)
    last_alert: float = float("-inf")
    score: float = 0.0

    def activity(self) -> int:
        return len(self.places) + len(self.cancels)

    def markets(self) -> set:
        return {m for _, m in self.market_hits}


class WalletMonitor:
    name = "wallet"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._stats: dict[str, WalletStat] = {}

    def update(self, market: str, ts: float, orders) -> list[Detection]:
        """Process one market's per-wallet orders; return wallet alerts."""
        window = self.settings.wallet_window_sec

        # Group this tick's orders by wallet.
        by_wallet: dict[str, list] = {}
        for o in orders:
            by_wallet.setdefault(o.wallet, []).append(o)

        detections: list[Detection] = []
        for wallet, wallet_orders in by_wallet.items():
            stat = self._stats.get(wallet)
            if stat is None:
                stat = self._stats[wallet] = WalletStat(wallet)

            cur_sigs = {
                (o.side, round(o.price, 4), round(o.size, 4)) for o in wallet_orders
            }
            prev_sigs = stat.last_sigs.get(market, set())
            placements = cur_sigs - prev_sigs
            cancels = prev_sigs - cur_sigs
            stat.last_sigs[market] = cur_sigs

            for _ in placements:
                stat.places.append(ts)
            for _ in cancels:
                stat.cancels.append(ts)
            for o in wallet_orders:
                stat.sizes.append((ts, o.size))
            stat.market_hits.append((ts, market))

            self._prune(stat, ts - window)

            stat.score = self._score(stat)
            detection = self._maybe_alert(stat, market, ts)
            if detection is not None:
                detections.append(detection)
        return detections

    def top_wallets(self, n: int = 10) -> list[WalletStat]:
        return sorted(self._stats.values(), key=lambda s: s.activity(), reverse=True)[:n]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _prune(stat: WalletStat, cutoff: float) -> None:
        while stat.places and stat.places[0] < cutoff:
            stat.places.popleft()
        while stat.cancels and stat.cancels[0] < cutoff:
            stat.cancels.popleft()
        while stat.sizes and stat.sizes[0][0] < cutoff:
            stat.sizes.popleft()
        while stat.market_hits and stat.market_hits[0][0] < cutoff:
            stat.market_hits.popleft()

    def _score(self, stat: WalletStat) -> float:
        places = len(stat.places)
        cancels = len(stat.cancels)

        churn_rate = cancels / places if places else 0.0
        churn = min(1.0, churn_rate / max(self.settings.wallet_churn_ratio, 1e-9))

        sizes = [s for _, s in stat.sizes]
        repeated = 0.0
        if len(sizes) >= self.settings.wallet_repeat_min:
            clusters = cluster_sizes(sizes, self.settings.repeated_size_tolerance)
            dominant = clusters[0].count if clusters else 0
            repeated = max(0.0, (dominant / len(sizes) - 0.5) * 2)

        distinct = len(stat.markets())
        span = max(self.settings.wallet_multi_market - 1, 1)
        multi = min(1.0, max(0, distinct - 1) / span)

        product = (1 - churn) * (1 - repeated) * (1 - multi)
        return 1 - product

    def _maybe_alert(self, stat: WalletStat, market: str, ts: float):
        if stat.score < self.settings.wallet_alert_threshold:
            return None
        if ts - stat.last_alert < self.settings.wallet_alert_cooldown_sec:
            return None
        stat.last_alert = ts
        return Detection(
            detector=self.name,
            market=market,
            score=min(stat.score, 1.0),
            message=(
                f"Wallet {stat.wallet} suspicion {stat.score:.2f} — "
                f"{len(stat.places)} placements / {len(stat.cancels)} cancels, "
                f"{len(stat.markets())} markets"
            ),
            details={
                "wallet": stat.wallet,
                "placements": len(stat.places),
                "cancels": len(stat.cancels),
                "markets": sorted(stat.markets()),
                "activity": stat.activity(),
            },
        )
