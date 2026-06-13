"""Latency probe helpers for keyboard sound diagnostics."""
from __future__ import annotations

import time
from typing import Any

from bgmusic.debug import format_ms


def make_latency_probe(
    logger: Any,
    key: str,
    event_value: int,
    receipt_ns: int | None,
    kernel_ms: float | None,
    count: int,
    limit: int,
) -> tuple[dict[str, Any] | None, int]:
    """Create a latency probe and return the updated probe count."""
    if not logger.deep_enabled or count >= limit:
        return None, count
    enqueue_ns = time.perf_counter_ns()
    receipt_ns = receipt_ns or enqueue_ns
    return {
        "key": key,
        "event_value": event_value,
        "receipt_ns": receipt_ns,
        "enqueue_ns": enqueue_ns,
        "kernel_to_receipt_ms": kernel_ms,
        "receipt_to_enqueue_ms": (enqueue_ns - receipt_ns) / 1_000_000.0,
    }, count + 1


def format_latency_probe(probe: dict[str, Any], time_info: Any, frames: int) -> str:
    """Format callback timing for debug logs."""
    callback_ns = time.perf_counter_ns()
    current_time = getattr(time_info, "currentTime", None)
    dac_time = getattr(time_info, "outputBufferDacTime", None)
    cb_to_dac_ms = None
    if isinstance(current_time, (int, float)) and isinstance(dac_time, (int, float)):
        cb_to_dac_ms = max(0.0, (dac_time - current_time) * 1000.0)
    rcpt_to_cb_ms = (callback_ns - probe["receipt_ns"]) / 1_000_000.0
    rcpt_to_dac_ms = None if cb_to_dac_ms is None else rcpt_to_cb_ms + cb_to_dac_ms
    return (
        f"key={probe['key']} event={probe['event_value']} "
        f"kernel->receipt={format_ms(probe['kernel_to_receipt_ms'])} "
        f"receipt->enqueue={format_ms(probe['receipt_to_enqueue_ms'])} "
        f"enqueue->callback={(callback_ns - probe['enqueue_ns']) / 1_000_000.0:.2f}ms "
        f"receipt->callback={rcpt_to_cb_ms:.2f}ms "
        f"callback->dac={format_ms(cb_to_dac_ms)} "
        f"receipt->estimated_dac={format_ms(rcpt_to_dac_ms)} frames={frames}"
    )
