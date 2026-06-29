"""
config/settings.py

Loads configuration from environment variables (and a local ``.env`` file if
present, via python-dotenv). Mirrors the keys documented in
``config.example.env``.

Import the ready-to-use singleton:

    from config.settings import settings
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads a local .env if one exists; harmless otherwise
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _get_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw.strip() if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    # --- Connection ---
    rpc_url: str
    drift_env: str
    keypair_path: str

    # --- Markets / feed ---
    markets: tuple = field(default_factory=tuple)  # tuple[str, ...] — truly immutable
    orderbook_depth: int = 20
    update_frequency_ms: int = 1000

    # --- Detector thresholds ---
    repeated_min_count: int = 4
    repeated_size_tolerance: float = 0.001
    layering_min_levels: int = 5
    flicker_window_sec: float = 5.0
    flicker_min_events: int = 3
    imbalance_min_ratio: float = 0.85
    imbalance_min_levels: int = 5
    spoof_window_sec: float = 10.0
    spoof_wall_ratio: float = 5.0
    spoof_min_price_move: float = 0.001
    spoof_pull_fraction: float = 0.5

    # --- Risk aggregation ---
    risk_aggregation: bool = True
    risk_smoothing: float = 0.4  # EMA alpha (0..1); higher = more reactive
    risk_alert_threshold: float = 0.6
    risk_clear_threshold: float = 0.4
    risk_alert_cooldown_sec: float = 30.0

    # --- Health-check (periodic in-process self-test) ---
    healthcheck_enabled: bool = False
    healthcheck_interval_sec: float = 300.0

    # --- Storage (time-series persistence) ---
    storage_enabled: bool = False
    db_path: str = "data/watcher.db"
    persist_snapshots: bool = False  # high volume; off by default

    # --- Dashboard ---
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787

    # --- Alerting ---
    alert_min_score: float = 0.6
    alert_format: str = "console"
    # Webhook credentials/links are the user's part (see docs/NOTES.md).
    alert_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Metrics ---
    metrics_port: int = 0  # 0 = disabled; e.g. 8000 → http://localhost:8000/metrics

    # --- Run control ---
    run_duration_sec: float = 0.0


def _validate(s: Settings) -> None:
    """Raise :exc:`ValueError` early for obviously wrong configuration values."""
    if s.spoof_min_price_move <= 0:
        raise ValueError(
            f"SPOOF_MIN_PRICE_MOVE must be > 0 (got {s.spoof_min_price_move}). "
            "A value of 0 causes division-by-zero in the spoof-pull detector."
        )
    if not (0.0 < s.risk_smoothing <= 1.0):
        raise ValueError(
            f"RISK_SMOOTHING must be in (0, 1] (got {s.risk_smoothing})."
        )
    if not (0.0 < s.risk_alert_threshold <= 1.0):
        raise ValueError(
            f"RISK_ALERT_THRESHOLD must be in (0, 1] (got {s.risk_alert_threshold})."
        )
    if not (0.0 <= s.risk_clear_threshold < s.risk_alert_threshold):
        raise ValueError(
            "RISK_CLEAR_THRESHOLD must be >= 0 and < RISK_ALERT_THRESHOLD "
            f"(got clear={s.risk_clear_threshold}, alert={s.risk_alert_threshold})."
        )
    if s.orderbook_depth < 1:
        raise ValueError(
            f"ORDERBOOK_DEPTH must be >= 1 (got {s.orderbook_depth})."
        )
    if s.update_frequency_ms < 10:
        raise ValueError(
            f"UPDATE_FREQUENCY_MS must be >= 10 ms (got {s.update_frequency_ms})."
        )
    if not (0 <= s.metrics_port <= 65535):
        raise ValueError(
            f"METRICS_PORT must be 0–65535 (got {s.metrics_port})."
        )
    if not (0 <= s.dashboard_port <= 65535):
        raise ValueError(
            f"DASHBOARD_PORT must be 0–65535 (got {s.dashboard_port})."
        )


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment."""
    markets_raw = _get_str("MARKETS", "SOL-PERP")
    markets_list = [m.strip() for m in markets_raw.split(",") if m.strip()]

    s = Settings(
        rpc_url=_get_str("RPC_URL", "https://api.mainnet-beta.solana.com"),
        drift_env=_get_str("DRIFT_ENV", "mainnet"),
        keypair_path=_get_str("KEYPAIR_PATH", ""),
        markets=tuple(markets_list) if markets_list else ("SOL-PERP",),
        orderbook_depth=_get_int("ORDERBOOK_DEPTH", 20),
        update_frequency_ms=_get_int("UPDATE_FREQUENCY_MS", 1000),
        repeated_min_count=_get_int("REPEATED_MIN_COUNT", 4),
        repeated_size_tolerance=_get_float("REPEATED_SIZE_TOLERANCE", 0.001),
        layering_min_levels=_get_int("LAYERING_MIN_LEVELS", 5),
        flicker_window_sec=_get_float("FLICKER_WINDOW_SEC", 5.0),
        flicker_min_events=_get_int("FLICKER_MIN_EVENTS", 3),
        imbalance_min_ratio=_get_float("IMBALANCE_MIN_RATIO", 0.85),
        imbalance_min_levels=_get_int("IMBALANCE_MIN_LEVELS", 5),
        spoof_window_sec=_get_float("SPOOF_WINDOW_SEC", 10.0),
        spoof_wall_ratio=_get_float("SPOOF_WALL_RATIO", 5.0),
        spoof_min_price_move=_get_float("SPOOF_MIN_PRICE_MOVE", 0.001),
        spoof_pull_fraction=_get_float("SPOOF_PULL_FRACTION", 0.5),
        risk_aggregation=_get_str("RISK_AGGREGATION", "true").lower()
        not in ("0", "false", "no", "off"),
        risk_smoothing=_get_float("RISK_SMOOTHING", 0.4),
        risk_alert_threshold=_get_float("RISK_ALERT_THRESHOLD", 0.6),
        risk_clear_threshold=_get_float("RISK_CLEAR_THRESHOLD", 0.4),
        risk_alert_cooldown_sec=_get_float("RISK_ALERT_COOLDOWN_SEC", 30.0),
        healthcheck_enabled=_get_str("HEALTHCHECK_ENABLED", "false").lower()
        in ("1", "true", "yes", "on"),
        healthcheck_interval_sec=_get_float("HEALTHCHECK_INTERVAL_SEC", 300.0),
        storage_enabled=_get_str("STORAGE_ENABLED", "false").lower()
        in ("1", "true", "yes", "on"),
        db_path=_get_str("DB_PATH", "data/watcher.db"),
        persist_snapshots=_get_str("PERSIST_SNAPSHOTS", "false").lower()
        in ("1", "true", "yes", "on"),
        dashboard_host=_get_str("DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=_get_int("DASHBOARD_PORT", 8787),
        alert_min_score=_get_float("ALERT_MIN_SCORE", 0.6),
        alert_format=_get_str("ALERT_FORMAT", "console").lower(),
        alert_webhook_url=_get_str("ALERT_WEBHOOK_URL", ""),
        telegram_bot_token=_get_str("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_get_str("TELEGRAM_CHAT_ID", ""),
        metrics_port=_get_int("METRICS_PORT", 0),
        run_duration_sec=_get_float("RUN_DURATION_SEC", 0.0),
    )
    _validate(s)
    return s


# Ready-to-use singleton.
settings = load_settings()
