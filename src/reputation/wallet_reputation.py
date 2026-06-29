"""
src/reputation/wallet_reputation.py

EMA-based per-wallet reputation tracker.

When a detector adds ``"maker": "<wallet_address>"`` to a Detection's
``details`` dict, this module accumulates an exponential moving average
reputation score for that wallet.  Wallets that repeatedly trigger
high-score detections automatically cross the ``REPUTATION_BLOCK_THRESHOLD``
and are flagged as blocked — optionally persisted to SQLite via
:class:`~src.storage.sqlite_store.SqliteStore`.

Configuration (env vars):

    BLOCKLIST_WALLETS=<addr1>,<addr2>   # comma-separated hard-block list
    REPUTATION_DECAY=0.90               # EMA decay (0=fast rise, 1=slow)
    REPUTATION_BLOCK_THRESHOLD=0.75     # auto-block above this score

Integration::

    rep = WalletReputation(settings, store)
    rep.load()                           # once at startup (loads from SQLite)

    # inside the tick loop, after detections:
    for det in detections:
        maker = det.details.get("maker")
        if maker:
            score = rep.record(maker, det.score)
            if rep.is_blocked(maker):
                logger.warning("Blocked maker %s (rep=%.3f)", maker, score)

Query helpers::

    rep.top_suspects(n=10)   # list of dicts: wallet, score, hit_count, blocked
    rep.get_score(wallet)    # float 0.0–1.0
    rep.is_blocked(wallet)   # bool
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class WalletReputation:
    """EMA reputation scorer for maker wallet addresses.

    - Scores accumulate with each detection (slow decay → slow fall-off).
    - A wallet is *blocked* when its score reaches ``block_threshold`` OR
      it appears in the manual ``blocklist_wallets`` list.
    - Everything is a no-op when detections carry no ``"maker"`` key — the
      system degrades gracefully until L3 order data is wired up.
    """

    def __init__(self, settings, store=None) -> None:
        raw = getattr(settings, "blocklist_wallets", []) or []
        self._blocklist: set[str] = {w.strip() for w in raw if w.strip()}
        self._decay: float = float(getattr(settings, "reputation_decay", 0.90))
        self._block_threshold: float = float(
            getattr(settings, "reputation_block_threshold", 0.75)
        )
        self._scores: dict[str, float] = {}
        self._hits: dict[str, int] = {}
        self._store = store  # SqliteStore | None — optional persistence

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """Restore persisted wallet scores from SQLite on startup."""
        if self._store is None:
            return
        for row in self._store.load_wallets():
            wallet = row["wallet"]
            self._scores[wallet] = row["score"]
            self._hits[wallet] = row["hit_count"]
            if row["blocked"]:
                self._blocklist.add(wallet)
        if self._scores:
            logger.info(
                "Wallet reputation: loaded %d wallets from storage (%d blocked)",
                len(self._scores),
                sum(1 for w in self._scores if self.is_blocked(w)),
            )

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    def record(self, wallet: str, detection_score: float) -> float:
        """Update EMA reputation for *wallet* given one detection.

        Uses a decaying accumulator:
            new = decay * old + (1 - decay) * detection_score

        Returns the new reputation score.
        """
        prev = self._scores.get(wallet, 0.0)
        was_auto_blocked = prev >= self._block_threshold
        new_score = self._decay * prev + (1.0 - self._decay) * float(detection_score)
        self._scores[wallet] = new_score
        self._hits[wallet] = self._hits.get(wallet, 0) + 1

        if self._store is not None:
            self._store.upsert_wallet(
                wallet,
                new_score,
                self._hits[wallet],
                wallet in self._blocklist,
            )

        # Log only on the rising edge — not every tick after threshold is crossed.
        if not was_auto_blocked and new_score >= self._block_threshold and wallet not in self._blocklist:
            logger.info(
                "Wallet reputation: %s auto-blocked (score=%.3f, hits=%d)",
                wallet,
                new_score,
                self._hits[wallet],
            )

        return new_score

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #

    def get_score(self, wallet: str) -> float:
        """Current EMA reputation score (0.0 = clean, 1.0 = very suspicious)."""
        return self._scores.get(wallet, 0.0)

    def hit_count(self, wallet: str) -> int:
        """Number of detections recorded for *wallet*."""
        return self._hits.get(wallet, 0)

    def is_blocked(self, wallet: str) -> bool:
        """True when wallet is manually blocklisted OR score ≥ block_threshold."""
        return wallet in self._blocklist or self._scores.get(wallet, 0.0) >= self._block_threshold

    def block(self, wallet: str) -> None:
        """Permanently add *wallet* to the manual blocklist."""
        self._blocklist.add(wallet)
        if self._store is not None:
            self._store.upsert_wallet(
                wallet,
                self._scores.get(wallet, 0.0),
                self._hits.get(wallet, 0),
                True,
            )
        logger.info("Wallet reputation: %s manually blocked", wallet)

    def top_suspects(self, n: int = 10) -> list[dict]:
        """Return the top *n* wallets ranked by reputation score (descending).

        Each entry: ``{wallet, score, hit_count, blocked}``.
        """
        ranked = sorted(self._scores.items(), key=lambda kv: kv[1], reverse=True)
        return [
            {
                "wallet": w,
                "score": round(s, 6),
                "hit_count": self._hits.get(w, 0),
                "blocked": self.is_blocked(w),
            }
            for w, s in ranked[:n]
        ]

    def __len__(self) -> int:
        """Number of wallets with any recorded reputation data."""
        return len(self._scores)
