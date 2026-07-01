"""
src/selftest.py

Algorithmic self-test: deterministically injects a *known* manipulation pattern
for every detector, runs it through the real detector stack (and the risk
aggregator), and verifies the matching detector actually fires.

Unlike the pytest unit tests, this runs against the live configuration and can
be invoked at any time:

    python main.py --selftest        # report + non-zero exit on failure

It is also reused as the in-process health-check (see src/watcher.py): probing
the detectors with known-positive inputs is robust, because it does not depend
on whatever the live market happens to be doing.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.detector.base import Detection
from src.risk import RiskAggregator

_MARKET = "SELFTEST"


@dataclass
class SelfTestResult:
    name: str
    passed: bool
    score: float
    note: str = ""


# --------------------------------------------------------------------------- #
# Scenario builders — each returns a tick sequence (oldest -> newest).
# --------------------------------------------------------------------------- #
def _snap(ts, bids, asks):
    return OrderbookSnapshot(
        market=_MARKET,
        timestamp=ts,
        bids=[Level(p, s) for p, s in bids],
        asks=[Level(p, s) for p, s in asks],
    )


def _sc_repeated_size():
    # 7 orders share the same size -> repeated-size signature.
    return [_snap(
        0.0,
        bids=[(100.0, 10.0), (99.9, 10.0), (99.8, 10.0), (99.7, 10.0)],
        asks=[(100.1, 10.0), (100.2, 10.0), (100.3, 10.0)],
    )]


def _sc_layering():
    # A 7-level one-sided wall of equal sizes.
    return [_snap(
        0.0,
        bids=[(100.0 - i * 0.1, 25.0) for i in range(7)],
        asks=[(100.1, 1.0), (100.2, 2.0)],
    )]


def _sc_flicker():
    # A level toggles present/absent across 6 snapshots within the window.
    snaps = []
    for i in range(6):
        present = i % 2 == 0
        bids = [(100.0, 5.0)] if present else [(99.0, 5.0)]
        snaps.append(_snap(i * 0.5, bids, [(101.0, 1.0)]))
    return snaps


def _sc_imbalance():
    # Top levels heavily stacked on the bid side.
    return [_snap(
        0.0,
        bids=[(100.0 - i, 100.0) for i in range(5)],
        asks=[(101.0 + i, 1.0) for i in range(5)],
    )]


def _sc_spoof_pull():
    # A big bid wall, then it is pulled while the mid moves up.
    prior = _snap(
        0.0,
        bids=[(99.9, 1.0), (99.8, 1.0), (99.7, 1.0), (99.5, 50.0)],
        asks=[(100.1, 1.0), (100.2, 1.0)],
    )
    current = _snap(
        1.0,
        bids=[(100.2, 1.0), (100.1, 1.0), (100.0, 1.0)],
        asks=[(100.4, 1.0), (100.5, 1.0)],
    )
    return [prior, current]


_SCENARIOS = {
    "repeated_size": _sc_repeated_size,
    "layering": _sc_layering,
    "flicker": _sc_flicker,
    "imbalance": _sc_imbalance,
    "spoof_pull": _sc_spoof_pull,
}


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def _safe_analyze(detector, snapshot, history) -> tuple[list[Detection], str]:
    """Run one detector, converting a raised exception into an error string.

    A broken detector must fail its own self-test/health-check entry, not
    crash the whole run — this is invoked from the live periodic health-check
    in src/watcher.py, where an uncaught exception would take down the
    watcher's main loop.
    """
    try:
        return detector.analyze(snapshot, history), ""
    except Exception as exc:
        return [], f"raised {exc.__class__.__name__}: {exc}"


def run_selftest(settings) -> list[SelfTestResult]:
    """Run every scenario through the real stack; return per-target results."""
    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    results: list[SelfTestResult] = []
    # Keyed by the *offending* detector's own name, not by whichever
    # scenario happened to be running when it raised — every scenario
    # exercises the full detector stack (not just its own target), so an
    # unrelated detector's crash must not be misattributed to this scenario.
    detector_errors: dict[str, str] = {}

    for target, build in _SCENARIOS.items():
        fired = False
        max_score = 0.0
        history: list = []
        for snapshot in build():
            for detector in detectors:
                detections, err = _safe_analyze(detector, snapshot, history)
                if err:
                    detector_errors.setdefault(detector.name, err)
                for d in detections:
                    if d.detector == target:
                        fired = True
                        max_score = max(max_score, d.score)
            history.append(snapshot)
        error = detector_errors.get(target, "")
        note = error or ("" if fired else "did not fire on its scenario")
        results.append(SelfTestResult(target, fired and not error, round(max_score, 3), note))

    results.append(_test_risk_aggregator(settings, detectors))
    return results


def _test_risk_aggregator(settings, detectors) -> SelfTestResult:
    aggregator = RiskAggregator(settings)
    snapshot = _sc_imbalance()[0]
    fired = False
    history: list = []
    for tick in range(8):
        detections: list = []
        for detector in detectors:
            found, _err = _safe_analyze(detector, snapshot, history)
            detections.extend(found)
        history.append(snapshot)
        if aggregator.update(_MARKET, float(tick), detections) is not None:
            fired = True
    note = "" if fired else "no consolidated risk alert from a sustained signal"
    return SelfTestResult("risk-aggregator", fired, round(aggregator.score(_MARKET), 3), note)


def format_report(results: list[SelfTestResult]) -> str:
    lines = ["🔬 Algorithmic self-test"]
    width = max(len(r.name) for r in results)
    for r in results:
        mark = "✅" if r.passed else "❌"
        detail = f"score {r.score:.2f}" if r.passed else r.note
        lines.append(f"   {r.name.ljust(width)}  {mark}  {detail}")
    passed = sum(r.passed for r in results)
    lines.append(f"Result: {passed}/{len(results)} passed")
    return "\n".join(lines)


def selftest_main(settings) -> int:
    """Run, print the report, and return a process exit code (0 = all passed)."""
    results = run_selftest(settings)
    print(format_report(results))
    return 0 if all(r.passed for r in results) else 1
