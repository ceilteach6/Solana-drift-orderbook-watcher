"""
src/replay/engine.py

Replay / backtesting engine (roadmap: "Replay / backtesting").

Feeds L2 snapshots recorded by the SQLite store (``PERSIST_SNAPSHOTS=true``)
back through the *real* detector stack and risk aggregator — offline, with no
network and no alert sinks — and reports what would have fired. Because the
engine takes a ``Settings`` instance, the same stored day can be replayed
under different thresholds, which is what the parameter sweep uses for tuning:

    python main.py --replay
    python main.py --replay --market SOL-PERP --since 2026-07-01
    python main.py --replay --sweep risk_alert_threshold=0.4,0.5,0.6,0.7

Timestamps come from the stored rows, not the wall clock, so time-windowed
detectors (flicker, spoof-pull) and the aggregator's cooldown behave exactly
as they did live, regardless of how fast the replay itself runs.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore


def snapshot_from_row(row) -> OrderbookSnapshot:
    """Rebuild an :class:`OrderbookSnapshot` from a stored ``snapshots`` row."""
    def levels(raw) -> list[Level]:
        return [Level(price, size) for price, size in json.loads(raw or "[]")]

    return OrderbookSnapshot(
        market=row["market"],
        timestamp=row["ts"],
        bids=levels(row["bids"]),
        asks=levels(row["asks"]),
    )


@dataclass
class DetectorStats:
    count: int = 0
    max_score: float = 0.0
    _score_sum: float = 0.0

    def add(self, score: float) -> None:
        self.count += 1
        self.max_score = max(self.max_score, score)
        self._score_sum += score

    @property
    def mean_score(self) -> float:
        return self._score_sum / self.count if self.count else 0.0


@dataclass
class ReplayReport:
    """What one pass over the stored snapshots produced."""

    market: str
    snapshots: int = 0
    t_start: float | None = None
    t_end: float | None = None
    detections: dict[str, DetectorStats] = field(default_factory=dict)
    # Risk-aggregation results (None when RISK_AGGREGATION is off).
    risk_alerts: int | None = None
    max_risk: float | None = None
    mean_risk: float | None = None
    elevated_fraction: float | None = None  # ticks with risk >= alert threshold

    @property
    def total_detections(self) -> int:
        return sum(s.count for s in self.detections.values())

    def format(self) -> str:
        lines = [f"▶️  Replay — {self.market}"]
        if not self.snapshots:
            lines.append("   no stored snapshots in the selected range")
            return "\n".join(lines)
        span = (self.t_end or 0) - (self.t_start or 0)
        lines.append(
            f"   snapshots : {self.snapshots} "
            f"({_fmt_ts(self.t_start)} → {_fmt_ts(self.t_end)}, {span:.0f}s)"
        )
        if self.detections:
            lines.append(f"   detections: {self.total_detections}")
            width = max(len(name) for name in self.detections)
            for name in sorted(self.detections):
                s = self.detections[name]
                lines.append(
                    f"     {name.ljust(width)}  x{s.count:<5d} "
                    f"max {s.max_score:.2f}  mean {s.mean_score:.2f}"
                )
        else:
            lines.append("   detections: none")
        if self.risk_alerts is not None:
            lines.append(
                f"   risk      : {self.risk_alerts} alert(s), "
                f"max {self.max_risk:.2f}, mean {self.mean_risk:.2f}, "
                f"elevated {self.elevated_fraction:.0%} of ticks"
            )
        return "\n".join(lines)


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class ReplayEngine:
    """Runs stored snapshots through detectors + risk aggregator, offline."""

    def __init__(self, settings, store: SQLiteStore) -> None:
        self.settings = settings
        self.store = store

    def run(self, market: str, start: float | None = None,
            end: float | None = None) -> ReplayReport:
        settings = self.settings
        detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
        aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
        report = ReplayReport(market=market)

        # Time-based history trimming: unlike the live watcher (which sizes
        # its deque from UPDATE_FREQUENCY_MS), recorded data may have any
        # cadence, so retention is keyed on the snapshots' own timestamps —
        # keep 1.5x the widest detector lookback window.
        keep_sec = 1.5 * max(settings.flicker_window_sec, settings.spoof_window_sec)
        history: deque[OrderbookSnapshot] = deque()

        risk_sum = 0.0
        elevated = 0

        for row in self.store.iter_snapshots(market, start, end):
            snapshot = snapshot_from_row(row)
            report.snapshots += 1
            if report.t_start is None:
                report.t_start = snapshot.timestamp
            report.t_end = snapshot.timestamp

            while len(history) > 8 and history[0].timestamp < snapshot.timestamp - keep_sec:
                history.popleft()

            detections = []
            for detector in detectors:
                detections.extend(detector.analyze(snapshot, history))
            history.append(snapshot)

            for d in detections:
                report.detections.setdefault(d.detector, DetectorStats()).add(d.score)

            if aggregator is not None:
                if aggregator.update(market, snapshot.timestamp, detections) is not None:
                    report.risk_alerts = (report.risk_alerts or 0) + 1
                score = aggregator.score(market)
                risk_sum += score
                report.max_risk = max(report.max_risk or 0.0, score)
                if score >= settings.risk_alert_threshold:
                    elevated += 1

        if aggregator is not None and report.snapshots:
            report.risk_alerts = report.risk_alerts or 0
            report.max_risk = report.max_risk or 0.0
            report.mean_risk = risk_sum / report.snapshots
            report.elevated_fraction = elevated / report.snapshots
        return report

    def sweep(self, market: str, param: str, values,
              start: float | None = None, end: float | None = None):
        """Replay the same range once per parameter value (threshold tuning).

        Returns ``[(value, ReplayReport | error-string), ...]``. A value that
        produces an invalid Settings combination (e.g. a clear threshold above
        the alert threshold) is reported as its error instead of aborting the
        rest of the sweep.
        """
        base_value = getattr(self.settings, param)  # AttributeError on typos
        if not isinstance(base_value, (int, float)) or isinstance(base_value, bool):
            raise ValueError(f"--sweep only supports numeric settings, {param!r} "
                             f"is {type(base_value).__name__}")
        results = []
        for value in values:
            try:
                cast = type(base_value)(value)
                settings = dataclasses.replace(self.settings, **{param: cast})
            except ValueError as exc:
                results.append((value, str(exc)))
                continue
            engine = ReplayEngine(settings, self.store)
            results.append((cast, engine.run(market, start, end)))
        return results


# --------------------------------------------------------------------------- #
# CLI (invoked by ``python main.py --replay ...``)
# --------------------------------------------------------------------------- #
def _parse_when(raw: str) -> float:
    """Accept epoch seconds or ISO dates ('2026-07-01', '2026-07-01T14:30')."""
    try:
        return float(raw)
    except ValueError:
        return datetime.fromisoformat(raw).timestamp()


def _parse_sweep(raw: str) -> tuple[str, list[float]]:
    param, _, values = raw.partition("=")
    if not param or not values:
        raise ValueError("--sweep expects PARAM=v1,v2,... "
                         "(e.g. risk_alert_threshold=0.4,0.5,0.6)")
    return param.strip(), [float(v) for v in values.split(",") if v.strip()]


def _format_sweep(param: str, results) -> str:
    lines = [f"🎛️  Sweep — {param}"]
    header = (f"   {'value':>8}  {'detections':>10}  {'risk alerts':>11}  "
              f"{'max risk':>8}  {'elevated':>8}")
    lines.append(header)
    for value, outcome in results:
        if isinstance(outcome, str):
            lines.append(f"   {value:>8}  skipped: {outcome}")
            continue
        r = outcome
        risk_alerts = "-" if r.risk_alerts is None else str(r.risk_alerts)
        max_risk = "-" if r.max_risk is None else f"{r.max_risk:.2f}"
        elevated = "-" if r.elevated_fraction is None else f"{r.elevated_fraction:.0%}"
        lines.append(f"   {value:>8g}  {r.total_detections:>10}  "
                     f"{risk_alerts:>11}  {max_risk:>8}  {elevated:>8}")
    return "\n".join(lines)


def run_replay(settings, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="main.py --replay",
        description="Replay stored L2 snapshots through the detector stack.",
    )
    parser.add_argument("--market", help="market to replay (default: every "
                                         "market found in the snapshots table)")
    parser.add_argument("--since", type=_parse_when, metavar="WHEN",
                        help="epoch seconds or ISO date/datetime (inclusive)")
    parser.add_argument("--until", type=_parse_when, metavar="WHEN",
                        help="epoch seconds or ISO date/datetime (inclusive)")
    parser.add_argument("--sweep", metavar="PARAM=v1,v2,...",
                        help="replay once per value of a numeric setting "
                             "(e.g. risk_alert_threshold=0.4,0.5,0.6)")
    args = parser.parse_args(argv)

    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        markets = [args.market] if args.market else store.snapshot_markets()
        if not markets:
            print(f"No stored snapshots in {settings.db_path}.\n"
                  "Record some first: STORAGE_ENABLED=true PERSIST_SNAPSHOTS=true "
                  "python main.py")
            return 1

        engine = ReplayEngine(settings, store)
        replayed_any = False
        for market in markets:
            if args.sweep:
                try:
                    param, values = _parse_sweep(args.sweep)
                    results = engine.sweep(market, param, values,
                                           args.since, args.until)
                except (ValueError, AttributeError) as exc:
                    print(f"Invalid --sweep: {exc}")
                    return 2
                print(f"▶️  Replay — {market}")
                print(_format_sweep(param, results))
                replayed_any = replayed_any or any(
                    not isinstance(r, str) and r.snapshots for _, r in results
                )
            else:
                report = engine.run(market, args.since, args.until)
                print(report.format())
                replayed_any = replayed_any or bool(report.snapshots)
            print()
        return 0 if replayed_any else 1
    finally:
        store.close()
