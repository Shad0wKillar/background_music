"""HotkeyManager, KeyboardMonitor, and start_keyboard_features.

HotkeyManager  — stateful combo tracker; fires action callbacks.
KeyboardMonitor — reads raw evdev events in a daemon thread; dispatches
                  to HotkeyManager and KeyboardSoundPlayer.
start_keyboard_features — wires everything together and returns
                          (sound_player, keyboard_monitor).
"""
from __future__ import annotations

import select
import threading
import time
from typing import Any

from bgmusic.actions import (
    keyboard_volume_down, keyboard_volume_up,
    next_track, previous_track,
    toggle_keyboard_sounds, toggle_loop, toggle_mute, toggle_music,
    volume_down, volume_up,
)
from bgmusic.config import bool_setting, float_setting, int_setting, resolve_project_path
from bgmusic.debug import DebugLogger, event_timestamp_seconds, kernel_to_user_ms
from bgmusic.keymaps import (
    MODIFIER_TOKENS, evdev_code_name, evdev_to_hotkey_token, parse_hotkeys,
)
from bgmusic.sound_player import KeyboardSoundPlayer
from bgmusic.state import SettingsStore, get_state

try:
    import evdev
except ImportError:
    evdev = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# HotkeyManager
# ---------------------------------------------------------------------------

class HotkeyManager:
    """Tracks held keys and fires the matching action callback on combo match."""

    def __init__(self, config: dict[str, Any], callbacks: dict[str, Any]) -> None:
        self.hotkeys = parse_hotkeys(config)
        self.callbacks = callbacks
        self.active_tokens: set[str] = set()
        # Prevent a held combo from re-firing until all its keys are released.
        self.triggered_combos: set[frozenset[str]] = set()

    def handle_key(self, evdev_name: str, event_value: int) -> None:
        token = evdev_to_hotkey_token(evdev_name)
        if token is None:
            return

        if event_value == 0:  # key release
            self.active_tokens.discard(token)
            self.triggered_combos = {
                c for c in self.triggered_combos if c.issubset(self.active_tokens)
            }
            return

        if event_value == 2:  # key repeat — ignore
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


# ---------------------------------------------------------------------------
# KeyboardMonitor
# ---------------------------------------------------------------------------

class KeyboardMonitor:
    """Reads raw evdev events and dispatches them to sound player and hotkeys."""

    def __init__(
        self,
        config: dict[str, Any],
        sound_player: KeyboardSoundPlayer | None,
        hotkey_manager: HotkeyManager | None,
    ) -> None:
        self.config = config
        self.sound_player = sound_player
        self.hotkey_manager = hotkey_manager
        self.devices: list[Any] = []
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
        device_names = ", ".join(d.name for d in self.devices)
        print(f"Keyboard monitor active: {device_names}")
        self.thread = threading.Thread(target=self._run, name="keyboard-monitor", daemon=True)
        self.thread.start()

    def _open_keyboard_devices(self) -> list[Any]:
        devices: list[Any] = []
        permission_errors: list[Any] = []
        for device_path in evdev.list_devices():
            try:
                device = evdev.InputDevice(device_path)
                key_codes = set(device.capabilities().get(evdev.ecodes.EV_KEY, []))
                if self._looks_like_keyboard(key_codes):
                    devices.append(device)
                else:
                    device.close()
            except PermissionError:
                permission_errors.append(device_path)
            except OSError:
                continue
        if not devices and permission_errors:
            paths = ", ".join(str(p) for p in permission_errors)
            raise RuntimeError(
                "Permission denied while reading keyboard devices. "
                f"Unreadable devices: {paths}. "
                "Add your user to the input group, add a udev rule, "
                "or run with suitable permissions."
            )
        return devices

    @staticmethod
    def _looks_like_keyboard(key_codes: set[int]) -> bool:
        return {
            evdev.ecodes.KEY_A, evdev.ecodes.KEY_Z,
            evdev.ecodes.KEY_SPACE, evdev.ecodes.KEY_ENTER,
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
                    receipt_ns   = time.perf_counter_ns()
                    receipt_wall = time.time()
                    receipt_mono = receipt_ns / 1_000_000_000.0
                    event_ts = event_timestamp_seconds(event)
                    event_to_receipt_ms = kernel_to_user_ms(event_ts, receipt_wall, receipt_mono)
                    evdev_name = evdev_code_name(event.code)
                    if evdev_name is None:
                        continue
                    if self.sound_player is not None:
                        self.sound_player.play(
                            evdev_name, event.value,
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


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def keyboard_audio_settings(config: dict[str, Any]) -> tuple[str | float, int]:
    """Return (latency, blocksize) from config with performance-preset defaults."""
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


def start_keyboard_features(
    config: dict[str, Any],
    logger: DebugLogger,
    store: SettingsStore,
) -> tuple[KeyboardSoundPlayer | None, KeyboardMonitor | None]:
    """Initialise sound player + keyboard monitor and return them."""
    kb = config["keyboard_sounds"]
    sound_player: KeyboardSoundPlayer | None = None

    try:
        latency, blocksize = keyboard_audio_settings(config)
        state = get_state(config)
        sound_player = KeyboardSoundPlayer(
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
    except Exception as error:
        print(f"Warning: keyboard sounds disabled: {error}")

    # Every hotkey callback gets a reference to the store so it can
    # persist the change immediately without waiting for the next loop tick.
    callbacks = {
        "toggle_music":           lambda: toggle_music(config),
        "next_track":             next_track,
        "previous_track":         previous_track,
        "toggle_loop":            lambda: toggle_loop(config, store),
        "toggle_keyboard_sounds": lambda: toggle_keyboard_sounds(config, sound_player, store),
        "volume_up":              lambda: volume_up(config, store),
        "volume_down":            lambda: volume_down(config, store),
        "toggle_mute":            toggle_mute,
        "keyboard_volume_up":     lambda: keyboard_volume_up(config, sound_player, store),
        "keyboard_volume_down":   lambda: keyboard_volume_down(config, sound_player, store),
    }
    hotkey_manager = HotkeyManager(config, callbacks)

    if not hotkey_manager.hotkeys and sound_player is None:
        return sound_player, None

    try:
        monitor = KeyboardMonitor(config, sound_player, hotkey_manager)
        monitor.start()
        return sound_player, monitor
    except Exception as error:
        print(f"Warning: keyboard monitor disabled: {error}")
        return sound_player, None
