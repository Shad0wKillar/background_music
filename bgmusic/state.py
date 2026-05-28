"""Runtime state and persistent settings.

Two separate storage layers:

STATE_FILE (/tmp/bgmusic_state.json)
    Short-lived IPC bridge between the daemon and control sub-commands.
    Holds manual_pause, loop, keyboard_sounds_enabled, keyboard_volume.
    Deleted on clean exit.

SettingsStore (bgmusic_settings.json in the project root)
    Persistent user preferences that survive restarts.
    Written atomically via a temp-file rename on every change so a
    Ctrl+C or kill -9 never leaves a corrupt file.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from bgmusic.config import bool_setting, clamp, float_setting
from bgmusic.constants import SETTINGS_FILE, STATE_FILE


# ---------------------------------------------------------------------------
# SettingsStore — in-memory + immediate-disk persistence
# ---------------------------------------------------------------------------

class SettingsStore:
    """Thread-safe settings object that writes to disk on every change."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Update a setting and flush to disk immediately (no-op if unchanged)."""
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self._flush_locked()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def _flush_locked(self) -> None:
        """Write via temp file so a crash mid-write never corrupts the saved state."""
        try:
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            tmp.replace(self._path)
        except Exception as error:
            print(f"Warning: could not save settings: {error}")

    @classmethod
    def load(cls, path: Path, config: dict[str, Any]) -> SettingsStore:
        """Load saved settings, falling back to config defaults for missing keys."""
        data: dict[str, Any] = {
            "keyboard_volume": clamp(
                float_setting(config["keyboard_sounds"].get("volume"), 1.0), 0.0, 1.0
            ),
            "keyboard_sounds_enabled": bool_setting(
                config["keyboard_sounds"].get("enabled"), True
            ),
            "loop": bool_setting(config["music"].get("loop"), True),
            "repeat": bool_setting(config["music"].get("repeat"), False),
            "music_volume": 100.0,
            "last_track": None,
        }
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    if "keyboard_volume" in saved:
                        data["keyboard_volume"] = clamp(
                            float_setting(saved["keyboard_volume"], data["keyboard_volume"]),
                            0.0, 1.0,
                        )
                    if "keyboard_sounds_enabled" in saved:
                        data["keyboard_sounds_enabled"] = bool_setting(
                            saved["keyboard_sounds_enabled"], True
                        )
                    if "loop" in saved:
                        data["loop"] = bool_setting(saved["loop"], True)
                    if "repeat" in saved:
                        data["repeat"] = bool_setting(saved["repeat"], False)
                    if "music_volume" in saved:
                        data["music_volume"] = clamp(
                            float_setting(saved["music_volume"], 100.0), 0.0, 100.0
                        )
                    if "last_track" in saved and isinstance(saved["last_track"], str):
                        data["last_track"] = saved["last_track"]
        except Exception as error:
            print(f"Warning: could not load settings: {error}")
        return cls(path, data)


# ---------------------------------------------------------------------------
# STATE_FILE helpers — used for daemon ↔ control sub-command IPC
# ---------------------------------------------------------------------------

def default_state(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "manual_pause": False,
        "loop": bool_setting(config["music"].get("loop"), True),
        "repeat": bool_setting(config["music"].get("repeat"), False),
        "keyboard_sounds_enabled": bool_setting(
            config["keyboard_sounds"].get("enabled"), True
        ),
        "keyboard_volume": clamp(
            float_setting(config["keyboard_sounds"].get("volume"), 1.0), 0.0, 1.0
        ),
    }


def get_state(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the runtime state file, falling back to defaults."""
    defaults = (
        default_state(config)
        if config is not None
        else {"manual_pause": False, "loop": True,
              "keyboard_sounds_enabled": True, "keyboard_volume": 1.0}
    )
    try:
        if STATE_FILE.exists():
            with STATE_FILE.open("r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict):
                for key in defaults:
                    if key in state:
                        defaults[key] = state[key]
    except Exception as error:
        print(f"Warning: could not read state file: {error}")

    # Validate / coerce every field so callers can trust the types.
    defaults["manual_pause"] = bool_setting(defaults.get("manual_pause"), False)
    defaults["loop"] = bool_setting(defaults.get("loop"), True)
    defaults["repeat"] = bool_setting(defaults.get("repeat"), False)
    defaults["keyboard_sounds_enabled"] = bool_setting(
        defaults.get("keyboard_sounds_enabled"), True
    )
    defaults["keyboard_volume"] = clamp(
        float_setting(defaults.get("keyboard_volume"), 1.0), 0.0, 1.0
    )
    return defaults


def set_state(state: dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def update_state(config: dict[str, Any], **updates: Any) -> dict[str, Any]:
    """Read → patch → write the state file atomically."""
    state = get_state(config)
    state.update(updates)
    set_state(state)
    return state
