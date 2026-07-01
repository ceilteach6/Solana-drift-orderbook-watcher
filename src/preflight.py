"""
src/preflight.py

Go-live preflight: checks everything a *real* (non-synthetic) run needs and
prints an actionable report. Run it after filling in ``.env``:

    python main.py --preflight

Offline checks: Python version, driftpy importability, market specs / venues,
feed mode, storage path. Online check: a JSON-RPC roundtrip against
``RPC_URL`` (getHealth + getSlot). Exits non-zero if anything a live run
requires fails — each failure comes with a hint on how to fix it.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import dataclass

PUBLIC_RPC_HOSTS = ("api.mainnet-beta.solana.com", "api.devnet.solana.com")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    hint: str = ""


def check_python() -> CheckResult:
    ok = sys.version_info >= (3, 10)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return CheckResult("python", ok, version,
                       "" if ok else "driftpy requires Python 3.10+")


def check_driftpy() -> CheckResult:
    try:
        import driftpy  # noqa: F401
    except Exception as exc:
        return CheckResult(
            "driftpy", False, str(exc),
            "pip install -r requirements.txt — use a virtualenv; the "
            "Debian/Ubuntu system Python fails building some driftpy deps",
        )
    try:
        import importlib.metadata as metadata
        version = f"v{metadata.version('driftpy')}"
    except Exception:
        version = "installed"
    return CheckResult("driftpy", True, version)


def _rpc_call(url: str, method: str, timeout: float) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method}).encode()
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def check_rpc(settings, timeout: float = 8.0) -> CheckResult:
    is_public = any(host in settings.rpc_url for host in PUBLIC_RPC_HOSTS)
    public_hint = (
        "public endpoint: rate limits will throttle the DLOB websocket "
        "subscriptions — get a free key at https://dev.helius.xyz"
    ) if is_public else ""
    try:
        _rpc_call(settings.rpc_url, "getHealth", timeout)
        slot = _rpc_call(settings.rpc_url, "getSlot", timeout).get("result")
    except Exception as exc:
        return CheckResult(
            "rpc", False, f"{settings.rpc_url} unreachable: {exc}",
            "check RPC_URL / API key, and that outbound HTTPS *and* WSS to "
            "the endpoint are allowed by your network (policy/firewall)",
        )
    return CheckResult("rpc", True, f"{settings.rpc_url} — slot {slot}",
                       public_hint)


def check_markets(settings) -> CheckResult:
    from src.collector.orderbook_feed import VENUE_FEEDS, parse_market

    try:
        venues = sorted({parse_market(spec)[0] for spec in settings.markets})
    except ValueError as exc:
        return CheckResult("markets", False, str(exc))
    unknown = [v for v in venues if v not in VENUE_FEEDS]
    if unknown:
        return CheckResult(
            "markets", False, f"unknown venue(s): {', '.join(unknown)}",
            f"known venues: {', '.join(sorted(VENUE_FEEDS))}",
        )
    return CheckResult(
        "markets", True,
        f"{len(settings.markets)} market(s) via {', '.join(venues)}",
    )


def check_feed_mode(settings) -> CheckResult:
    mode = settings.feed_mode
    if mode == "synthetic":
        return CheckResult("feed mode", False, "synthetic (demo data only)",
                           "set FEED_MODE=live (or auto) to watch real data")
    if mode == "auto":
        return CheckResult(
            "feed mode", True, "auto",
            "silently falls back to synthetic data if the live feed fails — "
            "set FEED_MODE=live to hard-fail instead",
        )
    return CheckResult("feed mode", True, "live (no synthetic fallback)")


def check_storage(settings) -> CheckResult:
    if not settings.storage_enabled:
        return CheckResult(
            "storage", True, "disabled",
            "set STORAGE_ENABLED=true + PERSIST_SNAPSHOTS=true to record real "
            "data for --replay threshold tuning",
        )
    from src.storage import SQLiteStore

    try:
        store = SQLiteStore(settings.db_path)
        store.connect()
        store.close()
    except Exception as exc:
        return CheckResult("storage", False, f"{settings.db_path}: {exc}",
                           "check DB_PATH points to a writable location")
    snaps = " + snapshots" if settings.persist_snapshots else ""
    return CheckResult("storage", True, f"{settings.db_path} writable{snaps}")


def run_preflight(settings) -> list[CheckResult]:
    return [
        check_python(),
        check_driftpy(),
        check_rpc(settings),
        check_markets(settings),
        check_feed_mode(settings),
        check_storage(settings),
    ]


def format_report(results: list[CheckResult]) -> str:
    lines = ["🛫 Go-live preflight"]
    width = max(len(r.name) for r in results)
    for r in results:
        mark = "✅" if r.passed else "❌"
        lines.append(f"   {r.name.ljust(width)}  {mark}  {r.detail}")
        if r.hint:
            lines.append(f"   {' ' * width}      ↳ {r.hint}")
    passed = sum(r.passed for r in results)
    verdict = "ready to go live" if passed == len(results) else "NOT ready"
    lines.append(f"Result: {passed}/{len(results)} passed — {verdict}")
    return "\n".join(lines)


def preflight_main(settings) -> int:
    """Run, print the report, and return a process exit code (0 = ready)."""
    results = run_preflight(settings)
    print(format_report(results))
    return 0 if all(r.passed for r in results) else 1
