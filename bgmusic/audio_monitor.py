"""Per-loop external-audio checks."""
from __future__ import annotations

from typing import Any

from bgmusic.audio_backend import describe_sink, sink_index
from bgmusic.audio_ignores import (
    config_ignore_reason, is_internal_audio_sink, is_user_ignored_audio_sink,
)
from bgmusic.debug import DebugLogger


class AudioDebugState:
    """Tracks which sinks have already been logged."""

    def __init__(self) -> None:
        self.seen_internal_sinks: set[int] = set()
        self.seen_config_ignored_sinks: set[int] = set()
        self.seen_user_ignored_sinks: set[int] = set()
        self.external_trigger: str | None = None


def log_once(
    seen: set[int],
    index: int | None,
    logger: DebugLogger,
    message: str,
) -> None:
    if logger.enabled and index is not None and index not in seen:
        seen.add(index)
        logger.log(message)


def other_audio_is_playing(
    pulse: Any,
    config: dict[str, Any],
    state: dict[str, Any],
    ignored_process_ids: set[str],
    ignored_sink_indexes: set[int],
    logger: DebugLogger,
    debug_state: AudioDebugState,
) -> tuple[bool, str | None]:
    """Return (is_playing, description) for the first external un-corked sink."""
    for sink in pulse.sink_input_list():
        index = sink_index(sink)
        if is_internal_audio_sink(sink, ignored_process_ids, ignored_sink_indexes):
            log_once(debug_state.seen_internal_sinks, index, logger,
                     f"ignoring internal audio sink: {describe_sink(sink)}")
            continue

        reason = config_ignore_reason(sink, config)
        if reason is not None:
            log_once(debug_state.seen_config_ignored_sinks, index, logger,
                     f"ignoring configured audio sink ({reason}): {describe_sink(sink)}")
            continue

        if is_user_ignored_audio_sink(sink, state):
            log_once(debug_state.seen_user_ignored_sinks, index, logger,
                     f"ignoring user-selected audio sink: {describe_sink(sink)}")
            continue

        if not sink.corked:
            return True, describe_sink(sink)
    return False, None
