"""Raw evdev keyboard monitor."""
from __future__ import annotations

import select
import threading
import time
from typing import Any

from bgmusic.config import float_setting
from bgmusic.debug import event_timestamp_seconds, kernel_to_user_ms
from bgmusic.keymaps import evdev_code_name

try:
    import evdev
except ImportError:
    evdev = None  # type: ignore[assignment]


class KeyboardMonitor:
    """Reads raw evdev events and dispatches them."""

    def __init__(self, config: dict[str, Any], sound_player: Any, hotkey_manager: Any) -> None:
        self.config = config
        self.sound_player = sound_player
        self.hotkey_manager = hotkey_manager
        self.devices: list[Any] = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        kb = config.get("keyboard_sounds", {})
        self.duplicate_suppression_ns = int(
            max(0.0, float_setting(kb.get("duplicate_suppression_ms"), 12.0)) * 1_000_000
        )
        self._last_key_events: dict[tuple[str, int], int] = {}

    def start(self) -> None:
        if evdev is None:
            raise RuntimeError("Global keyboard input requires evdev.")
        self.devices = self._open_keyboard_devices()
        if not self.devices:
            raise RuntimeError("No readable keyboard devices found under /dev/input.")
        print(f"Keyboard monitor active: {', '.join(d.name for d in self.devices)}")
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
            raise RuntimeError(f"Permission denied while reading keyboard devices: {paths}.")
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
                self._read_device(device)

    def _read_device(self, device: Any) -> None:
        try:
            events = device.read()
        except (BlockingIOError, OSError):
            return
        for event in events:
            if event.type != evdev.ecodes.EV_KEY:
                continue
            receipt_ns = time.perf_counter_ns()
            evdev_name = evdev_code_name(event.code)
            if evdev_name is None or self._is_duplicate_event(evdev_name, event.value, receipt_ns):
                continue
            self._dispatch(event, evdev_name, receipt_ns)

    def _dispatch(self, event: Any, evdev_name: str, receipt_ns: int) -> None:
        event_ts = event_timestamp_seconds(event)
        lag_ms = kernel_to_user_ms(event_ts, time.time(), receipt_ns / 1_000_000_000.0)
        if self.sound_player is not None:
            self.sound_player.play(evdev_name, event.value, receipt_ns=receipt_ns,
                                   kernel_to_receipt_ms=lag_ms)
        if self.hotkey_manager is not None:
            self.hotkey_manager.handle_key(evdev_name, event.value)

    def _is_duplicate_event(self, evdev_name: str, event_value: int, receipt_ns: int) -> bool:
        if self.duplicate_suppression_ns <= 0 or event_value == 2:
            return False
        key = (evdev_name, event_value)
        previous_ns = self._last_key_events.get(key)
        self._last_key_events[key] = receipt_ns
        return previous_ns is not None and receipt_ns - previous_ns <= self.duplicate_suppression_ns

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        for device in self.devices:
            try:
                device.close()
            except Exception:
                pass
