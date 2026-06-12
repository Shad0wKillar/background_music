"""Audio source keys and TUI snapshots."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bgmusic.audio_backend import proplist_value, sink_corked
from bgmusic.audio_backend import pulsectl
from bgmusic.audio_ignores import (
    configured_audio_source_keys, is_config_ignored_audio_sink,
    is_internal_audio_sink, is_user_ignored_audio_sink,
)
from bgmusic.config import string_list_setting
from bgmusic.constants import KEYBOARD_APP_NAME, MY_APP_NAME


BUILTIN_SOURCE_KEYS = {
    "app:My_Background_Music",
    "app:BGM_Keyboard_Sounds",
    "binary:chromium*",
    "binary:zen*",
    "binary:tor-browser*",
    "media:Virtual Source output",
}


def make_audio_source_key(kind: str, value: str) -> str:
    return f"{kind}:{value.strip()}"


def split_audio_source_key(key: str) -> tuple[str, str] | None:
    kind, sep, value = key.partition(":")
    if not sep or kind not in {"app", "media", "binary", "rule"} or not value.strip():
        return None
    return kind, value.strip()


def preferred_audio_source_key(sink: Any) -> str:
    app_name = proplist_value(sink, "application.name")
    media_name = proplist_value(sink, "media.name")
    proc_binary = Path(proplist_value(sink, "application.process.binary")).name
    if app_name in {MY_APP_NAME, KEYBOARD_APP_NAME}:
        return make_audio_source_key("app", app_name)
    if proc_binary:
        return make_audio_source_key("binary", proc_binary)
    if app_name:
        return make_audio_source_key("app", app_name)
    if media_name:
        return make_audio_source_key("media", media_name)
    return make_audio_source_key("media", "unknown-source")


def audio_source_label(key: str) -> str:
    parsed = split_audio_source_key(key)
    if parsed is None:
        return key
    kind, value = parsed
    if key == make_audio_source_key("app", MY_APP_NAME):
        return "Background music"
    if key == make_audio_source_key("app", KEYBOARD_APP_NAME):
        return "Internal keyboard sounds"
    if kind == "binary" and value in {"chromium*", "chromium"}:
        return "Chromium"
    if kind == "binary" and value in {"zen*", "zen-browser", "zen-bin"}:
        return "Zen Browser"
    if kind == "binary" and value.startswith("tor-browser"):
        return "Tor Browser"
    if kind == "rule":
        return value
    return f"{value} [{kind}]"


def known_audio_source_keys(config: dict[str, Any], state: dict[str, Any]) -> set[str]:
    keys = configured_audio_source_keys(config)
    keys.update(string_list_setting(state.get("ignored_audio_sources")))
    keys.update(BUILTIN_SOURCE_KEYS)
    return keys


def new_source(key: str, ignored: bool, reason: str = "") -> dict[str, Any]:
    internal = key in {"app:My_Background_Music", "app:BGM_Keyboard_Sounds"}
    return {
        "key": key, "label": audio_source_label(key), "active": False,
        "corked": True, "sink_count": 0, "ignored": ignored,
        "ignored_reason": reason, "internal": internal,
        "protected": internal or reason == "config",
    }


def seed_sources(config: dict[str, Any], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    config_keys = configured_audio_source_keys(config)
    user_keys = set(string_list_setting(state.get("ignored_audio_sources")))
    sources = {}
    for key in known_audio_source_keys(config, state):
        reason = (
            "user" if key in user_keys else
            "config" if key in config_keys else
            "internal" if key in {"app:My_Background_Music", "app:BGM_Keyboard_Sounds"} else ""
        )
        sources[key] = new_source(key, bool(reason), reason)
    return sources


def apply_sink_to_source(source: dict[str, Any], sink: Any, ignored: bool, reason: str) -> None:
    source["sink_count"] += 1
    source["active"] = source["active"] or not sink_corked(sink)
    source["corked"] = source["corked"] and sink_corked(sink)
    if ignored:
        source["ignored"] = True
        source["ignored_reason"] = reason
    source["protected"] = source["protected"] or reason in {"internal", "config"}


def audio_sources_snapshot(
    config: dict[str, Any],
    state: dict[str, Any],
    ignored_process_ids: set[str] | None = None,
    ignored_sink_indexes: set[int] | None = None,
) -> list[dict[str, Any]]:
    ignored_process_ids = ignored_process_ids or set()
    ignored_sink_indexes = ignored_sink_indexes or set()
    sources = seed_sources(config, state)
    if pulsectl is None:
        return sorted_sources(sources)

    with pulsectl.Pulse("bgmusic-tui-sources") as pulse:
        sinks = pulse.sink_input_list()

    for sink in sinks:
        key = preferred_audio_source_key(sink)
        internal = is_internal_audio_sink(sink, ignored_process_ids, ignored_sink_indexes)
        config_ignored = is_config_ignored_audio_sink(sink, config)
        user_ignored = is_user_ignored_audio_sink(sink, state)
        reason = "internal" if internal else "config" if config_ignored else "user" if user_ignored else ""
        source = sources.setdefault(key, new_source(key, False))
        apply_sink_to_source(source, sink, bool(reason), reason)
    return sorted_sources(sources)


def sorted_sources(sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        sources.values(),
        key=lambda item: (
            not bool(item["active"]),
            not bool(item["ignored"]),
            str(item["label"]).casefold(),
        ),
    )
