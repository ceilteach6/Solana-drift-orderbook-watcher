"""
src/alert/sqlite_alert.py

SQLite persistence sink.

Writes every delivered detection to a local SQLite database so you can:
  - query detection history after the fact
  - replay detections for model tuning / back-testing
  - build Grafana dashboards via DuckDB or similar

Activate by setting ``SQLITE_PATH`` in .env (or the environment):

    SQLITE_PATH=detections.db

If ``SQLITE_PATH`` is empty (the default), this sink is not added to the
dispatcher and no database file is created.

Schema
------
Table ``detections``:
  id        INTEGER PRIMARY KEY AUTOINCREMENT
  ts        REAL    — Unix epoch seconds (time.time())
  market    TEXT
  detector  TEXT
  score     REAL
  message   TEXT
  details   TEXT    — JSON-encoded details dict

Indices on (ts), (market), (detector) keep queries fast for typical slices.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time

from src.alert.base import Alert

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS detections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    market   TEXT    NOT NULL,
    detector TEXT    NOT NULL,
    score    REAL    NOT NULL,
    message  TEXT    NOT NULL,
    details  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ts       ON detections (ts);
CREATE INDEX IF NOT EXISTS idx_market   ON detections (market);
CREATE INDEX IF NOT EXISTS idx_detector ON detections (detector);
"""

_INSERT = (
    "INSERT INTO detections (ts, market, detector, score, message, details) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


class SqliteAlert(Alert):
    name = "sqlite"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._path: str = settings.sqlite_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @staticmethod
    def is_configured(settings) -> bool:
        return bool(getattr(settings, "sqlite_path", ""))

    def _init_db(self) -> None:
        try:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.executescript(_DDL)
            self._conn.commit()
            logger.info("SQLite detection log: %s", self._path)
        except Exception:
            logger.exception("Failed to open SQLite database %r — sink disabled", self._path)
            self._conn = None

    def _write_sync(self, ts: float, market: str, detector: str,
                    score: float, message: str, details_json: str) -> None:
        self._conn.execute(_INSERT, (ts, market, detector, score, message, details_json))
        self._conn.commit()

    async def deliver(self, detection) -> None:
        if self._conn is None:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._write_sync,
                time.time(),
                detection.market,
                detection.detector,
                round(detection.score, 6),
                detection.message,
                json.dumps(detection.details),
            )
        except Exception:
            logger.exception("SQLite write failed for detection %r", detection.detector)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
