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
    markets: tuple[str, ...] = field(default_factory=tuple)
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
        errors = self._validate()
        if errors:
            raise ValueError(
                "Invalid configuration (" + str(len(errors)) + " problem(s)):\n  - "
                + "\n  - ".join(errors)
            )

    def _validate(self) -> list[str]:
        """Collect (not raise on the first) every configuration problem.

        Catching these at startup turns a misconfigured threshold into a
        clear, immediate error instead of a runtime crash (or silent
        misbehaviour) buried several ticks into a live run — e.g.
        ``spoof_min_price_move=0`` would otherwise divide by zero inside the
        spoof-pull detector on every single tick.
        """
        errors: list[str] = []

        def positive(name: str, value: float) -> None:
            if not value > 0:
                errors.append(f"{name} must be > 0 (got {value!r})")

        def non_negative(name: str, value: float) -> None:
            if value < 0:
                errors.append(f"{name} must be >= 0 (got {value!r})")

        def unit_interval(name: str, value: float, *, low_inclusive=True, high_inclusive=True) -> None:
            lo_ok = value >= 0 if low_inclusive else value > 0
            hi_ok = value <= 1 if high_inclusive else value < 1
            if not (lo_ok and hi_ok):
                errors.append(f"{name} must be within [0, 1] (got {value!r})")

        if not self.markets or any(not m for m in self.markets):
            errors.append("markets must be a non-empty list of non-empty market names")

        if self.orderbook_depth < 1:
            errors.append(f"orderbook_depth must be >= 1 (got {self.orderbook_depth!r})")
        if self.update_frequency_ms < 1:
            errors.append(f"update_frequency_ms must be >= 1 (got {self.update_frequency_ms!r})")

        if self.repeated_min_count < 2:
            errors.append(f"repeated_min_count must be >= 2 (got {self.repeated_min_count!r})")
        non_negative("repeated_size_tolerance", self.repeated_size_tolerance)

        if self.layering_min_levels < 2:
            errors.append(f"layering_min_levels must be >= 2 (got {self.layering_min_levels!r})")

        positive("flicker_window_sec", self.flicker_window_sec)
        if self.flicker_min_events < 1:
            errors.append(f"flicker_min_events must be >= 1 (got {self.flicker_min_events!r})")

        unit_interval("imbalance_min_ratio", self.imbalance_min_ratio, low_inclusive=False)
        if self.imbalance_min_levels < 1:
            errors.append(f"imbalance_min_levels must be >= 1 (got {self.imbalance_min_levels!r})")

        positive("spoof_window_sec", self.spoof_window_sec)
        positive("spoof_wall_ratio", self.spoof_wall_ratio)
        # Divided into directly in SpoofPullDetector.analyze() — zero/negative
        # would raise ZeroDivisionError (or invert the comparison) every tick.
        positive("spoof_min_price_move", self.spoof_min_price_move)
        unit_interval("spoof_pull_fraction", self.spoof_pull_fraction)

        unit_interval("risk_smoothing", self.risk_smoothing, low_inclusive=False)
        unit_interval("risk_alert_threshold", self.risk_alert_threshold, low_inclusive=False)
        unit_interval("risk_clear_threshold", self.risk_clear_threshold)
        if self.risk_clear_threshold >= self.risk_alert_threshold:
            errors.append(
                "risk_clear_threshold must be < risk_alert_threshold "
                f"(got clear={self.risk_clear_threshold!r}, alert={self.risk_alert_threshold!r}) "
                "— otherwise the hysteresis gate never clears an alert"
            )
        non_negative("risk_alert_cooldown_sec", self.risk_alert_cooldown_sec)

        if self.healthcheck_enabled:
            positive("healthcheck_interval_sec", self.healthcheck_interval_sec)

        if not (1 <= self.dashboard_port <= 65535):
            errors.append(f"dashboard_port must be between 1 and 65535 (got {self.dashboard_port!r})")

        unit_interval("alert_min_score", self.alert_min_score)
        if self.alert_format not in ("console", "json"):
            errors.append(f"alert_format must be 'console' or 'json' (got {self.alert_format!r})")

        non_negative("run_duration_sec", self.run_duration_sec)

        return errors


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment."""
    markets_raw = _get_str("MARKETS", "SOL-PERP")
    markets = tuple(m.strip() for m in markets_raw.split(",") if m.strip())

    return Settings(
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


# Ready-to-use singleton.
settings = load_settings()
