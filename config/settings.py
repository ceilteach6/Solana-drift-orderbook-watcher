"""
config/settings.py

Loads configuration from environment variables (and a local ``.env`` file if
present, via python-dotenv). Mirrors the keys documented in
``config.example.env``.

Import the ready-to-use singleton:

    from config.settings import settings
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads a local .env if one exists; harmless otherwise
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _validate_webhook_url(url: str, *, allow_private_host: bool) -> None:
    """Fail fast on a misconfigured/unsafe webhook URL rather than letting a
    bad ``.env`` value silently fire requests at an unintended target (e.g.
    a `file://` handler misuse, or the cloud metadata endpoint) at runtime.
    """
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"ALERT_WEBHOOK_URL must use http:// or https:// (got {parsed.scheme!r})"
        )
    if not parsed.hostname:
        raise ValueError("ALERT_WEBHOOK_URL is missing a host")
    if allow_private_host:
        return
    if _is_loopback_hostname(parsed.hostname):
        raise ValueError(
            f"ALERT_WEBHOOK_URL points at a private/internal address ({parsed.hostname}); "
            "set ALERT_WEBHOOK_ALLOW_PRIVATE_HOST=true if this is intentional"
        )
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return  # a DNS hostname; can't cheaply classify without a lookup
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise ValueError(
            f"ALERT_WEBHOOK_URL points at a private/internal address ({parsed.hostname}); "
            "set ALERT_WEBHOOK_ALLOW_PRIVATE_HOST=true if this is intentional"
        )


_LOOPBACK_HOSTNAMES = {"localhost"}


def _is_loopback_hostname(hostname: str) -> bool:
    # ``localhost`` (and ``*.localhost``, reserved for loopback use by RFC
    # 6761) resolve to a loopback address without a DNS lookup, so the
    # IP-literal check above alone lets them slip through the SSRF guard.
    hostname = hostname.lower()
    return hostname in _LOOPBACK_HOSTNAMES or hostname.endswith(".localhost")


def _require_positive_int(name: str, value: int) -> None:
    if value < 1:
        raise ValueError(f"{name} must be >= 1 (got {value})")


def _require_positive_float(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be > 0 (got {value})")


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
    # A tuple, not a list: ``frozen=True`` only blocks reassigning the field,
    # not mutating a mutable object held by it — a list here would let
    # ``settings.markets.append(...)`` silently corrupt the "immutable" config.
    markets: tuple[str, ...] = field(default_factory=tuple)
    orderbook_depth: int = 20
    update_frequency_ms: int = 1000
    snapshot_timeout_sec: float = 5.0

    # --- Detector thresholds ---
    repeated_min_count: int = 4
    repeated_size_tolerance: float = 0.001
    layering_min_levels: int = 5
    flicker_window_sec: float = 5.0
    flicker_min_events: int = 3
    imbalance_min_ratio: float = 0.85
    imbalance_min_levels: int = 5
    imbalance_min_total_volume: float = 0.0  # 0 = disabled (no liquidity floor)
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
    alert_webhook_allow_private_host: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Run control ---
    run_duration_sec: float = 0.0

    def __post_init__(self) -> None:
        # Hysteresis in RiskAggregator requires clear < alert, or the
        # "alerting" state never clears and cooldown becomes the only gate.
        if self.risk_clear_threshold >= self.risk_alert_threshold:
            raise ValueError(
                "RISK_CLEAR_THRESHOLD must be < RISK_ALERT_THRESHOLD "
                f"(got clear={self.risk_clear_threshold}, alert={self.risk_alert_threshold})"
            )
        _validate_webhook_url(
            self.alert_webhook_url,
            allow_private_host=self.alert_webhook_allow_private_host,
        )
        # Every detector normalizes its hit-count into a 0..1 score via
        # ``count / (min_threshold * 2)``; a zero or negative threshold
        # turns that guard into a division by zero (or, for layering's
        # ``clusters[0]`` lookup, an IndexError) on the very first tick.
        # Reject these at startup instead of letting a single bad env var
        # silently kill one detector for the life of the process.
        _require_positive_int("REPEATED_MIN_COUNT", self.repeated_min_count)
        _require_positive_int("LAYERING_MIN_LEVELS", self.layering_min_levels)
        _require_positive_int("FLICKER_MIN_EVENTS", self.flicker_min_events)
        _require_positive_int("IMBALANCE_MIN_LEVELS", self.imbalance_min_levels)
        _require_positive_float("SPOOF_MIN_PRICE_MOVE", self.spoof_min_price_move)
        if not 0.0 < self.risk_smoothing <= 1.0:
            raise ValueError(
                f"RISK_SMOOTHING must be in (0, 1] (got {self.risk_smoothing}); "
                "an out-of-range EMA alpha makes the risk score unstable"
            )


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
        snapshot_timeout_sec=_get_float("SNAPSHOT_TIMEOUT_SEC", 5.0),
        repeated_min_count=_get_int("REPEATED_MIN_COUNT", 4),
        repeated_size_tolerance=_get_float("REPEATED_SIZE_TOLERANCE", 0.001),
        layering_min_levels=_get_int("LAYERING_MIN_LEVELS", 5),
        flicker_window_sec=_get_float("FLICKER_WINDOW_SEC", 5.0),
        flicker_min_events=_get_int("FLICKER_MIN_EVENTS", 3),
        imbalance_min_ratio=_get_float("IMBALANCE_MIN_RATIO", 0.85),
        imbalance_min_levels=_get_int("IMBALANCE_MIN_LEVELS", 5),
        imbalance_min_total_volume=_get_float("IMBALANCE_MIN_TOTAL_VOLUME", 0.0),
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
        alert_webhook_allow_private_host=_get_str(
            "ALERT_WEBHOOK_ALLOW_PRIVATE_HOST", "false"
        ).lower() in ("1", "true", "yes", "on"),
        telegram_bot_token=_get_str("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_get_str("TELEGRAM_CHAT_ID", ""),
        run_duration_sec=_get_float("RUN_DURATION_SEC", 0.0),
    )


# Ready-to-use singleton.
settings = load_settings()
