"""
src/alert/console_alert.py

Console alert sink: prints each detection to stdout, either human-readable or
as JSON (one object per line), controlled by ``ALERT_FORMAT``.

Score filtering is handled upstream by :class:`AlertDispatcher`.
"""

from __future__ import annotations

import json
import time

from src.alert.base import Alert

_ICONS = {
    "repeated_size": "♻️",
    "layering": "🧱",
    "flicker": "✨",
    "imbalance": "⚖️",
    "spoof_pull": "🎣",
    "risk": "🚨",
    "risk_aggregator": "🚨",
    "healthcheck": "🔬",
}


class ConsoleAlert(Alert):
    name = "console"

    def deliver(self, detection) -> None:
        if self.settings.alert_format == "json":
            self._emit_json(detection)
        else:
            self._emit_console(detection)

    def _emit_console(self, d) -> None:
        icon = _ICONS.get(d.detector, "⚠️")
        ts = time.strftime("%H:%M:%S")
        print(
            f"{icon}  [{ts}] {d.market} | {d.detector} "
            f"(score {d.score:.2f}) — {d.message}"
        )

    def _emit_json(self, d) -> None:
        print(
            json.dumps(
                {
                    "ts": time.time(),
                    "market": d.market,
                    "detector": d.detector,
                    "score": round(d.score, 4),
                    "message": d.message,
                    "details": d.details,
                }
            )
        )
