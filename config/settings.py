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
    markets: list[str] = field(default_factory=list)
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

    # --- Run control ---
    run_duration_sec: float = 0.0

    def __post_init__(self) -> None:
        """Fail fast on nonsensical config instead of crashing mid-run.

        Several detectors divide by these thresholds (or index into a
        result that only exists once the threshold is met); a value of 0
        or less doesn't mean "more sensitive", it means a ZeroDivisionError
        or IndexError the next time that detector runs.
        """
        _require_positive_int(
            "REPEATED_MIN_COUNT", self.repeated_min_count,
            "ORDERBOOK_DEPTH", self.orderbook_depth,
            "UPDATE_FREQUENCY_MS", self.update_frequency_ms,
            "LAYERING_MIN_LEVELS", self.layering_min_levels,
            "FLICKER_MIN_EVENTS", self.flicker_min_events,
            "IMBALANCE_MIN_LEVELS", self.imbalance_min_levels,
        )
        _require_positive_float(
            "SPOOF_MIN_PRICE_MOVE", self.spoof_min_price_move,
            "SPOOF_WALL_RATIO", self.spoof_wall_ratio,
            "FLICKER_WINDOW_SEC", self.flicker_window_sec,
            "SPOOF_WINDOW_SEC", self.spoof_window_sec,
        )
        _require_range("IMBALANCE_MIN_RATIO", self.imbalance_min_ratio, 0.0, 1.0)
        _require_range("SPOOF_PULL_FRACTION", self.spoof_pull_fraction, 0.0, 1.0)
        _require_range("RISK_SMOOTHING", self.risk_smoothing, 0.0, 1.0)
        _require_range("RISK_ALERT_THRESHOLD", self.risk_alert_threshold, 0.0, 1.0)
        _require_range("RISK_CLEAR_THRESHOLD", self.risk_clear_threshold, 0.0, 1.0)
        if self.risk_clear_threshold >= self.risk_alert_threshold:
            raise ValueError(
                "RISK_CLEAR_THRESHOLD "
                f"({self.risk_clear_threshold}) must be lower than "
                f"RISK_ALERT_THRESHOLD ({self.risk_alert_threshold}), or the "
                "aggregator can never leave its elevated state."
            )


def _require_positive_int(*name_value_pairs) -> None:
    for name, value in zip(name_value_pairs[0::2], name_value_pairs[1::2]):
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer, got {value!r}")


def _require_positive_float(*name_value_pairs) -> None:
    for name, value in zip(name_value_pairs[0::2], name_value_pairs[1::2]):
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{name} must be > 0, got {value!r}")


def _require_range(name: str, value: float, lo: float, hi: float) -> None:
    if not (lo <= value <= hi):
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value!r}")


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment."""
    markets_raw = _get_str("MARKETS", "SOL-PERP")
    markets = [m.strip() for m in markets_raw.split(",") if m.strip()]

    return Settings(
        rpc_url=_get_str("RPC_URL", "https://api.mainnet-beta.solana.com"),
        drift_env=_get_str("DRIFT_ENV", "mainnet"),
        keypair_path=_get_str("KEYPAIR_PATH", ""),
        markets=markets or ["SOL-PERP"],
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
        run_duration_sec=_get_float("RUN_DURATION_SEC", 0.0),
    )


# Ready-to-use singleton.
settings = load_settings()
