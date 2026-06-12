"""Keyboard soundpack loading and clip mapping."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bgmusic.keymaps import evdev_code_name, evdev_to_browser_code

try:
    import evdev
except ImportError:
    evdev = None  # type: ignore[assignment]


def trim_clip(
    np: Any, clip: Any, sample_rate: int,
    threshold_ratio: float, max_ms: float, preroll_ms: float,
) -> tuple[Any, float]:
    if len(clip) == 0:
        return clip, 0.0
    mono = np.max(np.abs(clip), axis=1)
    peak = float(np.max(mono))
    if peak <= 0:
        return clip, 0.0
    threshold = max(peak * max(0.0, threshold_ratio), 0.001)
    hits = np.flatnonzero(mono >= threshold)
    if len(hits) == 0:
        return clip, 0.0
    preroll = int(max(0.0, preroll_ms) / 1000.0 * sample_rate)
    max_trim = int(max(0.0, max_ms) / 1000.0 * sample_rate)
    trim = min(max(0, int(hits[0]) - preroll), max_trim)
    if trim <= 0:
        return clip, 0.0
    return clip[trim:].copy(), trim / sample_rate * 1000.0


def load_soundpack(
    soundpack_dir: Path,
    sf: Any,
    np: Any,
    trim_silence: bool,
    threshold_ratio: float,
    max_ms: float,
    preroll_ms: float,
    logger: Any,
) -> tuple[dict[tuple[str, int], Any], int, int]:
    with (soundpack_dir / "config.json").open("r", encoding="utf-8") as f:
        soundpack_config = json.load(f)
    audio_path = soundpack_dir / soundpack_config.get("audio_file", "sound.ogg")
    audio, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    clips: dict[tuple[str, int], Any] = {}
    for browser_code, definition in soundpack_config.get("definitions", {}).items():
        for index, timing in enumerate(definition.get("timing", [])[:2]):
            if not isinstance(timing, list) or len(timing) != 2:
                continue
            start = int((float(timing[0]) / 1000.0) * sample_rate)
            end = int((float(timing[1]) / 1000.0) * sample_rate)
            if not (0 <= start < end <= len(audio)):
                continue
            clip = audio[start:end].copy()
            if trim_silence:
                clip, trimmed_ms = trim_clip(np, clip, sample_rate, threshold_ratio, max_ms, preroll_ms)
                if logger.enabled and trimmed_ms > 0:
                    logger.log(f"trimmed {browser_code}[{index}] by {trimmed_ms:.2f}ms")
            clips[(browser_code, index)] = clip
    return clips, int(sample_rate), int(audio.shape[1])


def precompute_evdev_clips(clips: dict[tuple[str, int], Any]) -> dict[tuple[str, int], Any]:
    evdev_clips: dict[tuple[str, int], Any] = {}
    if evdev is None:
        return evdev_clips
    for code_value in evdev.ecodes.KEY:
        evdev_name = evdev_code_name(code_value)
        browser_code = evdev_to_browser_code(evdev_name) if evdev_name else None
        if browser_code is None:
            continue
        for event_value, timing_index in ((1, 0), (0, 1)):
            clip = clips.get((browser_code, timing_index))
            if clip is not None:
                evdev_clips[(evdev_name, event_value)] = clip
    return evdev_clips
