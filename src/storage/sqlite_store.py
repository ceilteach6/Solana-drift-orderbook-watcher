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
    score     REAL NOT NULL,
    mid       REAL
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

    def record_risk(self, market: str, ts: float, score: float, mid=None) -> None:
        raise NotImplementedError

    def record_tick(self, *, market, snapshot, detections,
                     persist_snapshot: bool, risk_score: float | None) -> None:
        raise NotImplementedError

    def prune(self, retention_sec: float, now: float) -> int:
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
    def _insert_snapshot(self, snapshot) -> None:
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

    def _insert_detections(self, ts: float, detections) -> None:
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

    def _insert_risk(self, market: str, ts: float, score: float, mid=None) -> None:
        self._conn.execute(
            "INSERT INTO risk(market, ts, score, mid) VALUES (?, ?, ?, ?)",
            (market, ts, score, mid),
        )

    def record_snapshot(self, snapshot) -> None:
        self._insert_snapshot(snapshot)
        self._conn.commit()

    def record_detections(self, ts: float, detections) -> None:
        """Record detections, stamped with the snapshot timestamp."""
        self._insert_detections(ts, detections)
        self._conn.commit()

    def record_risk(self, market: str, ts: float, score: float, mid=None) -> None:
        self._insert_risk(market, ts, score, mid)
        self._conn.commit()

    def record_tick(self, *, market, snapshot, detections,
                     persist_snapshot: bool, risk_score: float | None) -> None:
        """Persist everything for one watcher tick as a single transaction.

        Doing the snapshot/detections/risk inserts as one commit (instead of
        one commit each) halves-to-thirds the fsync count under sustained
        load and keeps the three rows for a tick atomically consistent.
        """
        if persist_snapshot:
            self._insert_snapshot(snapshot)
        self._insert_detections(snapshot.timestamp, detections)
        if risk_score is not None:
            self._insert_risk(market, snapshot.timestamp, risk_score, snapshot.mid)
        self._conn.commit()

    def prune(self, retention_sec: float, now: float) -> int:
        """Delete rows older than ``retention_sec`` from all tables.

        Keeps a long-running watcher's DB file from growing without bound.
        Freed pages are reused by SQLite for subsequent inserts, so this
        alone is enough to cap growth without a (blocking) VACUUM.
        """
        if retention_sec <= 0:
            return 0
        cutoff = now - retention_sec
        deleted = 0
        for table in ("snapshots", "detections", "risk"):
            cur = self._conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
            deleted += cur.rowcount if cur.rowcount > 0 else 0
        self._conn.commit()
        return deleted

    # ------------------------------------------------------------------ #
    # Read APIs for the dashboard (bucketed to whole seconds so the chart
    # gets unique, ascending time values).
    # ------------------------------------------------------------------ #
    def markets(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT market FROM risk "
            "UNION SELECT DISTINCT market FROM detections ORDER BY market"
        )
        return [r["market"] for r in cur.fetchall()]

    _ALLOWED_SERIES_COLUMNS = frozenset({"mid", "score"})

    def _series(self, column: str, market: str, limit: int):
        if column not in self._ALLOWED_SERIES_COLUMNS:
            raise ValueError(f"Invalid column: {column!r}")
        cur = self._conn.execute(
            f"SELECT CAST(ts AS INTEGER) AS sec, AVG({column}) AS v FROM risk "
            f"WHERE market = ? AND {column} IS NOT NULL "
            "GROUP BY sec ORDER BY sec DESC LIMIT ?",
            (market, limit),
        )
        rows = cur.fetchall()
        rows.reverse()  # ascending for the chart
        return [{"time": r["sec"], "value": r["v"]} for r in rows]

    def price_series(self, market: str, limit: int = 2000):
        return self._series("mid", market, limit)

    def risk_series(self, market: str, limit: int = 2000):
        return self._series("score", market, limit)

    def detection_markers(self, market: str, limit: int = 200):
        cur = self._conn.execute(
            "SELECT CAST(ts AS INTEGER) AS sec, detector, score, message FROM detections "
            "WHERE market = ? ORDER BY id DESC LIMIT ?",
            (market, limit),
        )
        rows = list(cur.fetchall())
        rows.reverse()
        return [
            {
                "time": r["sec"],
                "detector": r["detector"],
                "score": r["score"],
                "message": r["message"],
            }
            for r in rows
        ]

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
