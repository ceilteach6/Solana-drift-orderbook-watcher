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

CREATE TABLE IF NOT EXISTS wallets (
    wallet      TEXT PRIMARY KEY,
    last_ts     REAL,
    score       REAL,
    activity    INTEGER,
    placements  INTEGER,
    cancels     INTEGER,
    markets     TEXT
);
CREATE INDEX IF NOT EXISTS idx_wallets_score ON wallets(score);
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

    def record_risk(self, market: str, ts: float, score: float, mid=None) -> None:
        self._conn.execute(
            "INSERT INTO risk(market, ts, score, mid) VALUES (?, ?, ?, ?)",
            (market, ts, score, mid),
        )
        self._conn.commit()

    def upsert_wallet(self, stat, ts: float) -> None:
        """Insert or update a wallet's latest reputation snapshot."""
        import json as _json

        self._conn.execute(
            "INSERT INTO wallets(wallet, last_ts, score, activity, placements, "
            "cancels, markets) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(wallet) DO UPDATE SET "
            "last_ts=excluded.last_ts, score=excluded.score, "
            "activity=excluded.activity, placements=excluded.placements, "
            "cancels=excluded.cancels, markets=excluded.markets",
            (
                stat.wallet, ts, stat.score, stat.activity(),
                len(stat.places), len(stat.cancels),
                _json.dumps(sorted(stat.markets())),
            ),
        )
        self._conn.commit()

    def top_wallets(self, limit: int = 10) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT wallet, score, activity, placements, cancels, markets "
            "FROM wallets ORDER BY score DESC, activity DESC LIMIT ?",
            (limit,),
        )
        return list(cur.fetchall())

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

    def snapshot_markets(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT market FROM snapshots ORDER BY market"
        )
        return [r["market"] for r in cur.fetchall()]

    def read_snapshots_raw(self, market: str, limit: int | None = None):
        """Stored L2 books for a market, oldest first (for replay)."""
        sql = (
            "SELECT ts, bids, asks FROM snapshots WHERE market = ? "
            "ORDER BY ts ASC, id ASC"
        )
        params: tuple = (market,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (market, limit)
        return list(self._conn.execute(sql, params).fetchall())

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
