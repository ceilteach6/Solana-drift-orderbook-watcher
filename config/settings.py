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
from dataclasses import dataclass

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


class ConfigError(ValueError):
    """Raised when the loaded environment produces an unusable configuration."""


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    # --- Connection ---
    rpc_url: str
    drift_env: str
    keypair_path: str

    # --- Markets / feed ---
    markets: tuple[str, ...] = ()
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

    def validate(self) -> None:
        """Fail fast on configuration that would misbehave deep into a run.

        Called once from :func:`load_settings`, so a bad ``.env`` value is
        reported clearly at startup instead of surfacing later as a
        ``ZeroDivisionError`` in a detector, a busy-loop, or a risk gauge
        that can never clear.
        """
        errors: list[str] = []

        if not self.markets:
            errors.append("MARKETS must list at least one market")
        if self.orderbook_depth < 1:
            errors.append("ORDERBOOK_DEPTH must be >= 1")
        if self.update_frequency_ms <= 0:
            errors.append("UPDATE_FREQUENCY_MS must be > 0")

        if self.repeated_min_count < 1:
            errors.append("REPEATED_MIN_COUNT must be >= 1")
        if self.layering_min_levels < 1:
            errors.append("LAYERING_MIN_LEVELS must be >= 1")
        if self.flicker_min_events < 1:
            errors.append("FLICKER_MIN_EVENTS must be >= 1")
        if self.flicker_window_sec <= 0:
            errors.append("FLICKER_WINDOW_SEC must be > 0")
        if not 0.0 < self.imbalance_min_ratio <= 1.0:
            errors.append("IMBALANCE_MIN_RATIO must be in (0, 1]")
        if self.imbalance_min_levels < 1:
            errors.append("IMBALANCE_MIN_LEVELS must be >= 1")
        if self.spoof_window_sec <= 0:
            errors.append("SPOOF_WINDOW_SEC must be > 0")
        if self.spoof_min_price_move <= 0:
            errors.append("SPOOF_MIN_PRICE_MOVE must be > 0 (divides a ratio)")
        if self.spoof_wall_ratio <= 0:
            errors.append("SPOOF_WALL_RATIO must be > 0")
        if not 0.0 < self.spoof_pull_fraction <= 1.0:
            errors.append("SPOOF_PULL_FRACTION must be in (0, 1]")

        if self.risk_aggregation:
            if not 0.0 < self.risk_smoothing <= 1.0:
                errors.append("RISK_SMOOTHING must be in (0, 1]")
            if not 0.0 < self.risk_alert_threshold <= 1.0:
                errors.append("RISK_ALERT_THRESHOLD must be in (0, 1]")
            if not 0.0 <= self.risk_clear_threshold < self.risk_alert_threshold:
                errors.append(
                    "RISK_CLEAR_THRESHOLD must be >= 0 and < RISK_ALERT_THRESHOLD "
                    "(otherwise the alert can never clear, or flaps every tick)"
                )
            if self.risk_alert_cooldown_sec < 0:
                errors.append("RISK_ALERT_COOLDOWN_SEC must be >= 0")

        if not 0.0 <= self.alert_min_score <= 1.0:
            errors.append("ALERT_MIN_SCORE must be in [0, 1]")
        if not 1 <= self.dashboard_port <= 65535:
            errors.append("DASHBOARD_PORT must be a valid TCP port (1-65535)")

        if errors:
            raise ConfigError(
                "Invalid configuration:\n  - " + "\n  - ".join(errors)
            )


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment.

    Raises :class:`ConfigError` if the resulting configuration is invalid.
    """
    markets_raw = _get_str("MARKETS", "SOL-PERP")
    markets = tuple(m.strip() for m in markets_raw.split(",") if m.strip())

    result = Settings(
        rpc_url=_get_str("RPC_URL", "https://api.mainnet-beta.solana.com"),
        drift_env=_get_str("DRIFT_ENV", "mainnet"),
        keypair_path=_get_str("KEYPAIR_PATH", ""),
        markets=markets or ("SOL-PERP",),
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
    result.validate()
    return result


# Ready-to-use singleton.
settings = load_settings()
