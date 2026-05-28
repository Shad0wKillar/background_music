"""Project-wide constants: paths, app names, default config, hotkey labels.

Nothing here has side-effects — safe to import from anywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Root of the project (one level above this package directory).
PROJECT_DIR = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.yaml"

# mpv IPC socket — created by the daemon, read by control sub-commands.
SOCKET_PATH = Path("/tmp/mpv_bg_socket")

# Runtime state shared between the daemon and control sub-commands.
STATE_FILE = Path("/tmp/bgmusic_state.json")

# Persistent user preferences (volume, loop, last track, …).
# Written atomically by SettingsStore; gitignored.
SETTINGS_FILE = PROJECT_DIR / "bgmusic_settings.json"

# mpv uses this as its PulseAudio/PipeWire client name so we can
# reliably exclude our own stream from the "external audio" check.
MY_APP_NAME = "My_Background_Music"

# PortAudio keyboard-sound stream name — used the same way.
KEYBOARD_APP_NAME = "BGM_Keyboard_Sounds"

# How often the monitoring loop polls PulseAudio (seconds).
CHECK_INTERVAL = 0.5

DEFAULT_MUSIC_EXTENSIONS: list[str] = [
    ".mp3", ".flac", ".wav", ".ogg", ".opus",
    ".m4a", ".aac", ".webm", ".mp4", ".mkv",
]

# Built-in defaults; config.yaml values are merged on top at startup.
DEFAULT_CONFIG: dict[str, Any] = {
    "super": "Alt",
    "hotkeys": {
        "toggle_music":           "super+p",
        "next_track":             "super+]",
        "previous_track":         "super+[",
        "toggle_loop":            "super+l",
        "toggle_keyboard_sounds": "super+m",
        "volume_up":              "super+=",
        "volume_down":            "super+-",
        "toggle_mute":            "super+0",
        "keyboard_volume_up":     "super+shift+equal",
        "keyboard_volume_down":   "super+shift+minus",
    },
    "music": {
        "directory":            "music",
        "loop":                 True,
        "shuffle":              False,
        "volume_step":          5,
        "supported_extensions": DEFAULT_MUSIC_EXTENSIONS,
    },
    "audio_detection": {
        "ignore_app_names":       [],
        "ignore_media_names":     [],
        "ignore_process_binaries": [],
    },
    "keyboard_sounds": {
        "enabled":              True,
        "soundpack_directory":  "assets",
        "event":                "keydown",
        "volume":               1.0,
        "volume_step":          0.05,
        "max_polyphony":        32,
        "performance_preset":   "low_latency",
        "latency":              0.002,
        "blocksize":            32,
        "state_sync_interval":  0.1,
        "trim_leading_silence": True,
        "trim_threshold_ratio": 0.02,
        "trim_max_ms":          8,
        "trim_preroll_ms":      0.5,
        "pipewire_quantum":     256,
    },
}

# Human-readable labels for each hotkey action (used in error messages).
HOTKEY_ACTION_LABELS: dict[str, str] = {
    "toggle_music":           "toggle music",
    "next_track":             "next track",
    "previous_track":         "previous track",
    "toggle_loop":            "toggle loop",
    "toggle_keyboard_sounds": "toggle keyboard sounds",
    "volume_up":              "music volume up",
    "volume_down":            "music volume down",
    "toggle_mute":            "music mute",
    "keyboard_volume_up":     "keyboard volume up",
    "keyboard_volume_down":   "keyboard volume down",
}
