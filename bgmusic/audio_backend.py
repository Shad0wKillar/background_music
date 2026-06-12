"""PulseAudio / PipeWire backend helpers."""
from __future__ import annotations

import fnmatch
from typing import Any

from bgmusic.debug import DebugLogger

try:
    import pulsectl
except ImportError:
    pulsectl = None  # type: ignore[assignment]


def ensure_start_dependencies() -> None:
    if pulsectl is None:
        raise RuntimeError(
            "pulsectl is required for audio detection. "
            "Install dependencies with: uv pip install -r requirements.txt"
        )


def proplist_value(sink: Any, key: str) -> str:
    value = sink.proplist.get(key)
    return "" if value is None else str(value)


def sink_index(sink: Any) -> int | None:
    index = getattr(sink, "index", None)
    return index if isinstance(index, int) else None


def sink_corked(sink: Any) -> bool:
    return bool(getattr(sink, "corked", False))


def describe_sink(sink: Any) -> str:
    return (
        f"index={sink_index(sink)} "
        f"app={proplist_value(sink, 'application.name') or 'unknown'} "
        f"media={proplist_value(sink, 'media.name') or 'unknown'} "
        f"role={proplist_value(sink, 'media.role') or 'unknown'} "
        f"pid={proplist_value(sink, 'application.process.id') or 'unknown'} "
        f"binary={proplist_value(sink, 'application.process.binary') or 'unknown'} "
        f"corked={getattr(sink, 'corked', 'unknown')}"
    )


def matches_any_pattern(value: str, patterns: list[str]) -> bool:
    normalized = value.casefold()
    return any(fnmatch.fnmatchcase(normalized, p.casefold()) for p in patterns)


def snapshot_sink_indexes(logger: DebugLogger) -> set[int]:
    try:
        with pulsectl.Pulse("bg-music-sink-snapshot") as pulse:
            indexes = {
                idx
                for sink in pulse.sink_input_list()
                if (idx := sink_index(sink)) is not None
            }
            logger.log(f"sink snapshot: {sorted(indexes)}")
            return indexes
    except Exception as error:
        logger.log(f"sink snapshot failed: {error}")
        return set()
