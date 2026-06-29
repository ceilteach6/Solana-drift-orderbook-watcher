"""
src/alert/console_alert.py

Console alert sink.  Outputs detections to stdout as human-readable lines or
as JSON (one object per line), depending on ``settings.alert_format``.
"""

from __future__ import annotations

import json
import time

from src.alert.base import Alert

_ICONS = {
    "repeated_size": "♻️ ",
    "layering": "🧱",
    "flicker": "✨",
    "imbalance": "⚖️ ",
    "volume_spike": "📊",
}


class ConsoleAlert(Alert):
    name = "console"

    async def deliver(self, detection) -> None:
        if self.settings.alert_format == "json":
            self._emit_json(detection)
        else:
            self._emit_console(detection)

    def _emit_console(self, d) -> None:
        icon = _ICONS.get(d.detector, "⚠️ ")
        ts = time.strftime("%H:%M:%S")
        print(
            f"{icon} [{ts}] {d.market} | {d.detector} "
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
