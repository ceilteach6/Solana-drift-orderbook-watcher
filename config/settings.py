"""
config/settings.py

Loads configuration from environment variables (and a local ``.env`` file if
present, via python-dotenv). Mirrors the keys documented in
``config.example.env``.

Import the ready-to-use singleton:

    from config.settings import settings
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads a local .env if one exists; harmless otherwise
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when a `.env` / environment value is out of range or unusable."""


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
        self._validate()

    def _validate(self) -> None:
        """Fail fast on out-of-range config instead of letting a detector or
        the risk aggregator misbehave (silently never fire, or crash on a
        division) somewhere downstream, at an arbitrary point at runtime.

        Centralizing the checks here means every detector can trust the
        settings it receives — no per-detector defensive clamping needed.
        """
        errors: list[str] = []

        def require(cond: bool, msg: str) -> None:
            if not cond:
                errors.append(msg)

        require(bool(self.markets), "MARKETS must not be empty")
        require(self.orderbook_depth > 0, "ORDERBOOK_DEPTH must be > 0")
        require(self.update_frequency_ms > 0, "UPDATE_FREQUENCY_MS must be > 0")

        require(self.repeated_min_count >= 2, "REPEATED_MIN_COUNT must be >= 2")
        require(
            0 <= self.repeated_size_tolerance < 1,
            "REPEATED_SIZE_TOLERANCE must be in [0, 1)",
        )
        require(self.layering_min_levels >= 2, "LAYERING_MIN_LEVELS must be >= 2")
        require(self.flicker_window_sec > 0, "FLICKER_WINDOW_SEC must be > 0")
        require(self.flicker_min_events >= 1, "FLICKER_MIN_EVENTS must be >= 1")
        require(
            0 < self.imbalance_min_ratio <= 1,
            "IMBALANCE_MIN_RATIO must be in (0, 1] — it is compared against "
            "|bid_vol-ask_vol|/(bid_vol+ask_vol), which never exceeds 1, so a "
            "larger value would mean the detector can never fire",
        )
        require(self.imbalance_min_levels >= 1, "IMBALANCE_MIN_LEVELS must be >= 1")
        require(self.spoof_window_sec > 0, "SPOOF_WINDOW_SEC must be > 0")
        require(self.spoof_wall_ratio > 1, "SPOOF_WALL_RATIO must be > 1")
        require(
            self.spoof_min_price_move > 0,
            "SPOOF_MIN_PRICE_MOVE must be > 0 (it is used as a divisor in the "
            "spoof-pull detector; 0 would crash it)",
        )
        require(
            0 < self.spoof_pull_fraction <= 1,
            "SPOOF_PULL_FRACTION must be in (0, 1]",
        )

        require(
            0 < self.risk_smoothing <= 1,
            "RISK_SMOOTHING must be in (0, 1] — 0 would freeze the smoothed "
            "risk score at 0 forever and alerts would never fire",
        )
        require(
            0 <= self.risk_clear_threshold < self.risk_alert_threshold <= 1,
            "RISK_CLEAR_THRESHOLD must be < RISK_ALERT_THRESHOLD (both within "
            "[0, 1]) or the alert hysteresis can never clear or never fire",
        )
        require(
            self.risk_alert_cooldown_sec >= 0,
            "RISK_ALERT_COOLDOWN_SEC must be >= 0",
        )

        require(
            self.healthcheck_interval_sec > 0,
            "HEALTHCHECK_INTERVAL_SEC must be > 0",
        )
        require(0 <= self.alert_min_score <= 1, "ALERT_MIN_SCORE must be in [0, 1]")
        require(
            self.alert_format in ("console", "json"),
            "ALERT_FORMAT must be 'console' or 'json'",
        )
        require(self.dashboard_port > 0, "DASHBOARD_PORT must be > 0")
        require(self.run_duration_sec >= 0, "RUN_DURATION_SEC must be >= 0")

        if errors:
            details = "\n".join(f"  - {e}" for e in errors)
            raise ConfigError(
                f"Invalid configuration ({len(errors)} problem"
                f"{'s' if len(errors) != 1 else ''} found in your .env / "
                f"environment):\n{details}"
            )

        # Soft warnings: not fatal, but worth flagging.
        if self.alert_webhook_url and not self.alert_webhook_url.startswith("https://"):
            logger.warning(
                "ALERT_WEBHOOK_URL does not use https:// — alerts would be sent "
                "unencrypted over the network."
            )


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
