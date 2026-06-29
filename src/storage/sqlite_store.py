"""
src/storage/sqlite_store.py

Time-series persistence on top of stdlib ``sqlite3`` (no extra dependency).

Three tables:
- ``detections`` — every raw detector finding (low volume, always useful)
- ``risk``       — the smoothed per-market risk score over time (chart panel)
- ``snapshots``  — full L2 books (high volume; only when PERSIST_SNAPSHOTS=true)

This is the foundation for replay, analytics, a metrics exporter, and the
TradingView-style dashboard (which reads price + risk + detection markers back
out of here). The DB file lives under ``data/`` which ``.gitignore`` excludes.
"""

from __future__ import annotations

import json
import os
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id        INTEGER PRIMARY KEY,
    market    TEXT NOT NULL,
    ts        REAL NOT NULL,
    mid       REAL,
    best_bid  REAL,
    best_ask  REAL,
    spread    REAL,
    bids      TEXT,
    asks      TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_market_ts ON snapshots(market, ts);

CREATE TABLE IF NOT EXISTS detections (
    id        INTEGER PRIMARY KEY,
    market    TEXT NOT NULL,
    ts        REAL NOT NULL,
    detector  TEXT NOT NULL,
    score     REAL NOT NULL,
    message   TEXT,
    details   TEXT
);
CREATE INDEX IF NOT EXISTS idx_detections_market_ts ON detections(market, ts);
CREATE INDEX IF NOT EXISTS idx_detections_detector ON detections(detector);

CREATE TABLE IF NOT EXISTS risk (
    id        INTEGER PRIMARY KEY,
    market    TEXT NOT NULL,
    ts        REAL NOT NULL,
    score     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_market_ts ON risk(market, ts);
"""


class Store:
    """Persistence interface (so a Postgres backend can be dropped in later)."""

    def connect(self) -> None:
        raise NotImplementedError

    def record_snapshot(self, snapshot) -> None:
        raise NotImplementedError

    def record_detections(self, ts: float, detections) -> None:
        raise NotImplementedError

    def record_risk(self, market: str, ts: float, score: float) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _levels_to_json(levels) -> str:
    return json.dumps([[round(l.price, 8), round(l.size, 8)] for l in levels])


class SQLiteStore(Store):
    def __init__(self, db_path: str = "data/watcher.db") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if self.db_path not in (":memory:", ""):
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        # WAL lets a dashboard read while the watcher writes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    def record_snapshot(self, snapshot) -> None:
        best_bid = snapshot.bids[0].price if snapshot.bids else None
        best_ask = snapshot.asks[0].price if snapshot.asks else None
        spread = (best_ask - best_bid) if (best_bid and best_ask) else None
        self._conn.execute(
            "INSERT INTO snapshots(market, ts, mid, best_bid, best_ask, spread, bids, asks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.market,
                snapshot.timestamp,
                snapshot.mid,
                best_bid,
                best_ask,
                spread,
                _levels_to_json(snapshot.bids),
                _levels_to_json(snapshot.asks),
            ),
        )
        self._conn.commit()

    def record_detections(self, ts: float, detections) -> None:
        """Record detections, stamped with the snapshot timestamp."""
        rows = [
            (d.market, ts, d.detector, d.score, d.message, json.dumps(d.details))
            for d in detections
        ]
        if not rows:
            return
        self._conn.executemany(
            "INSERT INTO detections(market, ts, detector, score, message, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def record_risk(self, market: str, ts: float, score: float) -> None:
        self._conn.execute(
            "INSERT INTO risk(market, ts, score) VALUES (?, ?, ?)",
            (market, ts, score),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    def counts(self) -> dict[str, int]:
        out = {}
        for table in ("snapshots", "detections", "risk"):
            cur = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
            out[table] = cur.fetchone()["n"]
        return out

    def recent_detections(self, limit: int = 10) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT ts, market, detector, score, message FROM detections "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return list(cur.fetchall())

    def summary(self) -> str:
        counts = self.counts()
        lines = ["📦 Storage summary", f"   DB: {self.db_path}"]
        for table, n in counts.items():
            lines.append(f"   {table.ljust(11)}: {n} rows")
        recent = self.recent_detections(5)
        if recent:
            lines.append("   recent detections:")
            for r in recent:
                lines.append(
                    f"     {r['market']} {r['detector']} "
                    f"(score {r['score']:.2f}) — {r['message']}"
                )
        return "\n".join(lines)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None
