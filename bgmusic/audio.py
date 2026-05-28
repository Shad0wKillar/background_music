"""PulseAudio / PipeWire sink detection.

other_audio_is_playing() is called every CHECK_INTERVAL seconds by the
monitoring loop to decide whether to pause background music.  It iterates
the active sink inputs and returns True if any non-internal, non-ignored
stream is un-corked (i.e. actively playing audio).

'Internal' means our own mpv or keyboard-sound stream.
'Ignored' means the user added the app/media/binary to audio_detection
in config.yaml.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from bgmusic.config import string_list_setting
from bgmusic.constants import KEYBOARD_APP_NAME, MY_APP_NAME
from bgmusic.debug import DebugLogger

try:
    import pulsectl
except ImportError:
    pulsectl = None  # type: ignore[assignment]


def ensure_start_dependencies() -> None:
    """Raise early if pulsectl is missing — we need it for audio detection."""
    if pulsectl is None:
        raise RuntimeError(
            "pulsectl is required for audio detection. "
            "Install dependencies with: uv pip install -r requirements.txt"
        )


# ---------------------------------------------------------------------------
# Sink property helpers
# ---------------------------------------------------------------------------

def proplist_value(sink: Any, key: str) -> str:
    value = sink.proplist.get(key)
    return "" if value is None else str(value)


def sink_index(sink: Any) -> int | None:
    index = getattr(sink, "index", None)
    return index if isinstance(index, int) else None


def describe_sink(sink: Any) -> str:
    """One-line description of a sink for debug logs."""
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


# ---------------------------------------------------------------------------
# Ignore checks
# ---------------------------------------------------------------------------

def is_config_ignored_audio_sink(sink: Any, config: dict[str, Any]) -> bool:
    """Return True if the sink matches an ignore rule from config.yaml."""
    detection = config.get("audio_detection", {})
    if not isinstance(detection, dict):
        return False
    app_name     = proplist_value(sink, "application.name")
    media_name   = proplist_value(sink, "media.name")
    proc_binary  = Path(proplist_value(sink, "application.process.binary")).name
    return (
        matches_any_pattern(app_name,    string_list_setting(detection.get("ignore_app_names")))
        or matches_any_pattern(media_name,   string_list_setting(detection.get("ignore_media_names")))
        or matches_any_pattern(proc_binary,  string_list_setting(detection.get("ignore_process_binaries")))
    )


def is_internal_audio_sink(
    sink: Any,
    ignored_process_ids: set[str],
    ignored_sink_indexes: set[int],
) -> bool:
    """Return True if this sink belongs to the daemon itself."""
    app_name    = proplist_value(sink, "application.name")
    media_name  = proplist_value(sink, "media.name")
    process_id  = proplist_value(sink, "application.process.id")
    proc_binary = Path(proplist_value(sink, "application.process.binary")).name
    index = sink_index(sink)

    if index is not None and index in ignored_sink_indexes:
        return True
    if app_name in {MY_APP_NAME, KEYBOARD_APP_NAME}:
        return True
    if media_name == KEYBOARD_APP_NAME:
        return True
    if process_id and process_id in ignored_process_ids:
        return True
    if proc_binary in {"bgmusic.py"}:
        return True
    return False


# ---------------------------------------------------------------------------
# Sink snapshot (used at startup to baseline the keyboard-sound stream)
# ---------------------------------------------------------------------------

def snapshot_sink_indexes(logger: DebugLogger) -> set[int]:
    """Return the set of all current sink-input indexes."""
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


# ---------------------------------------------------------------------------
# Per-loop external-audio check
# ---------------------------------------------------------------------------

class AudioDebugState:
    """Tracks which sinks have already been logged to avoid duplicate lines."""
    def __init__(self) -> None:
        self.seen_internal_sinks: set[int] = set()
        self.seen_config_ignored_sinks: set[int] = set()
        self.external_trigger: str | None = None


def other_audio_is_playing(
    pulse: Any,
    config: dict[str, Any],
    ignored_process_ids: set[str],
    ignored_sink_indexes: set[int],
    logger: DebugLogger,
    debug_state: AudioDebugState,
) -> tuple[bool, str | None]:
    """Return (is_playing, description) for the first external un-corked sink."""
    for sink in pulse.sink_input_list():
        index = sink_index(sink)
        if is_internal_audio_sink(sink, ignored_process_ids, ignored_sink_indexes):
            if logger.enabled and index is not None and index not in debug_state.seen_internal_sinks:
                debug_state.seen_internal_sinks.add(index)
                logger.log(f"ignoring internal audio sink: {describe_sink(sink)}")
            continue
        if is_config_ignored_audio_sink(sink, config):
            if logger.enabled and index is not None and index not in debug_state.seen_config_ignored_sinks:
                debug_state.seen_config_ignored_sinks.add(index)
                logger.log(f"ignoring configured audio sink: {describe_sink(sink)}")
            continue
        if not sink.corked:
            return True, describe_sink(sink)
    return False, None
