"""Compatibility facade for audio detection helpers."""
from __future__ import annotations

from bgmusic.audio_backend import (
    describe_sink, ensure_start_dependencies, matches_any_pattern,
    proplist_value, sink_index, snapshot_sink_indexes,
)
from bgmusic.audio_ignores import (
    audio_source_key_matches_sink, configured_audio_source_keys,
    is_config_ignored_audio_sink, is_internal_audio_sink,
    is_user_ignored_audio_sink,
)
from bgmusic.audio_monitor import AudioDebugState, other_audio_is_playing
from bgmusic.audio_sources import (
    audio_source_label, audio_sources_snapshot, known_audio_source_keys,
    make_audio_source_key, preferred_audio_source_key, split_audio_source_key,
)

__all__ = [
    "AudioDebugState", "audio_source_key_matches_sink", "audio_source_label",
    "audio_sources_snapshot", "configured_audio_source_keys", "describe_sink",
    "ensure_start_dependencies", "is_config_ignored_audio_sink",
    "is_internal_audio_sink", "is_user_ignored_audio_sink",
    "known_audio_source_keys", "make_audio_source_key", "matches_any_pattern",
    "other_audio_is_playing", "preferred_audio_source_key", "proplist_value",
    "sink_index", "snapshot_sink_indexes", "split_audio_source_key",
]
