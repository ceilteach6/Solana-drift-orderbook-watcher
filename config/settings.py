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


class SettingsError(ValueError):
    """Raised when the loaded configuration is out of range or inconsistent."""


def _validate(settings: "Settings") -> "Settings":
    """Fail fast on config that would otherwise misbehave deep in the pipeline.

    Bad values here don't raise where they're used (a stray ``UPDATE_FREQUENCY_MS=0``
    would just busy-loop hammering the RPC; a smoothing alpha outside [0, 1] silently
    produces a meaningless EMA); by the time symptoms show up they're hard to trace
    back to the .env typo that caused them. Catching it once at startup, with the
    offending key named in the message, is cheaper than debugging each symptom.
    """
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    check(settings.update_frequency_ms > 0, "UPDATE_FREQUENCY_MS must be > 0")
    check(settings.orderbook_depth > 0, "ORDERBOOK_DEPTH must be > 0")
    check(settings.markets, "MARKETS must contain at least one market")

    check(settings.repeated_min_count > 0, "REPEATED_MIN_COUNT must be > 0")
    check(settings.layering_min_levels > 0, "LAYERING_MIN_LEVELS must be > 0")
    check(settings.flicker_window_sec > 0, "FLICKER_WINDOW_SEC must be > 0")
    check(settings.flicker_min_events > 0, "FLICKER_MIN_EVENTS must be > 0")
    check(settings.imbalance_min_levels > 0, "IMBALANCE_MIN_LEVELS must be > 0")
    check(settings.spoof_window_sec > 0, "SPOOF_WINDOW_SEC must be > 0")

    check(0.0 <= settings.imbalance_min_ratio <= 1.0, "IMBALANCE_MIN_RATIO must be in [0, 1]")
    check(0.0 <= settings.spoof_pull_fraction <= 1.0, "SPOOF_PULL_FRACTION must be in [0, 1]")

    check(0.0 < settings.risk_smoothing <= 1.0, "RISK_SMOOTHING (EMA alpha) must be in (0, 1]")
    check(0.0 <= settings.risk_clear_threshold <= 1.0, "RISK_CLEAR_THRESHOLD must be in [0, 1]")
    check(0.0 <= settings.risk_alert_threshold <= 1.0, "RISK_ALERT_THRESHOLD must be in [0, 1]")
    check(
        settings.risk_clear_threshold < settings.risk_alert_threshold,
        "RISK_CLEAR_THRESHOLD must be lower than RISK_ALERT_THRESHOLD "
        "(otherwise an alert can never clear)",
    )
    check(settings.risk_alert_cooldown_sec >= 0, "RISK_ALERT_COOLDOWN_SEC must be >= 0")

    check(settings.healthcheck_interval_sec > 0, "HEALTHCHECK_INTERVAL_SEC must be > 0")
    check(0.0 <= settings.alert_min_score <= 1.0, "ALERT_MIN_SCORE must be in [0, 1]")
    check(0 < settings.dashboard_port <= 65535, "DASHBOARD_PORT must be a valid port number")
    check(settings.run_duration_sec >= 0, "RUN_DURATION_SEC must be >= 0")

    if errors:
        raise SettingsError(
            "Invalid configuration (" + str(len(errors)) + " issue(s)):\n  - "
            + "\n  - ".join(errors)
        )
    return settings


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment."""
    markets_raw = _get_str("MARKETS", "SOL-PERP")
    markets = [m.strip() for m in markets_raw.split(",") if m.strip()]

    return _validate(Settings(
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
    ))


# Ready-to-use singleton.
settings = load_settings()
