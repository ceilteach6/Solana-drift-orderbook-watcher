"""
src/alert/console_alert.py

Outputs detections to stdout, either as human-readable lines or as JSON
(one object per line). Detections below ``ALERT_MIN_SCORE`` are suppressed.
"""

from __future__ import annotations

import json
import time

_ICONS = {
    "repeated_size": "♻️",
    "layering": "🧱",
    "flicker": "✨",
}


class ConsoleAlert:
    def __init__(self, settings) -> None:
        self.settings = settings

    def emit(self, detections) -> int:
        """Print qualifying detections. Returns how many were emitted."""
        emitted = 0
        for d in detections:
            if d.score < self.settings.alert_min_score:
                continue
            if self.settings.alert_format == "json":
                self._emit_json(d)
            else:
                self._emit_console(d)
            emitted += 1
        return emitted

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
