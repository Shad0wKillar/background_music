"""Logging and timing utilities.

DebugLogger gates verbose output behind --debug / --deep-debug flags.
The timing helpers convert evdev kernel timestamps to millisecond deltas
for the --deep-debug latency report.
"""
from __future__ import annotations

import time
from typing import Any


class DebugLogger:
    """Simple logger that only prints when the relevant flag is set."""

    def __init__(self, enabled: bool, deep_enabled: bool = False) -> None:
        self.deep_enabled = deep_enabled
        # deep mode implies normal debug mode
        self.enabled = enabled or deep_enabled

    def log(self, message: str) -> None:
        if self.enabled:
            print(f"[debug {time.strftime('%H:%M:%S')}] {message}", flush=True)

    def deep(self, message: str) -> None:
        if self.deep_enabled:
            print(f"[deep {time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Kernel-timestamp → millisecond helpers (used by KeyboardMonitor)
# ---------------------------------------------------------------------------

def event_timestamp_seconds(event: Any) -> float | None:
    """Extract the kernel timestamp from an evdev event as a Unix float."""
    try:
        return float(event.timestamp())
    except Exception:
        try:
            return float(event.sec) + (float(event.usec) / 1_000_000.0)
        except Exception:
            return None


def kernel_to_user_ms(
    event_timestamp: float | None,
    receipt_wall: float,
    receipt_mono: float,
) -> float | None:
    """Return kernel→receipt latency in ms, or None if the clock looks wrong."""
    if event_timestamp is None:
        return None
    for reference in (receipt_wall, receipt_mono):
        delta = (reference - event_timestamp) * 1000.0
        # Sanity-check: reject if clocks differ by more than a second.
        if -1000.0 <= delta <= 1000.0:
            return delta
    return None


def format_ms(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2f}ms"
