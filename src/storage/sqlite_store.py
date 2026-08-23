"""
src/storage/sqlite_store.py

Time-series persistence on top of stdlib ``sqlite3`` (no extra dependency).

Four tables:
- ``detections`` — every raw detector finding (low volume, always useful)
- ``prices``     — mid price per tick (low volume, always written; feeds the
                    dashboard's price line independent of risk aggregation)
- ``risk``       — the smoothed per-market risk score over time (chart panel;
                    only populated when RISK_AGGREGATION=true)
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

CREATE TABLE IF NOT EXISTS prices (
    id        INTEGER PRIMARY KEY,
    market    TEXT NOT NULL,
    ts        REAL NOT NULL,
    mid       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_market_ts ON prices(market, ts);
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
        # check_same_thread=False: record_tick() is dispatched via
        # asyncio.to_thread() off the watcher's hot path (see Watcher._persist),
        # which can hand different calls to different worker threads. Writes
        # stay strictly sequential (the watcher awaits one at a time), so this
        # is safe despite sqlite3's default same-thread restriction.
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL lets a dashboard read while the watcher writes. busy_timeout
        # makes a writer retry instead of raising "database is locked"
        # immediately when a reader briefly holds the file lock.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Each ``record_*`` call commits by default (safe standalone use, e.g.
    # from selftest/examples). Pass ``commit=False`` to batch several writes
    # from one caller into a single transaction/fsync — see ``record_tick``,
    # which the watcher's hot path uses instead of three separate commits.
    def record_snapshot(self, snapshot, *, commit: bool = True) -> None:
        best_bid = snapshot.bids[0].price if snapshot.bids else None
        best_ask = snapshot.asks[0].price if snapshot.asks else None
        # `is not None` (not truthiness): a legitimate price of exactly 0.0
        # must not be treated as "missing" and silently drop the spread.
        spread = (
            (best_ask - best_bid)
            if (best_bid is not None and best_ask is not None)
            else None
        )
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
        if commit:
            self._conn.commit()

    def record_detections(self, ts: float, detections, *, commit: bool = True) -> None:
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
        if commit:
            self._conn.commit()

    def record_risk(
        self, market: str, ts: float, score: float, mid=None, *, commit: bool = True
    ) -> None:
        self._conn.execute(
            "INSERT INTO risk(market, ts, score, mid) VALUES (?, ?, ?, ?)",
            (market, ts, score, mid),
        )
        if commit:
            self._conn.commit()

    def record_price(self, market: str, ts: float, mid: float, *, commit: bool = True) -> None:
        self._conn.execute(
            "INSERT INTO prices(market, ts, mid) VALUES (?, ?, ?)",
            (market, ts, mid),
        )
        if commit:
            self._conn.commit()

    def record_tick(self, market, snapshot, detections, risk=None, *, persist_snapshot=False) -> None:
        """Write one tick's worth of rows (snapshot/detections/price/risk) as
        a single transaction, instead of one fsync per table per tick.

        Price is recorded unconditionally (whenever the snapshot has a mid),
        independent of both ``persist_snapshot`` and risk aggregation, so the
        dashboard's price line has data even when RISK_AGGREGATION=false and
        full L2 snapshots aren't being persisted.
        """
        if persist_snapshot:
            self.record_snapshot(snapshot, commit=False)
        self.record_detections(snapshot.timestamp, detections, commit=False)
        if snapshot.mid is not None:
            self.record_price(market, snapshot.timestamp, snapshot.mid, commit=False)
        if risk is not None:
            self.record_risk(market, snapshot.timestamp, risk, snapshot.mid, commit=False)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Read APIs for the dashboard (bucketed to whole seconds so the chart
    # gets unique, ascending time values).
    # ------------------------------------------------------------------ #
    def markets(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT market FROM risk "
            "UNION SELECT DISTINCT market FROM detections "
            "UNION SELECT DISTINCT market FROM prices ORDER BY market"
        )
        return [r["market"] for r in cur.fetchall()]

    def price_series(self, market: str, limit: int = 2000):
        """Mid price over time, from the always-written ``prices`` table —
        available regardless of RISK_AGGREGATION or PERSIST_SNAPSHOTS."""
        cur = self._conn.execute(
            "SELECT CAST(ts AS INTEGER) AS sec, AVG(mid) AS v FROM prices "
            "WHERE market = ? GROUP BY sec ORDER BY sec DESC LIMIT ?",
            (market, limit),
        )
        rows = cur.fetchall()
        rows.reverse()  # ascending for the chart
        return [{"time": r["sec"], "value": r["v"]} for r in rows]

    def risk_series(self, market: str, limit: int = 2000):
        """Smoothed risk score over time. Empty when RISK_AGGREGATION=false,
        since no score exists to plot in that mode."""
        cur = self._conn.execute(
            "SELECT CAST(ts AS INTEGER) AS sec, AVG(score) AS v FROM risk "
            "WHERE market = ? AND score IS NOT NULL "
            "GROUP BY sec ORDER BY sec DESC LIMIT ?",
            (market, limit),
        )
        rows = cur.fetchall()
        rows.reverse()
        return [{"time": r["sec"], "value": r["v"]} for r in rows]

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
        for table in ("snapshots", "detections", "risk", "prices"):
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
