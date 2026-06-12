"""Audio sink ignore checks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bgmusic.audio_backend import matches_any_pattern, proplist_value, sink_index
from bgmusic.audio_rules import matching_rule_name
from bgmusic.config import string_list_setting
from bgmusic.constants import KEYBOARD_APP_NAME, MY_APP_NAME


def make_audio_source_key(kind: str, value: str) -> str:
    return f"{kind}:{value.strip()}"


def split_audio_source_key(key: str) -> tuple[str, str] | None:
    kind, sep, value = key.partition(":")
    if not sep or kind not in {"app", "media", "binary"} or not value.strip():
        return None
    return kind, value.strip()


def configured_audio_source_keys(config: dict[str, Any]) -> set[str]:
    detection = config.get("audio_detection", {})
    if not isinstance(detection, dict):
        return set()
    keys: set[str] = set()
    for value in string_list_setting(detection.get("ignore_app_names")):
        keys.add(make_audio_source_key("app", value))
    for value in string_list_setting(detection.get("ignore_media_names")):
        keys.add(make_audio_source_key("media", value))
    for value in string_list_setting(detection.get("ignore_process_binaries")):
        keys.add(make_audio_source_key("binary", value))
    return keys


def audio_source_key_matches_sink(key: str, sink: Any) -> bool:
    parsed = split_audio_source_key(key)
    if parsed is None:
        return False
    kind, pattern = parsed
    values = {
        "app": proplist_value(sink, "application.name"),
        "media": proplist_value(sink, "media.name"),
        "binary": Path(proplist_value(sink, "application.process.binary")).name,
    }
    return matches_any_pattern(values[kind], [pattern])


def is_legacy_config_ignored_audio_sink(sink: Any, config: dict[str, Any]) -> bool:
    detection = config.get("audio_detection", {})
    if not isinstance(detection, dict):
        return False
    app_name = proplist_value(sink, "application.name")
    media_name = proplist_value(sink, "media.name")
    proc_binary = Path(proplist_value(sink, "application.process.binary")).name
    return (
        matches_any_pattern(app_name, string_list_setting(detection.get("ignore_app_names")))
        or matches_any_pattern(media_name, string_list_setting(detection.get("ignore_media_names")))
        or matches_any_pattern(proc_binary, string_list_setting(detection.get("ignore_process_binaries")))
    )


def config_ignore_reason(sink: Any, config: dict[str, Any]) -> str | None:
    rule_name = matching_rule_name(sink, config)
    if rule_name is not None:
        return rule_name
    if is_legacy_config_ignored_audio_sink(sink, config):
        return "config"
    return None


def is_config_ignored_audio_sink(sink: Any, config: dict[str, Any]) -> bool:
    return config_ignore_reason(sink, config) is not None


def is_user_ignored_audio_sink(sink: Any, state: dict[str, Any]) -> bool:
    return any(
        audio_source_key_matches_sink(key, sink)
        for key in string_list_setting(state.get("ignored_audio_sources"))
    )


def is_internal_audio_sink(
    sink: Any,
    ignored_process_ids: set[str],
    ignored_sink_indexes: set[int],
) -> bool:
    app_name = proplist_value(sink, "application.name")
    media_name = proplist_value(sink, "media.name")
    process_id = proplist_value(sink, "application.process.id")
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
    return proc_binary in {"bgmusic.py"}
