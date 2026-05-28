#!/usr/bin/env python3
import argparse
import copy
import fnmatch
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

try:
    import pulsectl
except ImportError:
    pulsectl = None

try:
    import yaml
except ImportError:
    yaml = None

try:
    import evdev
except ImportError:
    evdev = None


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.yaml"
SOCKET_PATH = Path("/tmp/mpv_bg_socket")
STATE_FILE = Path("/tmp/bgmusic_state.json")
SETTINGS_FILE = PROJECT_DIR / "bgmusic_settings.json"
MY_APP_NAME = "My_Background_Music"
KEYBOARD_APP_NAME = "BGM_Keyboard_Sounds"
CHECK_INTERVAL = 0.5
DEFAULT_MUSIC_EXTENSIONS = [
    ".mp3",
    ".flac",
    ".wav",
    ".ogg",
    ".opus",
    ".m4a",
    ".aac",
    ".webm",
    ".mp4",
    ".mkv",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "super": "Alt",
    "hotkeys": {
        "toggle_music": "super+p",
        "next_track": "super+]",
        "previous_track": "super+[",
        "toggle_loop": "super+l",
        "toggle_keyboard_sounds": "super+m",
        "volume_up": "super+=",
        "volume_down": "super+-",
        "toggle_mute": "super+0",
        "keyboard_volume_up": "super+shift+equal",
        "keyboard_volume_down": "super+shift+minus",
    },
    "music": {
        "directory": "music",
        "loop": True,
        "shuffle": False,
        "volume_step": 5,
        "supported_extensions": DEFAULT_MUSIC_EXTENSIONS,
    },
    "audio_detection": {
        "ignore_app_names": [],
        "ignore_media_names": [],
        "ignore_process_binaries": [],
    },
    "keyboard_sounds": {
        "enabled": True,
        "soundpack_directory": "assets",
        "event": "keydown",
        "volume": 1.0,
        "volume_step": 0.1,
        "max_polyphony": 32,
        "performance_preset": "low_latency",
        "latency": 0.002,
        "blocksize": 32,
        "state_sync_interval": 0.1,
        "trim_leading_silence": True,
        "trim_threshold_ratio": 0.02,
        "trim_max_ms": 8,
        "trim_preroll_ms": 0.5,
        "pipewire_quantum": 256,
    },
}

HOTKEY_ACTION_LABELS = {
    "toggle_music": "toggle music",
    "next_track": "next track",
    "previous_track": "previous track",
    "toggle_loop": "toggle loop",
    "toggle_keyboard_sounds": "toggle keyboard sounds",
    "volume_up": "music volume up",
    "volume_down": "music volume down",
    "toggle_mute": "music mute",
    "keyboard_volume_up": "keyboard volume up",
    "keyboard_volume_down": "keyboard volume down",
}


class DebugLogger:
    def __init__(self, enabled: bool, deep_enabled: bool = False) -> None:
        self.deep_enabled = deep_enabled
        self.enabled = enabled or deep_enabled

    def log(self, message: str) -> None:
        if self.enabled:
            timestamp = time.strftime("%H:%M:%S")
            print(f"[debug {timestamp}] {message}", flush=True)

    def deep(self, message: str) -> None:
        if self.deep_enabled:
            timestamp = time.strftime("%H:%M:%S")
            print(f"[deep {timestamp}] {message}", flush=True)


def load_config(config_path: Path) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if not config_path.exists():
        print(f"Config not found at {config_path}; using built-in defaults.")
        return config

    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read config.yaml. "
            "Install dependencies with: uv pip install -r requirements.txt"
        )

    with config_path.open("r", encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}

    if not isinstance(loaded, dict):
        raise RuntimeError(f"Config file must contain a YAML mapping: {config_path}")

    merge_dict(config, loaded)
    return config


def merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_dict(base[key], value)
        else:
            base[key] = value


