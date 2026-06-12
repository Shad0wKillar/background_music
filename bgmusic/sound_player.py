"""Keyboard sound mixer."""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from bgmusic.config import clamp
from bgmusic.constants import STATE_FILE
from bgmusic.debug import DebugLogger, format_ms
from bgmusic.sound_stream import keyboard_stream_environment, open_keyboard_stream
from bgmusic.soundpack_loader import load_soundpack, precompute_evdev_clips
from bgmusic.state import get_state


class KeyboardSoundPlayer:
    """Loads a keyboard soundpack and mixes clips in real time."""

    def __init__(
        self, config: dict[str, Any], soundpack_dir: Path, enabled: bool,
        event_mode: str, volume: float, max_polyphony: int,
        latency: str | float, blocksize: int, state_sync_interval: float,
        trim_leading_silence: bool, trim_threshold_ratio: float,
        trim_max_ms: float, trim_preroll_ms: float, logger: DebugLogger,
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

        self.np, self.sd, self.config, self.logger = np, sd, config, logger
        self.enabled = enabled
        self.event_mode = event_mode if event_mode in {"keydown", "keyup", "both"} else "keydown"
        self.volume = clamp(volume, 0.0, 1.0)
        self.max_polyphony = max(1, max_polyphony)
        self.state_sync_interval = max(0.0, state_sync_interval)
        self.stop_event = threading.Event()
        self.latency_events: deque[str] = deque()
        self.latency_lock = threading.Lock()
        self.deep_key_count = 0
        self.deep_key_limit = 100
        self.lock = threading.Lock()
        self.active: list[list[Any]] = []
        self._state_mtime_ns: int | None = None
        self.state_thread: threading.Thread | None = None
        self.latency_thread: threading.Thread | None = None

        self.clips, self.sample_rate, self.channels = load_soundpack(
            soundpack_dir, sf, np, trim_leading_silence, trim_threshold_ratio,
            trim_max_ms, trim_preroll_ms, logger,
        )
        self.evdev_clips = precompute_evdev_clips(self.clips)
        logger.log(
            f"keyboard clips loaded: {len(self.clips)} slices, "
            f"{len(self.evdev_clips)} evdev mappings"
        )
        with keyboard_stream_environment(latency):
            self.stream = open_keyboard_stream(
                sd, self._callback, self.sample_rate, self.channels,
                latency, blocksize, logger,
            )
        self._start_threads()

    def _start_threads(self) -> None:
        self.state_thread = threading.Thread(
            target=self._state_watcher, name="keyboard-state-watcher", daemon=True
        )
        self.state_thread.start()
        if self.logger.deep_enabled:
            self.latency_thread = threading.Thread(
                target=self._latency_reporter, name="keyboard-latency-reporter", daemon=True
            )
            self.latency_thread.start()

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        outdata.fill(0)
        with self.lock:
            volume = self.volume
            remaining = []
            for clip, position, probe in self.active:
                chunk = clip[position: position + frames]
                if len(chunk):
                    outdata[: len(chunk)] += chunk
                    if probe is not None and position == 0:
                        self._record_latency_probe(probe, time_info, frames)
                next_pos = position + len(chunk)
                if next_pos < len(clip):
                    remaining.append([clip, next_pos, None])
            self.active = remaining
        outdata *= volume
        self.np.clip(outdata, -1.0, 1.0, out=outdata)

    def _record_latency_probe(self, probe: dict[str, Any], time_info: Any, frames: int) -> None:
        if not self.logger.deep_enabled:
            return
        callback_ns = time.perf_counter_ns()
        current_time = getattr(time_info, "currentTime", None)
        dac_time = getattr(time_info, "outputBufferDacTime", None)
        cb_to_dac_ms = None
        if isinstance(current_time, (int, float)) and isinstance(dac_time, (int, float)):
            cb_to_dac_ms = max(0.0, (dac_time - current_time) * 1000.0)
        rcpt_to_cb_ms = (callback_ns - probe["receipt_ns"]) / 1_000_000.0
        rcpt_to_dac_ms = None if cb_to_dac_ms is None else rcpt_to_cb_ms + cb_to_dac_ms
        msg = (
            f"key={probe['key']} event={probe['event_value']} "
            f"kernel->receipt={format_ms(probe['kernel_to_receipt_ms'])} "
            f"receipt->enqueue={format_ms(probe['receipt_to_enqueue_ms'])} "
            f"enqueue->callback={(callback_ns - probe['enqueue_ns']) / 1_000_000.0:.2f}ms "
            f"receipt->callback={rcpt_to_cb_ms:.2f}ms "
            f"callback->dac={format_ms(cb_to_dac_ms)} "
            f"receipt->estimated_dac={format_ms(rcpt_to_dac_ms)} frames={frames}"
        )
        with self.latency_lock:
            self.latency_events.append(msg)

    def set_enabled(self, enabled: bool) -> None:
        with self.lock:
            self.enabled = enabled

    def set_volume(self, volume: float) -> None:
        with self.lock:
            self.volume = clamp(volume, 0.0, 1.0)

    def play(
        self, evdev_name: str, event_value: int,
        receipt_ns: int | None = None,
        kernel_to_receipt_ms: float | None = None,
    ) -> None:
        if not self._event_allowed(event_value):
            return
        clip = self.evdev_clips.get((evdev_name, event_value))
        if clip is None:
            return
        probe = self._new_probe(evdev_name, event_value, receipt_ns, kernel_to_receipt_ms)
        with self.lock:
            if not self.enabled:
                return
            if len(self.active) >= self.max_polyphony:
                self.active = [] if self.max_polyphony == 1 else self.active[-(self.max_polyphony - 1):]
            self.active.append([clip, 0, probe])

    def _event_allowed(self, event_value: int) -> bool:
        if event_value == 2:
            return False
        if self.event_mode == "keydown":
            return event_value == 1
        if self.event_mode == "keyup":
            return event_value == 0
        return event_value in {0, 1}

    def _new_probe(
        self, key: str, event_value: int, receipt_ns: int | None, kernel_ms: float | None,
    ) -> dict[str, Any] | None:
        if not self.logger.deep_enabled or self.deep_key_count >= self.deep_key_limit:
            return None
        self.deep_key_count += 1
        enqueue_ns = time.perf_counter_ns()
        receipt_ns = receipt_ns or enqueue_ns
        return {
            "key": key, "event_value": event_value, "receipt_ns": receipt_ns,
            "enqueue_ns": enqueue_ns, "kernel_to_receipt_ms": kernel_ms,
            "receipt_to_enqueue_ms": (enqueue_ns - receipt_ns) / 1_000_000.0,
        }

    def close(self) -> None:
        self.stop_event.set()
        for thread in (self.state_thread, self.latency_thread):
            if thread is not None:
                thread.join(timeout=1.0)
        self._flush_latency_events()
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

    def _state_watcher(self) -> None:
        interval = max(0.05, self.state_sync_interval)
        while not self.stop_event.wait(interval):
            self.sync_from_state()

    def _latency_reporter(self) -> None:
        while not self.stop_event.wait(0.05):
            self._flush_latency_events()

    def _flush_latency_events(self) -> None:
        with self.latency_lock:
            pending = list(self.latency_events)
            self.latency_events.clear()
        for msg in pending:
            self.logger.deep(msg)

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
