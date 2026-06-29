"""
config/settings.py

Loads configuration from environment variables (and a local ``.env`` file if
present, via python-dotenv).  Mirrors the keys documented in
``config.example.env``.

Import the ready-to-use singleton:

    from config.settings import settings
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
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

    # --- Session-baseline detector ---
    baseline_warmup_cycles: int = 20
    baseline_spike_ratio: float = 3.0
    baseline_drain_ratio: float = 0.2
    baseline_ema_alpha: float = 0.1

    # --- Alerting ---
    alert_min_score: float = 0.6
    alert_format: str = "console"
    # Webhook credentials are the user's part — see docs/NOTES.md.
    alert_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Storage ---
    sqlite_path: str = ""  # leave empty to disable SQLite logging

    # --- Run control ---
    run_duration_sec: float = 0.0

    def validate(self) -> None:
        """Raise ValueError with a helpful message on bad configuration."""
        errors: list[str] = []
        if not self.rpc_url:
            errors.append("RPC_URL must not be empty")
        if self.orderbook_depth < 1:
            errors.append(f"ORDERBOOK_DEPTH must be >= 1, got {self.orderbook_depth}")
        if self.update_frequency_ms < 50:
            errors.append(
                f"UPDATE_FREQUENCY_MS must be >= 50, got {self.update_frequency_ms}"
            )
        if not 0.0 <= self.alert_min_score <= 1.0:
            errors.append(
                f"ALERT_MIN_SCORE must be in [0, 1], got {self.alert_min_score}"
            )
        if self.alert_format not in ("console", "json"):
            errors.append(
                f"ALERT_FORMAT must be 'console' or 'json', got {self.alert_format!r}"
            )
        if not self.markets:
            errors.append("MARKETS must contain at least one market name")
        if self.repeated_min_count < 2:
            errors.append(f"REPEATED_MIN_COUNT must be >= 2, got {self.repeated_min_count}")
        if self.layering_min_levels < 2:
            errors.append(f"LAYERING_MIN_LEVELS must be >= 2, got {self.layering_min_levels}")
        if self.flicker_min_events < 2:
            errors.append(f"FLICKER_MIN_EVENTS must be >= 2, got {self.flicker_min_events}")
        if not 0.0 < self.baseline_spike_ratio:
            errors.append(f"BASELINE_SPIKE_RATIO must be > 0, got {self.baseline_spike_ratio}")
        if not 0.0 < self.baseline_ema_alpha <= 1.0:
            errors.append(f"BASELINE_EMA_ALPHA must be in (0, 1], got {self.baseline_ema_alpha}")
        if errors:
            raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))


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
        baseline_warmup_cycles=_get_int("BASELINE_WARMUP_CYCLES", 20),
        baseline_spike_ratio=_get_float("BASELINE_SPIKE_RATIO", 3.0),
        baseline_drain_ratio=_get_float("BASELINE_DRAIN_RATIO", 0.2),
        baseline_ema_alpha=_get_float("BASELINE_EMA_ALPHA", 0.1),
        alert_min_score=_get_float("ALERT_MIN_SCORE", 0.6),
        alert_format=_get_str("ALERT_FORMAT", "console").lower(),
        alert_webhook_url=_get_str("ALERT_WEBHOOK_URL", ""),
        telegram_bot_token=_get_str("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_get_str("TELEGRAM_CHAT_ID", ""),
        sqlite_path=_get_str("SQLITE_PATH", ""),
        run_duration_sec=_get_float("RUN_DURATION_SEC", 0.0),
    )


def load_and_validate() -> Settings:
    """Load settings and immediately validate them, exiting on error."""
    try:
        s = load_settings()
    except (ValueError, TypeError) as exc:
        print(f"❌ Configuration error (bad environment variable): {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        s.validate()
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    return s


# Lazy singleton — call load_and_validate() in entry points (main.py, tests).
# This bare version is kept for backwards-compat module inspection only.
settings = load_settings()
