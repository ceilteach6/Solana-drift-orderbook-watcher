"""
src/storage/sqlite_store.py

SQLite-backed detection store.

Enable by setting ``DB_PATH`` in your .env (e.g. ``DB_PATH=drift_watcher.db``).
Leave it empty (the default) to run without any persistence — the store
becomes a no-op so nothing else in the codebase needs to change.

Schema (single table, append-only):

    detections(id, ts, market, detector, score, message, details)

The store is thread-safe (one connection per process, ``check_same_thread=False``
is safe for our single-writer pattern). The async helper wraps the blocking
call in ``asyncio.to_thread`` so it never blocks the event loop.

Query helper::

    rows = store.query_recent(market="SOL-PERP", limit=50)
    for r in rows:
        print(r["ts"], r["detector"], r["score"])
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe_json(obj) -> str:
    """Serialize to JSON, falling back to repr() on non-serializable objects."""
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        logger.debug("Details not JSON-serializable, using repr fallback: %r", obj)
        return json.dumps(repr(obj))

_DDL = """
CREATE TABLE IF NOT EXISTS detections (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    market    TEXT    NOT NULL,
    detector  TEXT    NOT NULL,
    score     REAL    NOT NULL,
    message   TEXT    NOT NULL,
    details   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_det_ts     ON detections(ts);
CREATE INDEX IF NOT EXISTS idx_det_market ON detections(market);

CREATE TABLE IF NOT EXISTS wallet_reputation (
    wallet    TEXT    PRIMARY KEY,
    score     REAL    NOT NULL DEFAULT 0.0,
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_seen REAL    NOT NULL,
    blocked   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rep_score ON wallet_reputation(score DESC);
"""


class SqliteStore:
    """Append-only SQLite store for :class:`Detection` objects.

    Pass ``db_path=""`` (or leave ``DB_PATH`` unset) to get a no-op instance
    that silently discards all writes — safe to call unconditionally.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def open(self) -> None:
        """Open (or create) the database and apply the schema."""
        if not self._db_path:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.commit()
        logger.info("SQLite store opened: %s", self._db_path)

    def close(self) -> None:
        """Flush and close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.debug("Error closing SQLite connection", exc_info=True)
            finally:
                self._conn = None

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #

    def save_detection(self, detection) -> None:
        """Persist one detection synchronously. No-op when DB is disabled."""
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT INTO detections(ts, market, detector, score, message, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                detection.market,
                detection.detector,
                float(detection.score),
                detection.message,
                _safe_json(detection.details),
            ),
        )
        self._conn.commit()

    async def async_save_detection(self, detection) -> None:
        """Non-blocking wrapper — runs ``save_detection`` in a thread pool."""
        await asyncio.to_thread(self.save_detection, detection)

    def save_detections(self, detections) -> None:
        """Batch-persist multiple detections in a single transaction."""
        if self._conn is None or not detections:
            return
        now = time.time()
        self._conn.executemany(
            "INSERT INTO detections(ts, market, detector, score, message, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    now,
                    d.market,
                    d.detector,
                    float(d.score),
                    d.message,
                    json.dumps(d.details),
                )
                for d in detections
            ],
        )
        self._conn.commit()

    async def async_save_detections(self, detections) -> None:
        """Non-blocking batch write."""
        await asyncio.to_thread(self.save_detections, detections)

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #

    def query_recent(
        self,
        *,
        market: str | None = None,
        detector: str | None = None,
        min_score: float = 0.0,
        limit: int = 100,
    ) -> list[dict]:
        """Return the most recent detections, newest first.

        Args:
            market:    Filter to a specific market (e.g. ``"SOL-PERP"``).
            detector:  Filter to a specific detector name.
            min_score: Only return rows with score >= this value.
            limit:     Maximum number of rows returned.

        Returns:
            List of dicts with keys: id, ts, market, detector, score,
            message, details (already deserialized from JSON).
        """
        if self._conn is None:
            return []

        clauses = ["score >= ?"]
        params: list = [min_score]

        if market:
            clauses.append("market = ?")
            params.append(market)
        if detector:
            clauses.append("detector = ?")
            params.append(detector)

        where = " AND ".join(clauses)
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT id, ts, market, detector, score, message, details "
            f"FROM detections WHERE {where} ORDER BY ts DESC LIMIT ?",
            params,
        ).fetchall()

        return [
            {
                "id": r[0],
                "ts": r[1],
                "market": r[2],
                "detector": r[3],
                "score": r[4],
                "message": r[5],
                "details": json.loads(r[6]),
            }
            for r in rows
        ]

    def count(self, *, market: str | None = None) -> int:
        """Total number of stored detections (optionally filtered by market)."""
        if self._conn is None:
            return 0
        if market:
            return self._conn.execute(
                "SELECT COUNT(*) FROM detections WHERE market = ?", (market,)
            ).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]

    # ------------------------------------------------------------------ #
    # Wallet reputation
    # ------------------------------------------------------------------ #

    def upsert_wallet(
        self,
        wallet: str,
        score: float,
        hit_count: int,
        blocked: bool = False,
    ) -> None:
        """Insert or update a wallet's reputation row."""
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT INTO wallet_reputation(wallet, score, hit_count, last_seen, blocked) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(wallet) DO UPDATE SET "
            "  score=excluded.score, hit_count=excluded.hit_count, "
            "  last_seen=excluded.last_seen, blocked=excluded.blocked",
            (wallet, float(score), hit_count, time.time(), int(blocked)),
        )
        self._conn.commit()

    def load_wallets(self) -> list[dict]:
        """Return all persisted wallet reputation rows."""
        if self._conn is None:
            return []
        rows = self._conn.execute(
            "SELECT wallet, score, hit_count, last_seen, blocked "
            "FROM wallet_reputation ORDER BY score DESC"
        ).fetchall()
        return [
            {
                "wallet": r[0],
                "score": r[1],
                "hit_count": r[2],
                "last_seen": r[3],
                "blocked": bool(r[4]),
            }
            for r in rows
        ]

    def top_suspect_wallets(self, n: int = 10) -> list[dict]:
        """Return the top N wallets by reputation score."""
        if self._conn is None:
            return []
        rows = self._conn.execute(
            "SELECT wallet, score, hit_count, last_seen, blocked "
            "FROM wallet_reputation ORDER BY score DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [
            {
                "wallet": r[0],
                "score": r[1],
                "hit_count": r[2],
                "last_seen": r[3],
                "blocked": bool(r[4]),
            }
            for r in rows
        ]

    def wallet_count(self) -> int:
        """Total number of tracked wallets."""
        if self._conn is None:
            return 0
        return self._conn.execute("SELECT COUNT(*) FROM wallet_reputation").fetchone()[0]
