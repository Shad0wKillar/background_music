"""Main daemon: starts mpv, keyboard features, and the monitoring loop.

handle_start() is the core entry point.  Private helpers are extracted to
keep it readable — each helper does one well-defined job.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from bgmusic.audio import (
    AudioDebugState, ensure_start_dependencies,
    other_audio_is_playing, snapshot_sink_indexes,
)
from bgmusic.config import bool_setting, load_config, resolve_project_path
from bgmusic.constants import CHECK_INTERVAL, SETTINGS_FILE, SOCKET_PATH, STATE_FILE
from bgmusic.daemon_helpers import (
    initial_runtime_state, persist_live_settings, restore_from_settings,
    snapshot_final_state, wait_for_mpv_socket,
)
from bgmusic.debug import DebugLogger
from bgmusic.hotkeys import start_keyboard_features
from bgmusic.ipc import send_ipc_command, set_mpv_pause
from bgmusic.music import (
    MusicDebugTracker, build_mpv_command, configured_music_extensions,
    discover_music_files, setup_pipewire_quantum, _apply_pipewire_force_quantum,
)
from bgmusic.state import SettingsStore, get_state, set_state

try:
    import pulsectl
except ImportError:
    pulsectl = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# handle_start — daemon entry point
# ---------------------------------------------------------------------------

def handle_start(args: Any, stop_event: threading.Event | None = None) -> None:
    # Free-threaded Python (PYTHON_GIL=0) has no GIL at all, which is the
    # best case.  On standard Python we shorten the GIL yield interval to
    # 1 ms so the PortAudio C callback thread isn't blocked for long.
    if getattr(sys, "_is_gil_enabled", lambda: True)():
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

    # Reduce PipeWire's graph quantum for lower audio callback latency.
    pw_applied, pw_original, _ = setup_pipewire_quantum(config, logger)

    # Load user settings (volume, loop, last track, …).
    # manual_pause is NOT restored — daemon always starts playing.
    store = SettingsStore.load(SETTINGS_FILE, config)
    logger.log(
        f"settings: loop={store.get('loop')} "
        f"repeat={store.get('repeat', False)} "
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
    for i, p in enumerate(music_files, 1):
        logger.log(f"  {i}. {p}")

    loop_enabled = store.get("loop")
    repeat_enabled = store.get("repeat", False)
    shuffle = bool_setting(config["music"].get("shuffle"), False)
    if getattr(args, "shuffle", None):
        shuffle = True

    # Write initial state so keyboard monitor can read it immediately.
    set_state(initial_runtime_state(store))
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    command = build_mpv_command(music_files, loop_enabled, shuffle, repeat=repeat_enabled)
    print(f"Starting mpv with {len(music_files)} track(s) (shuffle: {shuffle}, loop: {loop_enabled}, repeat: {repeat_enabled})...")
    try:
        mpv_process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as error:
        raise RuntimeError("mpv was not found. Install mpv and try again.") from error

    ignored_audio_pids = {str(os.getpid()), str(mpv_process.pid)}
    logger.log(f"ignoring audio process ids: python={os.getpid()}, mpv={mpv_process.pid}")

    wait_for_mpv_socket(logger)
    restore_from_settings(store, music_files, loop_enabled, repeat_enabled, logger)

    pre_sink_indexes = snapshot_sink_indexes(logger)
    sound_player, keyboard_monitor = start_keyboard_features(config, logger, store)
    time.sleep(0.25)
    post_sink_indexes = snapshot_sink_indexes(logger)
    ignored_sink_indexes = post_sink_indexes - pre_sink_indexes
    if ignored_sink_indexes:
        logger.log(f"ignoring startup-created sink indexes: {sorted(ignored_sink_indexes)}")

    cleaned_up = False

    def cleanup(signum: int | None = None, _frame: Any = None) -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        print("\nStopping background music...")

        # Snapshot before killing anything — store.set writes immediately.
        snapshot_final_state(store, config)

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

        if pw_applied:
            restore_to = pw_original if pw_original is not None else 0
            _apply_pipewire_force_quantum(restore_to)
            logger.log(f"PipeWire quantum restored to {restore_to if restore_to else 'default (unset)'}")

        if signum is not None and stop_event is None:
            sys.exit(0)

    if stop_event is None:
        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

    _known_track: str | None = store.get("last_track")
    print("Monitoring started.")
    audio_debug = AudioDebugState()
    music_debug = MusicDebugTracker(logger)

    try:
        with pulsectl.Pulse("bg-music-monitor") as pulse:
            while stop_event is None or not stop_event.is_set():
                try:
                    state = get_state(config)
                    if state["manual_pause"]:
                        set_mpv_pause(True)
                        music_debug.tick("manual pause")
                        time.sleep(CHECK_INTERVAL)
                        continue

                    external_playing, external_trigger = other_audio_is_playing(
                        pulse, config, state, ignored_audio_pids, ignored_sink_indexes, logger, audio_debug,
                    )
                    if external_trigger != audio_debug.external_trigger:
                        audio_debug.external_trigger = external_trigger
                        if external_trigger:
                            logger.log(f"external audio detected; pausing music: {external_trigger}")
                        else:
                            logger.log("external audio cleared; resuming music")

                    set_mpv_pause(external_playing)
                    reason = (
                        f"external audio ({external_trigger})"
                        if external_playing and external_trigger
                        else "no external audio"
                    )
                    music_debug.tick(reason)

                    _known_track = persist_live_settings(store, state, _known_track)

                    time.sleep(CHECK_INTERVAL)
                except pulsectl.PulseError:
                    logger.log("PulseAudio connection error; retrying")
                    time.sleep(2)
                except Exception as error:
                    raise RuntimeError(f"Monitor loop failed: {error}") from error
    finally:
        cleanup()
