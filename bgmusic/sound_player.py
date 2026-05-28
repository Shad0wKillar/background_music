"""KeyboardSoundPlayer — the in-process audio mixer for key click sounds.

Audio pipeline:
  evdev key event → play() → active list → _callback() (PortAudio C thread)
  → PortAudio ring buffer → PipeWire → DAC

The callback runs in a native C thread outside the Python GIL.  On the
free-threaded build (PYTHON_GIL=0) there is no GIL at all.  On standard
Python the switch interval is reduced to 1 ms in handle_start so the C
thread rarely has to wait.

Key design decisions:
  - All clips are pre-sliced from the OGG file at startup into numpy arrays
    so the callback never does file I/O.
  - evdev_clips is a pre-built lookup (evdev_name, event_value) → array so
    the callback path does only one dict lookup per key event.
  - Leading silence is trimmed once at startup (trim_leading_silence=True)
    so the perceived click is immediate.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from bgmusic.config import clamp
from bgmusic.constants import KEYBOARD_APP_NAME, STATE_FILE
from bgmusic.debug import DebugLogger, format_ms
from bgmusic.keymaps import evdev_code_name, evdev_to_browser_code
from bgmusic.state import get_state

try:
    import evdev
except ImportError:
    evdev = None  # type: ignore[assignment]


class KeyboardSoundPlayer:
    """Loads a MechvibesDX soundpack and mixes clips in real time."""

    def __init__(
        self,
        config: dict[str, Any],
        soundpack_dir: Path,
        enabled: bool,
        event_mode: str,           # "keydown" | "keyup" | "both"
        volume: float,             # 0.0–1.0
        max_polyphony: int,        # max simultaneous clips
        latency: str | float,      # PortAudio latency hint
        blocksize: int,            # PortAudio frames per callback
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
        self.event_mode = event_mode if event_mode in {"keydown", "keyup", "both"} else "keydown"
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

        self._load_soundpack(soundpack_dir, sf, trim_leading_silence,
                             trim_threshold_ratio, trim_max_ms, trim_preroll_ms)
        self._precompute_evdev_clips()
        logger.log(
            f"keyboard clips loaded: {len(self.clips)} slices, "
            f"{len(self.evdev_clips)} evdev mappings"
        )

        # Set PipeWire / PulseAudio latency hints in the environment before
        # opening the PortAudio stream.  These tell the audio stack how small
        # a buffer to use; without them it defaults to 100–200 ms.
        _latency_secs = latency if isinstance(latency, float) else 0.020
        _pw_frames = max(32, round(_latency_secs * 48000))
        pulse_props = {
            "PULSE_PROP_application.name": KEYBOARD_APP_NAME,
            "PULSE_PROP_media.name":       KEYBOARD_APP_NAME,
            "PULSE_PROP_media.role":       "event",
            "PIPEWIRE_LATENCY":            f"{_pw_frames}/48000",
            "PULSE_LATENCY_MSEC":          str(max(1, round(_latency_secs * 1000))),
        }
        previous_props = {k: os.environ.get(k) for k in pulse_props}
        os.environ.update(pulse_props)
        try:
            self.stream = self._open_stream(latency, blocksize)
        finally:
            for k, v in previous_props.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.state_thread = threading.Thread(
            target=self._state_watcher, name="keyboard-state-watcher", daemon=True
        )
        self.state_thread.start()
        if self.logger.deep_enabled:
            self.latency_thread = threading.Thread(
                target=self._latency_reporter, name="keyboard-latency-reporter", daemon=True
            )
            self.latency_thread.start()

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _load_soundpack(
        self, soundpack_dir: Path, sf: Any,
        trim_silence: bool, threshold_ratio: float, max_ms: float, preroll_ms: float,
    ) -> None:
        config_path = soundpack_dir / "config.json"
        with config_path.open("r", encoding="utf-8") as f:
            soundpack_config = json.load(f)

        audio_path = soundpack_dir / soundpack_config.get("audio_file", "sound.ogg")
        audio, self.sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
        self.channels = audio.shape[1]

        for browser_code, definition in soundpack_config.get("definitions", {}).items():
            for index, timing in enumerate(definition.get("timing", [])[:2]):
                if not isinstance(timing, list) or len(timing) != 2:
                    continue
                start_ms, end_ms = timing
                start = int((float(start_ms) / 1000.0) * self.sample_rate)
                end   = int((float(end_ms)   / 1000.0) * self.sample_rate)
                if not (0 <= start < end <= len(audio)):
                    continue
                clip = audio[start:end].copy()
                if trim_silence:
                    clip, trimmed_ms = self._trim_clip(clip, threshold_ratio, max_ms, preroll_ms)
                    if self.logger.enabled and trimmed_ms > 0:
                        self.logger.log(f"trimmed {browser_code}[{index}] by {trimmed_ms:.2f}ms")
                self.clips[(browser_code, index)] = clip

    def _open_stream(self, latency: str | float, blocksize: int) -> Any:
        """Try configured settings first, fall back to safe defaults on failure."""
        attempts = [
            (latency, max(0, blocksize), "configured"),
            ("low", 128, "fallback"),
        ]
        last_error: Exception | None = None
        for attempt_latency, attempt_blocksize, label in attempts:
            stream = None
            try:
                self.logger.log(
                    f"opening keyboard audio stream: latency={attempt_latency}, "
                    f"blocksize={attempt_blocksize}, sample_rate={self.sample_rate}, "
                    f"channels={self.channels}"
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
                self.logger.log(
                    f"keyboard audio stream opened ({label}); "
                    f"actual latency={getattr(stream, 'latency', 'unknown')}; "
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
                    f"keyboard audio stream open failed ({label}): {error}"
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
        self, clip: Any, threshold_ratio: float, max_ms: float, preroll_ms: float
    ) -> tuple[Any, float]:
        """Remove leading silence from a clip; keep a short preroll for naturalness."""
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
        preroll = int(max(0.0, preroll_ms) / 1000.0 * self.sample_rate)
        max_trim = int(max(0.0, max_ms)     / 1000.0 * self.sample_rate)
        trim = min(max(0, int(hits[0]) - preroll), max_trim)
        if trim <= 0:
            return clip, 0.0
        return clip[trim:].copy(), trim / self.sample_rate * 1000.0

    def _precompute_evdev_clips(self) -> None:
        """Build the evdev_name → clip lookup so the callback needs no computation."""
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

    # ------------------------------------------------------------------
    # PortAudio callback — runs in a native C thread
    # ------------------------------------------------------------------

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
        enq_to_cb_ms  = (callback_ns - probe["enqueue_ns"])  / 1_000_000.0
        rcpt_to_cb_ms = (callback_ns - probe["receipt_ns"])  / 1_000_000.0
        rcpt_to_dac_ms = (
            None if cb_to_dac_ms is None else rcpt_to_cb_ms + cb_to_dac_ms
        )
        msg = (
            f"key={probe['key']} event={probe['event_value']} "
            f"kernel->receipt={format_ms(probe['kernel_to_receipt_ms'])} "
            f"receipt->enqueue={format_ms(probe['receipt_to_enqueue_ms'])} "
            f"enqueue->callback={enq_to_cb_ms:.2f}ms "
            f"receipt->callback={rcpt_to_cb_ms:.2f}ms "
            f"callback->dac={format_ms(cb_to_dac_ms)} "
            f"receipt->estimated_dac={format_ms(rcpt_to_dac_ms)} "
            f"frames={frames}"
        )
        with self.latency_lock:
            self.latency_events.append(msg)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        with self.lock:
            self.enabled = enabled

    def set_volume(self, volume: float) -> None:
        with self.lock:
            self.volume = clamp(volume, 0.0, 1.0)

    def play(
        self,
        evdev_name: str,
        event_value: int,
        receipt_ns: int | None = None,
        kernel_to_receipt_ms: float | None = None,
    ) -> None:
        # Filter by event type first — cheapest check.
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
                    # Drop oldest clips when the polyphony limit is hit.
                    self.active = (
                        [] if self.max_polyphony == 1
                        else self.active[-(self.max_polyphony - 1):]
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
            for msg in pending:
                self.logger.deep(msg)
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _state_watcher(self) -> None:
        """Poll the state file and sync volume/enabled when it changes."""
        interval = max(0.05, self.state_sync_interval)
        while not self.stop_event.wait(interval):
            self.sync_from_state()

    def _latency_reporter(self) -> None:
        """Drain the latency probe queue to the logger (--deep-debug only)."""
        while not self.stop_event.wait(0.05):
            pending = []
            with self.latency_lock:
                while self.latency_events:
                    pending.append(self.latency_events.popleft())
            for msg in pending:
                self.logger.deep(msg)

    def sync_from_state(self) -> None:
        """Re-read the runtime state file if it has changed on disk."""
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