def resolve_project_path(value: Any) -> Path:
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def bool_setting(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return default


def float_setting(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def int_setting(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def string_list_setting(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def default_state(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "manual_pause": False,
        "loop": bool_setting(config["music"].get("loop"), True),
        "keyboard_sounds_enabled": bool_setting(
            config["keyboard_sounds"].get("enabled"), True
        ),
        "keyboard_volume": clamp(
            float_setting(config["keyboard_sounds"].get("volume"), 1.0), 0.0, 1.0
        ),
    }


def get_state(config: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = (
        default_state(config)
        if config is not None
        else {
            "manual_pause": False,
            "loop": True,
            "keyboard_sounds_enabled": True,
            "keyboard_volume": 1.0,
        }
    )
    try:
        if STATE_FILE.exists():
            with STATE_FILE.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if isinstance(state, dict):
                for key in defaults:
                    if key in state:
                        defaults[key] = state[key]
    except Exception as error:
        print(f"Warning: could not read state file: {error}")
    defaults["manual_pause"] = bool_setting(defaults.get("manual_pause"), False)
    defaults["loop"] = bool_setting(defaults.get("loop"), True)
    defaults["keyboard_sounds_enabled"] = bool_setting(
        defaults.get("keyboard_sounds_enabled"), True
    )
    defaults["keyboard_volume"] = clamp(
        float_setting(defaults.get("keyboard_volume"), 1.0), 0.0, 1.0
    )
    return defaults


def set_state(state: dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)


def update_state(config: dict[str, Any], **updates: Any) -> dict[str, Any]:
    state = get_state(config)
    state.update(updates)
    set_state(state)
    return state


class SettingsStore:
    """In-memory settings with immediate atomic persistence to disk.

    Every call to set() writes to SETTINGS_FILE right away so no state is
    lost if the process is interrupted.  The file is written via a temp-file
    rename so a partial write can never corrupt the saved state.
    """

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self._flush_locked()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def _flush_locked(self) -> None:
        try:
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            tmp.replace(self._path)
        except Exception as error:
            print(f"Warning: could not save settings: {error}")

    @classmethod
    def load(cls, path: Path, config: dict[str, Any]) -> "SettingsStore":
        data: dict[str, Any] = {
            "keyboard_volume": clamp(
                float_setting(config["keyboard_sounds"].get("volume"), 1.0), 0.0, 1.0
            ),
            "keyboard_sounds_enabled": bool_setting(
                config["keyboard_sounds"].get("enabled"), True
            ),
            "loop": bool_setting(config["music"].get("loop"), True),
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
                    if "music_volume" in saved:
                        data["music_volume"] = clamp(
                            float_setting(saved["music_volume"], 100.0), 0.0, 100.0
                        )
                    if "last_track" in saved and isinstance(saved["last_track"], str):
                        data["last_track"] = saved["last_track"]
        except Exception as error:
            print(f"Warning: could not load settings: {error}")
        return cls(path, data)


def send_ipc_command(command_dict: dict[str, Any]) -> Any:
    try:
        if not SOCKET_PATH.exists():
            return None

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(SOCKET_PATH))
            client.sendall(json.dumps(command_dict).encode("utf-8") + b"\n")
            response = client.recv(4096)

        if not response:
            return None
        return json.loads(response.decode("utf-8"))
    except Exception:
        return None


def get_mpv_property(name: str) -> Any:
    response = send_ipc_command({"command": ["get_property", name]})
    if isinstance(response, dict) and response.get("error") == "success":
        return response.get("data")
    return None


def set_mpv_pause(paused: bool) -> None:
    send_ipc_command({"command": ["set_property", "pause", paused]})


def set_mpv_loop(enabled: bool) -> None:
    value = "inf" if enabled else "no"
    send_ipc_command({"command": ["set_property", "loop-playlist", value]})


def toggle_music(config: dict[str, Any]) -> None:
    current = get_state(config)
    manual_pause = not current["manual_pause"]
    update_state(config, manual_pause=manual_pause)
    set_mpv_pause(manual_pause)
    print(f"Manual pause: {manual_pause}")


def next_track() -> None:
    send_ipc_command({"command": ["playlist-next"]})
    print("Skipped to next track")


def previous_track() -> None:
    send_ipc_command({"command": ["playlist-prev"]})
    print("Skipped to previous track")


def toggle_loop(config: dict[str, Any], store: "SettingsStore | None" = None) -> None:
    current = get_state(config)
    enabled = not current["loop"]
    update_state(config, loop=enabled)
    set_mpv_loop(enabled)
    print(f"Playlist loop: {enabled}")
    if store is not None:
        store.set("loop", enabled)


def toggle_keyboard_sounds(
    config: dict[str, Any],
    sound_player: "KeyboardSoundPlayer | None" = None,
    store: "SettingsStore | None" = None,
) -> None:
    current = get_state(config)
    enabled = not current["keyboard_sounds_enabled"]
    update_state(config, keyboard_sounds_enabled=enabled)
    if sound_player is not None:
        sound_player.set_enabled(enabled)
    print(f"Keyboard sounds: {enabled}")
    if store is not None:
        store.set("keyboard_sounds_enabled", enabled)


def volume_step(config: dict[str, Any]) -> float:
    return float_setting(config["music"].get("volume_step"), 5.0)


def keyboard_volume_step(config: dict[str, Any]) -> float:
    return float_setting(config["keyboard_sounds"].get("volume_step"), 0.1)


def adjust_volume(delta: float, store: "SettingsStore | None" = None) -> None:
    fallback = store.get("music_volume", 100.0) if store is not None else 100.0
    current = get_mpv_property("volume")
    current_vol = clamp(float_setting(current, fallback), 0.0, 100.0)
    new_vol = clamp(current_vol + delta, 0.0, 100.0)
    send_ipc_command({"command": ["set_property", "volume", new_vol]})
    print(f"Music volume: {new_vol:.0f}%")
    if store is not None:
        store.set("music_volume", new_vol)


def volume_up(config: dict[str, Any], store: "SettingsStore | None" = None) -> None:
    adjust_volume(volume_step(config), store)


def volume_down(config: dict[str, Any], store: "SettingsStore | None" = None) -> None:
    adjust_volume(-volume_step(config), store)


def toggle_mute() -> None:
    send_ipc_command({"command": ["cycle", "mute"]})
    print("Mute toggled")


def adjust_keyboard_volume(
    config: dict[str, Any],
    delta: float,
    sound_player: "KeyboardSoundPlayer | None" = None,
    store: "SettingsStore | None" = None,
) -> None:
    current = get_state(config)
    volume = clamp(float_setting(current.get("keyboard_volume"), 1.0) + delta, 0.0, 1.0)
    update_state(config, keyboard_volume=volume)
    if sound_player is not None:
        sound_player.set_volume(volume)
    print(f"Keyboard volume: {volume * 100:.0f}%")
    if store is not None:
        store.set("keyboard_volume", volume)


def keyboard_volume_up(
    config: dict[str, Any],
    sound_player: "KeyboardSoundPlayer | None" = None,
    store: "SettingsStore | None" = None,
) -> None:
    adjust_keyboard_volume(config, keyboard_volume_step(config), sound_player, store)


def keyboard_volume_down(
    config: dict[str, Any],
    sound_player: "KeyboardSoundPlayer | None" = None,
    store: "SettingsStore | None" = None,
) -> None:
    adjust_keyboard_volume(config, -keyboard_volume_step(config), sound_player, store)


def list_audio_devices() -> None:
    try:
        import sounddevice as sd
    except ImportError as error:
        raise RuntimeError(
            "sounddevice is required to list audio devices. "
            "Install dependencies with: uv pip install -r requirements.txt"
        ) from error

    print("Host APIs:")
    for index, hostapi in enumerate(sd.query_hostapis()):
        print(f"  [{index}] {hostapi['name']}")

    print("\nOutput devices:")
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_output_channels", 0)) <= 0:
            continue
        default_marker = "*" if index == sd.default.device[1] else " "
        print(
            f"{default_marker} [{index}] {device['name']} "
            f"hostapi={device['hostapi']} "
            f"outputs={device['max_output_channels']} "
            f"default_low={device['default_low_output_latency']:.4f}s "
            f"default_high={device['default_high_output_latency']:.4f}s"
        )


def event_timestamp_seconds(event: Any) -> float | None:
    try:
        return float(event.timestamp())
    except Exception:
        try:
            return float(event.sec) + (float(event.usec) / 1_000_000.0)
        except Exception:
            return None


def kernel_to_user_ms(
    event_timestamp: float | None, receipt_wall: float, receipt_mono: float
) -> float | None:
    if event_timestamp is None:
        return None
    for reference in (receipt_wall, receipt_mono):
        delta = (reference - event_timestamp) * 1000.0
        if -1000.0 <= delta <= 1000.0:
            return delta
    return None


def format_ms(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2f}ms"


MODIFIER_ALIASES = {
    "alt": "alt",
    "option": "alt",
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "meta": "meta",
    "super": "meta",
    "win": "meta",
    "windows": "meta",
    "cmd": "meta",
    "command": "meta",
}
MODIFIER_TOKENS = {"alt", "ctrl", "shift", "meta"}

KEY_TOKEN_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "bracketleft": "[",
    "leftbracket": "[",
    "openbracket": "[",
    "bracketright": "]",
    "rightbracket": "]",
    "closebracket": "]",
    "spacebar": "space",
    "minus": "-",
    "equal": "=",
    "equals": "=",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "pageup": "pageup",
    "pagedown": "pagedown",
}


def normalize_modifier(value: Any) -> str:
    token = str(value).strip().lower()
    if token not in MODIFIER_ALIASES:
        raise RuntimeError(
            f"Unsupported super modifier '{value}'. "
            "Use Alt, Control, Shift, Meta, or Super."
        )
    return MODIFIER_ALIASES[token]


def normalize_hotkey_token(token: str, super_modifier: str) -> str:
    normalized = token.strip().lower()
    if normalized == "super":
        return super_modifier
    if normalized in MODIFIER_ALIASES:
        return MODIFIER_ALIASES[normalized]
    if normalized in KEY_TOKEN_ALIASES:
        return KEY_TOKEN_ALIASES[normalized]
    if normalized.startswith("key") and len(normalized) == 4:
        return normalized[-1]
    if normalized.startswith("digit") and len(normalized) == 6:
        return normalized[-1]
    return normalized


def parse_hotkeys(config: dict[str, Any]) -> list[tuple[frozenset[str], str, str]]:
    hotkey_config = config.get("hotkeys", {})
    if not isinstance(hotkey_config, dict):
        print("Warning: hotkeys config must be a mapping; hotkeys disabled.")
        return []

    super_modifier = normalize_modifier(config.get("super", "Alt"))
    parsed = []
    for action, combo in hotkey_config.items():
        if action not in HOTKEY_ACTION_LABELS:
            print(f"Warning: unknown hotkey action '{action}' ignored.")
            continue
        if not isinstance(combo, str) or not combo.strip():
            print(f"Warning: hotkey for '{action}' must be a non-empty string.")
            continue

        tokens = [
            normalize_hotkey_token(part, super_modifier)
            for part in combo.split("+")
            if part.strip()
        ]
        if not tokens:
            print(f"Warning: hotkey for '{action}' has no usable keys.")
            continue
        parsed.append((frozenset(tokens), action, combo))

    return parsed


EVDEV_HOTKEY_TOKENS = {
    "KEY_LEFTALT": "alt",
    "KEY_RIGHTALT": "alt",
    "KEY_LEFTCTRL": "ctrl",
    "KEY_RIGHTCTRL": "ctrl",
    "KEY_LEFTSHIFT": "shift",
    "KEY_RIGHTSHIFT": "shift",
    "KEY_LEFTMETA": "meta",
    "KEY_RIGHTMETA": "meta",
    "KEY_SPACE": "space",
    "KEY_ENTER": "enter",
    "KEY_KPENTER": "enter",
    "KEY_ESC": "escape",
    "KEY_BACKSPACE": "backspace",
    "KEY_TAB": "tab",
    "KEY_CAPSLOCK": "capslock",
    "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]",
    "KEY_MINUS": "-",
    "KEY_EQUAL": "=",
    "KEY_GRAVE": "`",
    "KEY_BACKSLASH": "\\",
    "KEY_SEMICOLON": ";",
    "KEY_APOSTROPHE": "'",
    "KEY_COMMA": ",",
    "KEY_DOT": ".",
    "KEY_SLASH": "/",
    "KEY_UP": "up",
    "KEY_DOWN": "down",
    "KEY_LEFT": "left",
    "KEY_RIGHT": "right",
    "KEY_INSERT": "insert",
    "KEY_HOME": "home",
    "KEY_PAGEUP": "pageup",
    "KEY_DELETE": "delete",
    "KEY_END": "end",
    "KEY_PAGEDOWN": "pagedown",
}

EVDEV_BROWSER_CODES = {
    "KEY_ESC": "Escape",
    "KEY_SPACE": "Space",
    "KEY_ENTER": "Enter",
    "KEY_BACKSPACE": "Backspace",
    "KEY_TAB": "Tab",
    "KEY_CAPSLOCK": "CapsLock",
    "KEY_LEFTBRACE": "BracketLeft",
    "KEY_RIGHTBRACE": "BracketRight",
    "KEY_MINUS": "Minus",
    "KEY_EQUAL": "Equal",
    "KEY_GRAVE": "Backquote",
    "KEY_BACKSLASH": "Backslash",
    "KEY_SEMICOLON": "Semicolon",
    "KEY_APOSTROPHE": "Quote",
    "KEY_COMMA": "Comma",
    "KEY_DOT": "Period",
    "KEY_SLASH": "Slash",
    "KEY_LEFTSHIFT": "ShiftLeft",
    "KEY_RIGHTSHIFT": "ShiftRight",
    "KEY_LEFTCTRL": "ControlLeft",
    "KEY_RIGHTCTRL": "ControlLeft",
    "KEY_LEFTALT": "AltLeft",
    "KEY_RIGHTALT": "AltLeft",
    "KEY_UP": "ArrowUp",
    "KEY_DOWN": "ArrowDown",
    "KEY_LEFT": "ArrowLeft",
    "KEY_RIGHT": "ArrowRight",
    "KEY_INSERT": "Insert",
    "KEY_HOME": "Home",
    "KEY_PAGEUP": "PageUp",
    "KEY_DELETE": "Delete",
    "KEY_END": "End",
    "KEY_PAGEDOWN": "PageDown",
    "KEY_NUMLOCK": "NumLock",
    "KEY_KPSLASH": "NumpadDivide",
    "KEY_KPASTERISK": "NumpadMultiply",
    "KEY_KPMINUS": "NumpadSubtract",
    "KEY_KPPLUS": "NumpadAdd",
    "KEY_KPENTER": "NumpadEnter",
    "KEY_KPDOT": "NumpadDecimal",
    "KEY_SYSRQ": "PrintScreen",
    "KEY_SCROLLLOCK": "ScrollLock",
    "KEY_PAUSE": "Pause",
}


def evdev_code_name(event_code: int) -> str | None:
    if evdev is None:
        return None
    name = evdev.ecodes.KEY.get(event_code)
    if isinstance(name, (list, tuple)):
        return name[0]
    return name


def evdev_to_hotkey_token(name: str) -> str | None:
    if name in EVDEV_HOTKEY_TOKENS:
        return EVDEV_HOTKEY_TOKENS[name]

    if name.startswith("KEY_"):
        suffix = name[4:]
        if len(suffix) == 1 and suffix.isalpha():
            return suffix.lower()
        if len(suffix) == 1 and suffix.isdigit():
            return suffix
        if suffix.startswith("F") and suffix[1:].isdigit():
            return suffix.lower()
        if suffix.startswith("KP") and suffix[2:].isdigit():
            return f"numpad{suffix[2:]}"

    return None


def evdev_to_browser_code(name: str) -> str | None:
    if name in EVDEV_BROWSER_CODES:
        return EVDEV_BROWSER_CODES[name]

    if name.startswith("KEY_"):
        suffix = name[4:]
        if len(suffix) == 1 and suffix.isalpha():
            return f"Key{suffix}"
        if len(suffix) == 1 and suffix.isdigit():
            return f"Digit{suffix}"
        if suffix.startswith("F") and suffix[1:].isdigit():
            return suffix
        if suffix.startswith("KP") and suffix[2:].isdigit():
            return f"Numpad{suffix[2:]}"

    return None


class KeyboardSoundPlayer:
    def __init__(
        self,
        config: dict[str, Any],
        soundpack_dir: Path,
        enabled: bool,
        event_mode: str,
        volume: float,
        max_polyphony: int,
        latency: str | float,
        blocksize: int,
        state_sync_interval: float,
        trim_leading_silence: bool,
        trim_threshold_ratio: float,
        trim_max_ms: float,
        trim_preroll_ms: float,
        logger: DebugLogger,
    ) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
        except ImportError as error:
            raise RuntimeError(
                "Keyboard sounds require numpy, sounddevice, and soundfile. "
                "Install dependencies with: uv pip install -r requirements.txt"
            ) from error

        self.np = np
        self.sd = sd
        self.config = config
        self.enabled = enabled
        self.event_mode = (
            event_mode if event_mode in {"keydown", "keyup", "both"} else "keydown"
        )
        self.volume = clamp(volume, 0.0, 1.0)
        self.max_polyphony = max(1, max_polyphony)
        self.state_sync_interval = max(0.0, state_sync_interval)
        self.logger = logger
        self.stop_event = threading.Event()
        self.state_thread: threading.Thread | None = None
        self.latency_thread: threading.Thread | None = None
        self.latency_events: deque[str] = deque()
        self.latency_lock = threading.Lock()
        self.deep_key_count = 0
        self.deep_key_limit = 100
        self.lock = threading.Lock()
        self.active: list[list[Any]] = []
        self.clips: dict[tuple[str, int], Any] = {}
        self.evdev_clips: dict[tuple[str, int], Any] = {}
        self._state_mtime_ns: int | None = None

        config_path = soundpack_dir / "config.json"
        with config_path.open("r", encoding="utf-8") as config_file:
            soundpack_config = json.load(config_file)

        audio_path = soundpack_dir / soundpack_config.get("audio_file", "sound.ogg")
        audio, self.sample_rate = sf.read(
            str(audio_path), dtype="float32", always_2d=True
        )
        self.channels = audio.shape[1]

        definitions = soundpack_config.get("definitions", {})
        for browser_code, definition in definitions.items():
            timings = definition.get("timing", [])
            for index, timing in enumerate(timings[:2]):
                if not isinstance(timing, list) or len(timing) != 2:
                    continue
                start_ms, end_ms = timing
                start_sample = int((float(start_ms) / 1000.0) * self.sample_rate)
                end_sample = int((float(end_ms) / 1000.0) * self.sample_rate)
                if 0 <= start_sample < end_sample <= len(audio):
                    clip = audio[start_sample:end_sample].copy()
                    if trim_leading_silence:
                        clip, trimmed_ms = self._trim_clip(
                            clip,
                            trim_threshold_ratio,
                            trim_max_ms,
                            trim_preroll_ms,
                        )
                        if logger.enabled and trimmed_ms > 0:
                            logger.log(
                                f"trimmed {browser_code}[{index}] by {trimmed_ms:.2f}ms"
                            )
                    self.clips[(browser_code, index)] = clip

        self._precompute_evdev_clips()
        logger.log(
            f"keyboard clips loaded: {len(self.clips)} slices, "
            f"{len(self.evdev_clips)} evdev mappings"
        )

        # Derive a latency in seconds so we can tell PipeWire/PulseAudio what
        # buffer size to use.  Without these hints the downstream stack (PipeWire
        # quantum, PulseAudio playback buffer) defaults to something in the
        # 100–200 ms range regardless of what PortAudio requests.
        #
        # PIPEWIRE_LATENCY  – read by the PipeWire ALSA plugin (pipewire-alsa)
        #   when PortAudio opens "default via ALSA".  Format is "frames/rate".
        #   Sets the PipeWire graph quantum for this node, which is the true
        #   upstream determinant of callback→DAC latency on Arch/Wayland.
        #
        # PULSE_LATENCY_MSEC – read by the PulseAudio client library (or
        #   pipewire-pulse) when PortAudio uses the PulseAudio host API.
        #   Requests a specific playback buffer size in milliseconds.
        _latency_secs = latency if isinstance(latency, float) else 0.020
        _pw_frames = max(32, round(_latency_secs * 48000))
        pulse_props = {
            "PULSE_PROP_application.name": KEYBOARD_APP_NAME,
            "PULSE_PROP_media.name": KEYBOARD_APP_NAME,
            "PULSE_PROP_media.role": "event",
            "PIPEWIRE_LATENCY": f"{_pw_frames}/48000",
            "PULSE_LATENCY_MSEC": str(max(1, round(_latency_secs * 1000))),
        }
        previous_props = {key: os.environ.get(key) for key in pulse_props}
        os.environ.update(pulse_props)
        try:
            self.stream = self._open_stream(latency, blocksize)
        finally:
            for key, value in previous_props.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.state_thread = threading.Thread(
            target=self._state_watcher,
            name="keyboard-state-watcher",
            daemon=True,
        )
        self.state_thread.start()
        if self.logger.deep_enabled:
            self.latency_thread = threading.Thread(
                target=self._latency_reporter,
                name="keyboard-latency-reporter",
                daemon=True,
            )
            self.latency_thread.start()

    def _open_stream(self, latency: str | float, blocksize: int) -> Any:
        attempts = [
            (latency, max(0, blocksize), "configured"),
            ("low", 128, "fallback"),
        ]
        last_error: Exception | None = None
        for attempt_latency, attempt_blocksize, label in attempts:
            stream = None
            try:
                self.logger.log(
                    "opening keyboard audio stream: "
                    f"latency={attempt_latency}, blocksize={attempt_blocksize}, "
                    f"sample_rate={self.sample_rate}, channels={self.channels}, "
                    f"PIPEWIRE_LATENCY={os.environ.get('PIPEWIRE_LATENCY', 'unset')}, "
                    f"PULSE_LATENCY_MSEC={os.environ.get('PULSE_LATENCY_MSEC', 'unset')}ms"
                )
                stream = self.sd.OutputStream(
                    samplerate=self.sample_rate,
                    blocksize=attempt_blocksize,
                    channels=self.channels,
                    dtype="float32",
                    latency=attempt_latency,
                    callback=self._callback,
                    prime_output_buffers_using_stream_callback=True,
                )
                stream.start()
                actual_latency = getattr(stream, "latency", "unknown")
                self.logger.log(
                    "keyboard audio stream opened "
                    f"({label}); actual latency={actual_latency}; "
                    f"device={self._describe_output_device(stream)}"
                )
                return stream
            except Exception as error:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                last_error = error
                self.logger.log(
                    "keyboard audio stream open failed "
                    f"({label}, latency={attempt_latency}, blocksize={attempt_blocksize}): "
                    f"{error}"
                )
        raise RuntimeError(f"Could not open keyboard audio stream: {last_error}")

    def _describe_output_device(self, stream: Any) -> str:
        try:
            device = getattr(stream, "device", None)
            if isinstance(device, tuple):
                device = device[1]
            if device is None or device == -1:
                device = self.sd.default.device[1]
            info = self.sd.query_devices(device)
            hostapi = self.sd.query_hostapis(info["hostapi"])["name"]
            return f"[{device}] {info['name']} via {hostapi}"
        except Exception:
            return "unknown"

    def _trim_clip(
        self,
        clip: Any,
        threshold_ratio: float,
        max_ms: float,
        preroll_ms: float,
    ) -> tuple[Any, float]:
        if len(clip) == 0:
            return clip, 0.0
        mono = self.np.max(self.np.abs(clip), axis=1)
        peak = float(self.np.max(mono))
        if peak <= 0:
            return clip, 0.0
        threshold = max(peak * max(0.0, threshold_ratio), 0.001)
        hits = self.np.flatnonzero(mono >= threshold)
        if len(hits) == 0:
            return clip, 0.0
        preroll_samples = int(max(0.0, preroll_ms) / 1000.0 * self.sample_rate)
        max_trim_samples = int(max(0.0, max_ms) / 1000.0 * self.sample_rate)
        trim_samples = min(max(0, int(hits[0]) - preroll_samples), max_trim_samples)
        if trim_samples <= 0:
            return clip, 0.0
        return clip[trim_samples:].copy(), trim_samples / self.sample_rate * 1000.0

    def _precompute_evdev_clips(self) -> None:
        if evdev is None:
            return
        for code_value in evdev.ecodes.KEY:
            evdev_name = evdev_code_name(code_value)
            if evdev_name is None:
                continue
            browser_code = evdev_to_browser_code(evdev_name)
            if browser_code is None:
                continue
            for event_value, timing_index in ((1, 0), (0, 1)):
                clip = self.clips.get((browser_code, timing_index))
                if clip is not None:
                    self.evdev_clips[(evdev_name, event_value)] = clip

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            pass

        outdata.fill(0)
        with self.lock:
            volume = self.volume
            remaining = []
            for clip, position, probe in self.active:
                chunk = clip[position : position + frames]
                if len(chunk):
                    outdata[: len(chunk)] += chunk
                    if probe is not None and position == 0:
                        self._record_latency_probe(probe, time_info, frames)
                next_position = position + len(chunk)
                if next_position < len(clip):
                    remaining.append([clip, next_position, None])
            self.active = remaining

        outdata *= volume
        self.np.clip(outdata, -1.0, 1.0, out=outdata)

    def _record_latency_probe(self, probe: dict[str, Any], time_info: Any, frames: int) -> None:
        if not self.logger.deep_enabled:
            return
        callback_ns = time.perf_counter_ns()
        current_time = getattr(time_info, "currentTime", None)
        dac_time = getattr(time_info, "outputBufferDacTime", None)
        callback_to_dac_ms = None
        if isinstance(current_time, (int, float)) and isinstance(dac_time, (int, float)):
            callback_to_dac_ms = max(0.0, (dac_time - current_time) * 1000.0)
        enqueue_to_callback_ms = (callback_ns - probe["enqueue_ns"]) / 1_000_000.0
        receipt_to_callback_ms = (callback_ns - probe["receipt_ns"]) / 1_000_000.0
        receipt_to_estimated_dac_ms = (
            None
            if callback_to_dac_ms is None
            else receipt_to_callback_ms + callback_to_dac_ms
        )
        message = (
            f"key={probe['key']} event={probe['event_value']} "
            f"kernel->receipt={format_ms(probe['kernel_to_receipt_ms'])} "
            f"receipt->enqueue={format_ms(probe['receipt_to_enqueue_ms'])} "
            f"enqueue->callback={enqueue_to_callback_ms:.2f}ms "
            f"receipt->callback={receipt_to_callback_ms:.2f}ms "
            f"callback->dac={format_ms(callback_to_dac_ms)} "
            f"receipt->estimated_dac={format_ms(receipt_to_estimated_dac_ms)} "
            f"frames={frames}"
        )
        with self.latency_lock:
            self.latency_events.append(message)

    def set_enabled(self, enabled: bool) -> None:
        with self.lock:
            self.enabled = enabled

    def set_volume(self, volume: float) -> None:
        with self.lock:
            self.volume = clamp(volume, 0.0, 1.0)

    def _state_watcher(self) -> None:
        interval = max(0.05, self.state_sync_interval)
        while not self.stop_event.wait(interval):
            self.sync_from_state()

    def _latency_reporter(self) -> None:
        while not self.stop_event.wait(0.05):
            pending = []
            with self.latency_lock:
                while self.latency_events:
                    pending.append(self.latency_events.popleft())
            for message in pending:
                self.logger.deep(message)

    def sync_from_state(self) -> None:
        try:
            mtime_ns = STATE_FILE.stat().st_mtime_ns
        except OSError:
            return
        if mtime_ns == self._state_mtime_ns:
            return
        self._state_mtime_ns = mtime_ns
        state = get_state(self.config)
        self.set_enabled(state["keyboard_sounds_enabled"])
        self.set_volume(state["keyboard_volume"])

    def play(
        self,
        evdev_name: str,
        event_value: int,
        receipt_ns: int | None = None,
        kernel_to_receipt_ms: float | None = None,
    ) -> None:
        if event_value == 2:
            return
        if self.event_mode == "keydown" and event_value != 1:
            return
        if self.event_mode == "keyup" and event_value != 0:
            return
        if self.event_mode == "both" and event_value not in {0, 1}:
            return

        clip = self.evdev_clips.get((evdev_name, event_value))
        if clip is None:
            return

        probe = None
        if self.logger.deep_enabled and self.deep_key_count < self.deep_key_limit:
            self.deep_key_count += 1
            enqueue_ns = time.perf_counter_ns()
            receipt_ns = receipt_ns or enqueue_ns
            probe = {
                "key": evdev_name,
                "event_value": event_value,
                "receipt_ns": receipt_ns,
                "enqueue_ns": enqueue_ns,
                "kernel_to_receipt_ms": kernel_to_receipt_ms,
                "receipt_to_enqueue_ms": (enqueue_ns - receipt_ns) / 1_000_000.0,
            }

        with self.lock:
            if self.enabled:
                if len(self.active) >= self.max_polyphony:
                    self.active = (
                        [] if self.max_polyphony == 1 else self.active[-(self.max_polyphony - 1) :]
                    )
                self.active.append([clip, 0, probe])

    def close(self) -> None:
        self.stop_event.set()
        if self.state_thread is not None:
            self.state_thread.join(timeout=1.0)
        if self.latency_thread is not None:
            self.latency_thread.join(timeout=1.0)
        if self.logger.deep_enabled:
            with self.latency_lock:
                pending = list(self.latency_events)
                self.latency_events.clear()
            for message in pending:
                self.logger.deep(message)
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class HotkeyManager:
    def __init__(self, config: dict[str, Any], callbacks: dict[str, Any]) -> None:
        self.hotkeys = parse_hotkeys(config)
        self.callbacks = callbacks
        self.active_tokens: set[str] = set()
        self.triggered_combos: set[frozenset[str]] = set()

    def handle_key(self, evdev_name: str, event_value: int) -> None:
        token = evdev_to_hotkey_token(evdev_name)
        if token is None:
            return

        if event_value == 0:
            self.active_tokens.discard(token)
            self.triggered_combos = {
                combo
                for combo in self.triggered_combos
                if combo.issubset(self.active_tokens)
            }
            return

        if event_value == 2:
            return

        self.active_tokens.add(token)
        active_modifiers = self.active_tokens & MODIFIER_TOKENS
        for combo, action, label in self.hotkeys:
            combo_modifiers = combo & MODIFIER_TOKENS
            if (
                token in combo
                and combo.issubset(self.active_tokens)
                and combo_modifiers == active_modifiers
                and combo not in self.triggered_combos
            ):
                self.triggered_combos.add(combo)
                callback = self.callbacks.get(action)
                if callback is None:
                    continue
                try:
                    callback()
                except Exception as error:
                    print(f"Hotkey '{label}' failed: {error}")


class KeyboardMonitor:
    def __init__(
        self,
        config: dict[str, Any],
        sound_player: KeyboardSoundPlayer | None,
        hotkey_manager: HotkeyManager | None,
    ) -> None:
        self.config = config
        self.sound_player = sound_player
        self.hotkey_manager = hotkey_manager
        self.devices = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if evdev is None:
            raise RuntimeError(
                "Global keyboard input requires evdev. "
                "Install dependencies with: uv pip install -r requirements.txt"
            )

        self.devices = self._open_keyboard_devices()
        if not self.devices:
            raise RuntimeError(
                "No readable keyboard devices found under /dev/input. "
                "Give this user permission to read input devices, then restart."
            )

        device_names = ", ".join(device.name for device in self.devices)
        print(f"Keyboard monitor active: {device_names}")
        self.thread = threading.Thread(target=self._run, name="keyboard-monitor", daemon=True)
        self.thread.start()

    def _open_keyboard_devices(self) -> list[Any]:
        devices = []
        permission_errors = []
        for device_path in evdev.list_devices():
            try:
                device = evdev.InputDevice(device_path)
                capabilities = device.capabilities()
                key_codes = set(capabilities.get(evdev.ecodes.EV_KEY, []))
                if self._looks_like_keyboard(key_codes):
                    devices.append(device)
                else:
                    device.close()
            except PermissionError:
                permission_errors.append(device_path)
            except OSError:
                continue

        if not devices and permission_errors:
            paths = ", ".join(permission_errors)
            raise RuntimeError(
                "Permission denied while reading keyboard devices. "
                f"Unreadable devices: {paths}. "
                "Add your user to the input group, add a udev rule, or run with suitable permissions."
            )

        return devices

    @staticmethod
    def _looks_like_keyboard(key_codes: set[int]) -> bool:
        return {
            evdev.ecodes.KEY_A,
            evdev.ecodes.KEY_Z,
            evdev.ecodes.KEY_SPACE,
            evdev.ecodes.KEY_ENTER,
        }.issubset(key_codes)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                readable, _, _ = select.select(self.devices, [], [], 0.25)
            except (OSError, ValueError):
                return

            for device in readable:
                try:
                    events = device.read()
                except (BlockingIOError, OSError):
                    continue

                for event in events:
                    if event.type != evdev.ecodes.EV_KEY:
                        continue
                    receipt_ns = time.perf_counter_ns()
                    receipt_wall = time.time()
                    receipt_mono = receipt_ns / 1_000_000_000.0
                    event_ts = event_timestamp_seconds(event)
                    event_to_receipt_ms = kernel_to_user_ms(
                        event_ts, receipt_wall, receipt_mono
                    )
                    evdev_name = evdev_code_name(event.code)
                    if evdev_name is None:
                        continue
                    if self.sound_player is not None:
                        self.sound_player.play(
                            evdev_name,
                            event.value,
                            receipt_ns=receipt_ns,
                            kernel_to_receipt_ms=event_to_receipt_ms,
                        )
                    if self.hotkey_manager is not None:
                        self.hotkey_manager.handle_key(evdev_name, event.value)

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        for device in self.devices:
            try:
                device.close()
            except Exception:
                pass


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
    return any(fnmatch.fnmatchcase(normalized, pattern.casefold()) for pattern in patterns)


def is_config_ignored_audio_sink(sink: Any, config: dict[str, Any]) -> bool:
    detection_config = config.get("audio_detection", {})
    if not isinstance(detection_config, dict):
        return False

    app_name = proplist_value(sink, "application.name")
    media_name = proplist_value(sink, "media.name")
    process_binary = Path(proplist_value(sink, "application.process.binary")).name

    return (
        matches_any_pattern(
            app_name, string_list_setting(detection_config.get("ignore_app_names"))
        )
        or matches_any_pattern(
            media_name, string_list_setting(detection_config.get("ignore_media_names"))
        )
        or matches_any_pattern(
            process_binary,
            string_list_setting(detection_config.get("ignore_process_binaries")),
        )
    )


def is_internal_audio_sink(
    sink: Any, ignored_process_ids: set[str], ignored_sink_indexes: set[int]
) -> bool:
    app_name = proplist_value(sink, "application.name")
    media_name = proplist_value(sink, "media.name")
    process_id = proplist_value(sink, "application.process.id")
    process_binary = Path(proplist_value(sink, "application.process.binary")).name
    index = sink_index(sink)

    if index is not None and index in ignored_sink_indexes:
        return True
    if app_name in {MY_APP_NAME, KEYBOARD_APP_NAME}:
        return True
    if media_name == KEYBOARD_APP_NAME:
        return True
    if process_id and process_id in ignored_process_ids:
        return True
    if process_binary in {"bgmusic.py"}:
        return True
    return False


def snapshot_sink_indexes(logger: DebugLogger) -> set[int]:
    try:
        with pulsectl.Pulse("bg-music-sink-snapshot") as pulse:
            indexes = {
                index
                for sink in pulse.sink_input_list()
                if (index := sink_index(sink)) is not None
            }
            logger.log(f"sink snapshot: {sorted(indexes)}")
            return indexes
    except Exception as error:
        logger.log(f"sink snapshot failed: {error}")
        return set()


class AudioDebugState:
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
    for sink in pulse.sink_input_list():
        index = sink_index(sink)
        if is_internal_audio_sink(sink, ignored_process_ids, ignored_sink_indexes):
            if logger.enabled and index is not None and index not in debug_state.seen_internal_sinks:
                debug_state.seen_internal_sinks.add(index)
                logger.log(f"ignoring internal audio sink: {describe_sink(sink)}")
            continue
        if is_config_ignored_audio_sink(sink, config):
            if (
                logger.enabled
                and index is not None
                and index not in debug_state.seen_config_ignored_sinks
            ):
                debug_state.seen_config_ignored_sinks.add(index)
                logger.log(f"ignoring configured audio sink: {describe_sink(sink)}")
            continue
        if not sink.corked:
            return True, describe_sink(sink)
    return False, None


class MusicDebugTracker:
    def __init__(self, logger: DebugLogger) -> None:
        self.logger = logger
        self.last_path: str | None = None
        self.last_pause: bool | None = None
        self.last_reason: str | None = None

    def tick(self, reason: str) -> None:
        if not self.logger.enabled:
            return

        path = get_mpv_property("path")
        pause = get_mpv_property("pause")
        if path != self.last_path:
            self.last_path = path
            if path:
                self.logger.log(f"track active: {Path(str(path)).name}")
            else:
                self.logger.log("track active: none")

        if pause != self.last_pause or reason != self.last_reason:
            self.last_pause = pause
            self.last_reason = reason
            state = "paused" if pause else "playing"
            self.logger.log(f"music state: {state}; reason={reason}")


def configured_music_extensions(config: dict[str, Any]) -> set[str]:
    configured = config["music"].get("supported_extensions", DEFAULT_MUSIC_EXTENSIONS)
    if not isinstance(configured, list):
        print("Warning: music.supported_extensions must be a list; using defaults.")
        configured = DEFAULT_MUSIC_EXTENSIONS

    extensions = set()
    for value in configured:
        if not isinstance(value, str) or not value.strip():
            continue
        extension = value.strip().lower()
        if not extension.startswith("."):
            extension = f".{extension}"
        extensions.add(extension)
    return extensions or set(DEFAULT_MUSIC_EXTENSIONS)


def keyboard_latency_setting(value: Any) -> str | float:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"low", "high"}:
            return normalized
        try:
            return float(normalized)
        except ValueError:
            return "low"
    if isinstance(value, (int, float)):
        return float(value)
    return "low"


def discover_music_files(music_dir: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        (
            path
            for path in music_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in extensions
            and not path.name.lower().endswith(".part")
        ),
        key=lambda path: str(path).lower(),
    )


def build_mpv_command(
    music_files: list[Path], loop_enabled: bool, shuffle: bool
) -> list[str]:
    loop_value = "inf" if loop_enabled else "no"
    command = [
        "mpv",
        "--no-video",
        f"--input-ipc-server={SOCKET_PATH}",
        "--idle=yes",
        f"--audio-client-name={MY_APP_NAME}",
        f"--loop-playlist={loop_value}",
        "--volume-max=100",
    ]
    if shuffle:
        command.append("--shuffle")
    command.extend(str(path) for path in music_files)
    return command


def keyboard_audio_settings(config: dict[str, Any]) -> tuple[str | float, int]:
    keyboard_config = config["keyboard_sounds"]
    preset = str(keyboard_config.get("performance_preset", "low_latency")).strip().lower()
    default_latency: str | float = 0.005 if preset == "low_latency" else "low"
    default_blocksize = 64 if preset == "low_latency" else 128
    return (
        keyboard_latency_setting(keyboard_config.get("latency", default_latency)),
        int_setting(keyboard_config.get("blocksize"), default_blocksize),
    )


def start_keyboard_features(
    config: dict[str, Any], logger: DebugLogger, store: "SettingsStore"
) -> tuple[Any, Any]:
    keyboard_config = config["keyboard_sounds"]
    sound_player = None

    try:
        latency, blocksize = keyboard_audio_settings(config)
        sound_player = KeyboardSoundPlayer(
            config=config,
            soundpack_dir=resolve_project_path(keyboard_config.get("soundpack_directory")),
            enabled=get_state(config)["keyboard_sounds_enabled"],
            event_mode=str(keyboard_config.get("event", "keydown")).strip().lower(),
            volume=get_state(config)["keyboard_volume"],
            max_polyphony=int_setting(keyboard_config.get("max_polyphony"), 32),
            latency=latency,
            blocksize=blocksize,
            state_sync_interval=float_setting(
                keyboard_config.get("state_sync_interval"), 0.1
            ),
            trim_leading_silence=bool_setting(
                keyboard_config.get("trim_leading_silence"), True
            ),
            trim_threshold_ratio=float_setting(
                keyboard_config.get("trim_threshold_ratio"), 0.02
            ),
            trim_max_ms=float_setting(keyboard_config.get("trim_max_ms"), 8.0),
            trim_preroll_ms=float_setting(
                keyboard_config.get("trim_preroll_ms"), 0.5
            ),
            logger=logger,
        )
        print("Keyboard soundpack loaded.")
    except Exception as error:
        print(f"Warning: keyboard sounds disabled: {error}")

    callbacks = {
        "toggle_music": lambda: toggle_music(config),
        "next_track": next_track,
        "previous_track": previous_track,
        "toggle_loop": lambda: toggle_loop(config, store),
        "toggle_keyboard_sounds": lambda: toggle_keyboard_sounds(config, sound_player, store),
        "volume_up": lambda: volume_up(config, store),
        "volume_down": lambda: volume_down(config, store),
        "toggle_mute": toggle_mute,
        "keyboard_volume_up": lambda: keyboard_volume_up(config, sound_player, store),
        "keyboard_volume_down": lambda: keyboard_volume_down(config, sound_player, store),
    }
    hotkey_manager = HotkeyManager(config, callbacks)

    if not hotkey_manager.hotkeys and sound_player is None:
        return sound_player, None

    try:
        keyboard_monitor = KeyboardMonitor(config, sound_player, hotkey_manager)
        keyboard_monitor.start()
        return sound_player, keyboard_monitor
    except Exception as error:
        print(f"Warning: keyboard monitor disabled: {error}")
        return sound_player, None


def _get_pipewire_force_quantum() -> int | None:
    """Read clock.force-quantum from PipeWire's settings metadata.

    Returns the current value (>0), or None if the key is absent / unset / zero.
    PipeWire stores quantum overrides in the "settings" metadata under object ID 0.
    A value of 0 means "no override" — so we return None in that case.
    """
    try:
        result = subprocess.run(
            ["pw-metadata", "-n", "settings", "0"],
            capture_output=True, text=True, timeout=2,
        )
        for line in result.stdout.splitlines():
            if "clock.force-quantum" in line and "value:'" in line:
                # Line format: "update: id:0 key:'clock.force-quantum' value:'1024' type:''"
                value_str = line.split("value:'", 1)[1].split("'", 1)[0]
                parsed = int(value_str)
                return parsed if parsed > 0 else None
    except Exception:
        pass
    return None


def _apply_pipewire_force_quantum(frames: int) -> bool:
    """Write clock.force-quantum to PipeWire's settings metadata.

    frames=0 removes the override and lets PipeWire negotiate freely.
    Returns True on success, False if pw-metadata is missing or fails.
    """
    try:
        subprocess.run(
            ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(frames)],
            capture_output=True, text=True, timeout=2, check=True,
        )
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def handle_start(args: argparse.Namespace) -> None:
    # The audio callback runs in a native PortAudio C thread.  To call Python code
    # it must acquire the GIL.  Python's default GIL yield interval (5 ms) means the
    # callback thread can wait up to 5 ms for another Python thread to release the
    # GIL — visible as enqueue->callback spikes in --deep-debug output.
    #
    # Best fix: run with the free-threaded Python build (no GIL at all):
    #   uv run --python cpython-3.14t bgmusic.py
    #
    # Fallback fix on standard Python: shrink the yield interval so no thread can
    # hold the GIL longer than ~1 ms.  In normal operation (no --deep-debug) there
    # are only a few mostly-sleeping threads, so the overhead is negligible.
    if not getattr(sys, "_is_gil_enabled", lambda: True)():
        pass  # free-threaded Python — GIL doesn't exist, nothing to tune
    else:
        sys.setswitchinterval(0.001)

    config = load_config(Path(args.config))
    logger = DebugLogger(
        bool_setting(getattr(args, "debug", False), False),
        bool_setting(getattr(args, "deep_debug", False), False),
    )
    ensure_start_dependencies()
    logger.log(
        f"using config: {Path(args.config).resolve()} | "
        f"GIL={'disabled' if not getattr(sys, '_is_gil_enabled', lambda: True)() else 'enabled, interval=1ms'}"
    )
    logger.deep("deep keyboard latency logging enabled for first 100 key sounds")

    # Reduce PipeWire's graph quantum so the audio callback fires more frequently.
    #
    # Background: PortAudio's callback is driven by PipeWire's processing cycle
    # (not by the blocksize parameter).  With PipeWire's default quantum of 1024
    # frames at 48 kHz (~21 ms), the callback only fires every 21 ms regardless of
    # blocksize — causing up to 21 ms of "enqueue->callback" jitter, plus multiple
    # quanta of pipeline buffering on top (total perceived latency: 50-100 ms).
    #
    # clock.force-quantum overrides the negotiated quantum for ALL nodes in the
    # PipeWire graph.  256 frames at 48 kHz ≈ 5.3 ms per cycle.  3 quanta in the
    # typical pipeline = ~16 ms total.  128 frames ≈ 2.7 ms / ~8 ms total (lower
    # latency, slightly more CPU).  Set pipewire_quantum: 0 to leave it unchanged.
    _pw_quantum_target = int_setting(
        config["keyboard_sounds"].get("pipewire_quantum"), 256
    )
    _pw_quantum_original: int | None = None
    _pw_quantum_applied = False
    if _pw_quantum_target > 0:
        _pw_quantum_original = _get_pipewire_force_quantum()
        if _apply_pipewire_force_quantum(_pw_quantum_target):
            _pw_quantum_applied = True
            logger.log(
                f"PipeWire quantum set to {_pw_quantum_target} frames "
                f"(~{_pw_quantum_target / 48:.1f} ms at 48 kHz); "
                f"was {_pw_quantum_original if _pw_quantum_original else 'unset'}"
            )
        else:
            logger.log(
                "could not set PipeWire quantum "
                "(pw-metadata not found or failed — install pipewire package)"
            )

    # Load persistent user settings from bgmusic_settings.json inside the project dir.
    # manual_pause is intentionally excluded — the daemon always starts playing.
    store = SettingsStore.load(SETTINGS_FILE, config)
    logger.log(
        f"settings: loop={store.get('loop')} "
        f"kb_sounds={store.get('keyboard_sounds_enabled')} "
        f"kb_vol={store.get('keyboard_volume', 1.0) * 100:.0f}% "
        f"music_vol={store.get('music_volume', 100.0):.0f}% "
        f"last_track={Path(store.get('last_track')).name if store.get('last_track') else 'none'}"
    )

    music_dir = resolve_project_path(config["music"].get("directory", "music"))
    if not music_dir.exists():
        raise RuntimeError(f"Music directory not found: {music_dir}")
    music_files = discover_music_files(music_dir, configured_music_extensions(config))
    if not music_files:
        raise RuntimeError(f"No supported music files found in: {music_dir}")
    logger.log("playlist:")
    for index, path in enumerate(music_files, start=1):
        logger.log(f"  {index}. {path}")

    loop_enabled = store.get("loop")
    shuffle = bool_setting(config["music"].get("shuffle"), False)
    arg_shuffle = getattr(args, "shuffle", None)
    if arg_shuffle is not None:
        shuffle = arg_shuffle

    set_state({
        "manual_pause": False,
        "loop": store.get("loop"),
        "keyboard_sounds_enabled": store.get("keyboard_sounds_enabled"),
        "keyboard_volume": store.get("keyboard_volume"),
    })
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    command = build_mpv_command(music_files, loop_enabled, shuffle)
    print(
        f"Starting mpv with {len(music_files)} track(s) "
        f"(shuffle: {shuffle}, loop: {loop_enabled})..."
    )
    try:
        mpv_process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError as error:
        raise RuntimeError("mpv was not found. Install mpv and try again.") from error
    ignored_audio_process_ids = {str(os.getpid()), str(mpv_process.pid)}
    logger.log(
        "ignoring audio process ids: "
        f"python={os.getpid()}, mpv={mpv_process.pid}"
    )

    # Wait for mpv to create its IPC socket instead of sleeping a fixed second.
    # mpv typically creates the socket in < 200 ms; we poll every 25 ms.
    _socket_wait_start = time.monotonic()
    _socket_deadline = _socket_wait_start + 5.0
    while not SOCKET_PATH.exists():
        if time.monotonic() >= _socket_deadline:
            logger.log("warning: mpv socket did not appear within 5 s")
            break
        time.sleep(0.025)
    logger.log(f"mpv socket ready ({(time.monotonic() - _socket_wait_start) * 1000:.0f} ms)")

    set_mpv_loop(loop_enabled)

    # Restore saved music volume (cap at 100%).
    _saved_vol = store.get("music_volume", 100.0)
    if _saved_vol != 100.0:
        send_ipc_command({"command": ["set_property", "volume", _saved_vol]})
        logger.log(f"restored music volume: {_saved_vol:.0f}%")

    # Resume from the last played track if it is still in the playlist.
    _saved_track = store.get("last_track")
    if _saved_track:
        for _idx, _path in enumerate(music_files):
            if str(_path) == _saved_track:
                if _idx > 0:
                    send_ipc_command({"command": ["set_property", "playlist-pos", _idx]})
                    logger.log(f"resumed from track {_idx + 1}: {Path(_saved_track).name}")
                break

    pre_keyboard_sink_indexes = snapshot_sink_indexes(logger)
    sound_player, keyboard_monitor = start_keyboard_features(config, logger, store)
    time.sleep(0.25)
    post_keyboard_sink_indexes = snapshot_sink_indexes(logger)
    ignored_sink_indexes = post_keyboard_sink_indexes - pre_keyboard_sink_indexes
    if ignored_sink_indexes:
        logger.log(
            "ignoring startup-created sink indexes: "
            f"{sorted(ignored_sink_indexes)}"
        )
    cleaned_up = False

    def cleanup(signum: int | None = None, _frame: Any = None) -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True

        print("\nStopping background music...")

        # Final snapshot from mpv and state file while everything is still alive.
        # store.set writes to disk immediately, so these are the last saved values.
        _cur_track = get_mpv_property("path")
        _cur_vol = get_mpv_property("volume")
        _cur_state = get_state(config)
        if _cur_track:
            store.set("last_track", str(_cur_track))
        if _cur_vol is not None:
            store.set("music_volume", clamp(float_setting(_cur_vol, 100.0), 0.0, 100.0))
        store.set("keyboard_volume", _cur_state.get("keyboard_volume", store.get("keyboard_volume", 1.0)))
        store.set("keyboard_sounds_enabled", _cur_state.get("keyboard_sounds_enabled", store.get("keyboard_sounds_enabled", True)))
        store.set("loop", _cur_state.get("loop", store.get("loop", True)))

        if keyboard_monitor is not None:
            keyboard_monitor.close()
        if sound_player is not None:
            sound_player.close()

        send_ipc_command({"command": ["quit"]})
        try:
            mpv_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            mpv_process.terminate()
            try:
                mpv_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                mpv_process.kill()

        for path in (SOCKET_PATH, STATE_FILE):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

        if _pw_quantum_applied:
            restore_to = _pw_quantum_original if _pw_quantum_original is not None else 0
            _apply_pipewire_force_quantum(restore_to)
            logger.log(
                f"PipeWire quantum restored to "
                f"{restore_to if restore_to else 'default (unset)'}"
            )

        if signum is not None:
            sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Track last known track so the store is updated when the song changes.
    _known_track: str | None = store.get("last_track")
    print("Monitoring started.")
    audio_debug_state = AudioDebugState()
    music_debug_tracker = MusicDebugTracker(logger)
    try:
        with pulsectl.Pulse("bg-music-monitor") as pulse:
            while True:
                try:
                    state = get_state(config)
                    if state["manual_pause"]:
                        set_mpv_pause(True)
                        music_debug_tracker.tick("manual pause")
                        time.sleep(CHECK_INTERVAL)
                        continue

                    external_playing, external_trigger = other_audio_is_playing(
                        pulse,
                        config,
                        ignored_audio_process_ids,
                        ignored_sink_indexes,
                        logger,
                        audio_debug_state,
                    )
                    if external_trigger != audio_debug_state.external_trigger:
                        audio_debug_state.external_trigger = external_trigger
                        if external_trigger:
                            logger.log(
                                "external audio detected; pausing music: "
                                f"{external_trigger}"
                            )
                        else:
                            logger.log("external audio cleared; resuming music")

                    set_mpv_pause(external_playing)
                    reason = (
                        f"external audio ({external_trigger})"
                        if external_playing and external_trigger
                        else "no external audio"
                    )
                    music_debug_tracker.tick(reason)

                    # Keep last_track and music_volume current in the store.
                    # music_volume is also updated immediately by hotkey callbacks,
                    # but this catches CLI-based volume commands too.
                    _cur_track = get_mpv_property("path")
                    _cur_track_str = str(_cur_track) if _cur_track else None
                    if _cur_track_str != _known_track:
                        _known_track = _cur_track_str
                        store.set("last_track", _cur_track_str)
                    _cur_vol = get_mpv_property("volume")
                    if _cur_vol is not None:
                        store.set("music_volume", clamp(float_setting(_cur_vol, 100.0), 0.0, 100.0))

                    time.sleep(CHECK_INTERVAL)
                except pulsectl.PulseError:
                    logger.log("PulseAudio connection error; retrying")
                    time.sleep(2)
                except Exception as error:
                    raise RuntimeError(f"Monitor loop failed: {error}") from error
    finally:
        cleanup()


def handle_control(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))

    if args.action == "toggle":
        toggle_music(config)
    elif args.action == "next":
        next_track()
    elif args.action == "previous":
        previous_track()
    elif args.action == "loop":
        toggle_loop(config)
    elif args.action == "keyboard-sounds":
        toggle_keyboard_sounds(config)
    elif args.action == "volume-up":
        volume_up(config)
    elif args.action == "volume-down":
        volume_down(config)
    elif args.action == "mute":
        toggle_mute()
    elif args.action == "keyboard-volume-up":
        keyboard_volume_up(config)
    elif args.action == "keyboard-volume-down":
        keyboard_volume_down(config)
    elif args.action == "audio-devices":
        list_audio_devices()
    elif args.action == "volume":
        try:
            value = float(args.value)
        except ValueError:
            print("Invalid volume number")
            return
        adjust_volume(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OperaGX-style background music manager",
        epilog="Run without an action to start the daemon.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print verbose playback and audio-detection logs",
    )
    parser.add_argument(
        "--deep-debug",
        action="store_true",
        help="Print per-key keyboard latency diagnostics",
    )
    subparsers = parser.add_subparsers(dest="action")

    start_parser = subparsers.add_parser("start", help="Start the music daemon")
    start_parser.add_argument(
        "--shuffle",
        action="store_true",
        default=None,
        help="Shuffle the playlist for this run",
    )
    start_parser.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print verbose playback and audio-detection logs",
    )
    start_parser.add_argument(
        "--deep-debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print per-key keyboard latency diagnostics",
    )

    subparsers.add_parser("toggle", help="Toggle manual pause")
    subparsers.add_parser("next", help="Skip to the next song")
    subparsers.add_parser("previous", help="Skip to the previous song")
    subparsers.add_parser("loop", help="Toggle playlist looping")
    subparsers.add_parser("keyboard-sounds", help="Toggle keyboard sounds")
    subparsers.add_parser("volume-up", help="Increase volume by configured step")
    subparsers.add_parser("volume-down", help="Decrease volume by configured step")
    subparsers.add_parser("mute", help="Toggle mpv mute")
    subparsers.add_parser(
        "keyboard-volume-up", help="Increase keyboard sound volume"
    )
    subparsers.add_parser(
        "keyboard-volume-down", help="Decrease keyboard sound volume"
    )
    subparsers.add_parser("audio-devices", help="List available audio output devices")

    volume_parser = subparsers.add_parser("volume", help="Adjust mpv volume")
    volume_parser.add_argument("value", help="Amount to change, such as +10 or -5")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.action in {None, "start"}:
            handle_start(args)
        else:
            handle_control(args)
    except RuntimeError as error:
        print(f"Error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
