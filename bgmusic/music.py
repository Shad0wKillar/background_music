"""Music discovery, mpv command builder, debug tracker, PipeWire quantum.

MusicDebugTracker  — logs track and pause-state changes (--debug only).
configured_music_extensions — normalises the extensions list from config.
discover_music_files        — recursive glob filtered to supported formats.
build_mpv_command           — builds the mpv argv list.
PipeWire quantum helpers    — read / write clock.force-quantum via pw-metadata.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from bgmusic.config import int_setting
from bgmusic.constants import (
    DEFAULT_MUSIC_EXTENSIONS, MY_APP_NAME, SOCKET_PATH,
)
from bgmusic.debug import DebugLogger
from bgmusic.ipc import get_mpv_property


# ---------------------------------------------------------------------------
# Debug tracker
# ---------------------------------------------------------------------------

class MusicDebugTracker:
    """Logs track changes and play/pause transitions (printed only in --debug)."""

    def __init__(self, logger: DebugLogger) -> None:
        self.logger = logger
        self.last_path: str | None = None
        self.last_pause: bool | None = None
        self.last_reason: str | None = None

    def tick(self, reason: str) -> None:
        if not self.logger.enabled:
            return
        path  = get_mpv_property("path")
        pause = get_mpv_property("pause")
        if path != self.last_path:
            self.last_path = path
            self.logger.log(f"track active: {Path(str(path)).name}" if path else "track active: none")
        if pause != self.last_pause or reason != self.last_reason:
            self.last_pause  = pause
            self.last_reason = reason
            state = "paused" if pause else "playing"
            self.logger.log(f"music state: {state}; reason={reason}")


# ---------------------------------------------------------------------------
# Music file discovery
# ---------------------------------------------------------------------------

def configured_music_extensions(config: dict[str, Any]) -> set[str]:
    """Return normalised file-extension set from config; falls back to defaults."""
    configured = config["music"].get("supported_extensions", DEFAULT_MUSIC_EXTENSIONS)
    if not isinstance(configured, list):
        print("Warning: music.supported_extensions must be a list; using defaults.")
        configured = DEFAULT_MUSIC_EXTENSIONS

    extensions: set[str] = set()
    for value in configured:
        if not isinstance(value, str) or not value.strip():
            continue
        ext = value.strip().lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        extensions.add(ext)
    return extensions or set(DEFAULT_MUSIC_EXTENSIONS)


def discover_music_files(music_dir: Path, extensions: set[str]) -> list[Path]:
    """Recursively find all supported audio files, sorted case-insensitively."""
    return sorted(
        (
            p for p in music_dir.rglob("*")
            if p.is_file()
            and p.suffix.lower() in extensions
            and not p.name.lower().endswith(".part")
        ),
        key=lambda p: str(p).lower(),
    )


# ---------------------------------------------------------------------------
# mpv command builder
# ---------------------------------------------------------------------------

def build_mpv_command(
    music_files: list[Path], loop_enabled: bool, shuffle: bool, repeat: bool = False
) -> list[str]:
    """Return the full mpv argv list for the given playlist and options."""
    command = [
        "mpv",
        "--no-video",
        f"--input-ipc-server={SOCKET_PATH}",
        "--idle=yes",
        f"--audio-client-name={MY_APP_NAME}",
        f"--loop-playlist={'inf' if loop_enabled else 'no'}",
        f"--loop-file={'inf' if repeat else 'no'}",
        "--volume-max=100",          # hard cap — matches our Python-side clamping
    ]
    if shuffle:
        command.append("--shuffle")
    command.extend(str(p) for p in music_files)
    return command


# ---------------------------------------------------------------------------
# PipeWire quantum management
# ---------------------------------------------------------------------------

def _get_pipewire_force_quantum() -> int | None:
    """Read clock.force-quantum from PipeWire's settings metadata.

    Returns the current value if > 0, or None if unset / zero.
    PipeWire stores this under settings metadata object ID 0.
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

    frames=0 removes the override (PipeWire negotiates freely).
    Returns True on success, False if pw-metadata is unavailable.
    """
    try:
        subprocess.run(
            ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(frames)],
            capture_output=True, text=True, timeout=2, check=True,
        )
        return True
    except (FileNotFoundError, Exception):
        return False


def setup_pipewire_quantum(
    config: dict[str, Any], logger: DebugLogger
) -> tuple[bool, int | None, int]:
    """Apply the configured pipewire_quantum; return (applied, original, target)."""
    target = int_setting(config["keyboard_sounds"].get("pipewire_quantum"), 256)
    if target <= 0:
        return False, None, target

    original = _get_pipewire_force_quantum()
    if _apply_pipewire_force_quantum(target):
        logger.log(
            f"PipeWire quantum set to {target} frames "
            f"(~{target / 48:.1f} ms at 48 kHz); "
            f"was {original if original else 'unset'}"
        )
        return True, original, target

    logger.log(
        "could not set PipeWire quantum "
        "(pw-metadata not found or failed — install pipewire package)"
    )
    return False, None, target
