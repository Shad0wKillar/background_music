"""Keyboard feature wiring."""
from __future__ import annotations

from typing import Any

from bgmusic.actions import (
    keyboard_volume_down, keyboard_volume_up, next_track, previous_track,
    toggle_keyboard_sounds, toggle_loop, toggle_mute, toggle_music,
    toggle_repeat, volume_down, volume_up,
)
from bgmusic.config import bool_setting, float_setting, int_setting, resolve_project_path
from bgmusic.debug import DebugLogger
from bgmusic.hotkey_manager import HotkeyManager
from bgmusic.keyboard_monitor import KeyboardMonitor
from bgmusic.sound_player import KeyboardSoundPlayer
from bgmusic.state import SettingsStore, get_state


def keyboard_audio_settings(config: dict[str, Any]) -> tuple[str | float, int]:
    kb = config["keyboard_sounds"]
    preset = str(kb.get("performance_preset", "low_latency")).strip().lower()
    default_latency: str | float = 0.005 if preset == "low_latency" else "low"
    default_blocksize = 64 if preset == "low_latency" else 128
    raw_latency = kb.get("latency", default_latency)
    if isinstance(raw_latency, str):
        normalized = raw_latency.strip().lower()
        latency: str | float = normalized if normalized in {"low", "high"} else (
            float(normalized) if normalized.replace(".", "", 1).isdigit() else "low"
        )
    else:
        latency = float(raw_latency) if isinstance(raw_latency, (int, float)) else "low"
    return latency, int_setting(kb.get("blocksize"), default_blocksize)


def make_sound_player(
    config: dict[str, Any],
    logger: DebugLogger,
) -> KeyboardSoundPlayer | None:
    kb = config["keyboard_sounds"]
    try:
        latency, blocksize = keyboard_audio_settings(config)
        state = get_state(config)
        player = KeyboardSoundPlayer(
            config=config,
            soundpack_dir=resolve_project_path(kb.get("soundpack_directory")),
            enabled=state["keyboard_sounds_enabled"],
            event_mode=str(kb.get("event", "keydown")).strip().lower(),
            volume=state["keyboard_volume"],
            max_polyphony=int_setting(kb.get("max_polyphony"), 32),
            latency=latency,
            blocksize=blocksize,
            state_sync_interval=float_setting(kb.get("state_sync_interval"), 0.1),
            trim_leading_silence=bool_setting(kb.get("trim_leading_silence"), True),
            trim_threshold_ratio=float_setting(kb.get("trim_threshold_ratio"), 0.02),
            trim_max_ms=float_setting(kb.get("trim_max_ms"), 8.0),
            trim_preroll_ms=float_setting(kb.get("trim_preroll_ms"), 0.5),
            logger=logger,
        )
        print("Keyboard soundpack loaded.")
        return player
    except Exception as error:
        print(f"Warning: keyboard sounds disabled: {error}")
        return None


def make_callbacks(config: dict[str, Any], sound_player: Any, store: SettingsStore) -> dict[str, Any]:
    return {
        "toggle_music": lambda: toggle_music(config),
        "next_track": next_track,
        "previous_track": previous_track,
        "toggle_loop": lambda: toggle_loop(config, store),
        "toggle_repeat": lambda: toggle_repeat(config, store),
        "toggle_keyboard_sounds": lambda: toggle_keyboard_sounds(config, sound_player, store),
        "volume_up": lambda: volume_up(config, store),
        "volume_down": lambda: volume_down(config, store),
        "toggle_mute": toggle_mute,
        "keyboard_volume_up": lambda: keyboard_volume_up(config, sound_player, store),
        "keyboard_volume_down": lambda: keyboard_volume_down(config, sound_player, store),
    }


def start_keyboard_features(
    config: dict[str, Any],
    logger: DebugLogger,
    store: SettingsStore,
) -> tuple[KeyboardSoundPlayer | None, KeyboardMonitor | None]:
    sound_player = make_sound_player(config, logger)
    hotkey_manager = HotkeyManager(config, make_callbacks(config, sound_player, store))
    if not hotkey_manager.hotkeys and sound_player is None:
        return sound_player, None
    try:
        monitor = KeyboardMonitor(config, sound_player, hotkey_manager)
        monitor.start()
        return sound_player, monitor
    except Exception as error:
        print(f"Warning: keyboard monitor disabled: {error}")
        return sound_player, None
