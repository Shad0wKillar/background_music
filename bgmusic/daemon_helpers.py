"""Helper functions for daemon startup/shutdown."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from bgmusic.config import clamp, float_setting
from bgmusic.constants import SOCKET_PATH
from bgmusic.debug import DebugLogger
from bgmusic.ipc import get_mpv_property, send_ipc_command, set_mpv_loop, set_mpv_repeat
from bgmusic.state import SettingsStore, get_state


def wait_for_mpv_socket(logger: DebugLogger, timeout: float = 5.0) -> None:
    start = time.monotonic()
    deadline = start + timeout
    while not SOCKET_PATH.exists():
        if time.monotonic() >= deadline:
            logger.log("warning: mpv socket did not appear within 5 s")
            return
        time.sleep(0.025)
    logger.log(f"mpv socket ready ({(time.monotonic() - start) * 1000:.0f} ms)")


def restore_from_settings(
    store: SettingsStore,
    music_files: list[Path],
    loop_enabled: bool,
    repeat_enabled: bool,
    logger: DebugLogger,
) -> None:
    set_mpv_loop(loop_enabled)
    set_mpv_repeat(repeat_enabled)
    saved_vol = store.get("music_volume", 100.0)
    if saved_vol != 100.0:
        send_ipc_command({"command": ["set_property", "volume", saved_vol]})
        logger.log(f"restored music volume: {saved_vol:.0f}%")
    saved_track = store.get("last_track")
    if saved_track:
        for idx, path in enumerate(music_files):
            if str(path) == saved_track:
                if idx > 0:
                    send_ipc_command({"command": ["set_property", "playlist-pos", idx]})
                    logger.log(f"resumed from track {idx + 1}: {Path(saved_track).name}")
                break


def snapshot_final_state(store: SettingsStore, config: dict[str, Any]) -> None:
    cur_track = get_mpv_property("path")
    cur_vol = get_mpv_property("volume")
    cur_state = get_state(config)
    if cur_track:
        store.set("last_track", str(cur_track))
    if cur_vol is not None:
        store.set("music_volume", clamp(float_setting(cur_vol, 100.0), 0.0, 100.0))
    for key in (
        "keyboard_volume", "keyboard_sounds_enabled", "loop",
        "repeat", "ignored_audio_sources",
    ):
        store.set(key, cur_state.get(key, store.get(key)))


def initial_runtime_state(store: SettingsStore) -> dict[str, Any]:
    return {
        "manual_pause": False,
        "loop": store.get("loop"),
        "repeat": store.get("repeat", False),
        "keyboard_sounds_enabled": store.get("keyboard_sounds_enabled"),
        "keyboard_volume": store.get("keyboard_volume"),
        "ignored_audio_sources": store.get("ignored_audio_sources", []),
    }


def persist_live_settings(
    store: SettingsStore,
    state: dict[str, Any],
    known_track: str | None,
) -> str | None:
    cur_track = get_mpv_property("path")
    cur_track_str = str(cur_track) if cur_track else None
    if cur_track_str != known_track:
        known_track = cur_track_str
        store.set("last_track", cur_track_str)
    cur_vol = get_mpv_property("volume")
    if cur_vol is not None:
        store.set("music_volume", clamp(float_setting(cur_vol, 100.0), 0.0, 100.0))
    store.set("ignored_audio_sources", state.get("ignored_audio_sources", []))
    return known_track
