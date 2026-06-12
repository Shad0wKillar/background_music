"""User-visible action functions.

Each function corresponds to one thing the user can trigger — either via a
hotkey (when the daemon is running) or via a CLI sub-command.  Functions that
accept a `store` argument write the new value to disk immediately when called
from a hotkey; the store parameter is None for CLI invocations (the daemon's
monitoring loop picks up changes from mpv within one poll cycle instead).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bgmusic.config import clamp, float_setting, string_list_setting
from bgmusic.audio import audio_source_label
from bgmusic.ipc import get_mpv_property, send_ipc_command, set_mpv_loop, set_mpv_pause, set_mpv_repeat
from bgmusic.state import get_state, update_state

if TYPE_CHECKING:
    from bgmusic.sound_player import KeyboardSoundPlayer
    from bgmusic.state import SettingsStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def volume_step(config: dict[str, Any]) -> float:
    return float_setting(config["music"].get("volume_step"), 5.0)


def keyboard_volume_step(config: dict[str, Any]) -> float:
    return float_setting(config["keyboard_sounds"].get("volume_step"), 0.05)


# ---------------------------------------------------------------------------
# Music playback
# ---------------------------------------------------------------------------

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


def toggle_loop(config: dict[str, Any], store: SettingsStore | None = None) -> None:
    current = get_state(config)
    enabled = not current["loop"]
    update_state(config, loop=enabled)
    set_mpv_loop(enabled)
    print(f"Playlist loop: {enabled}")
    if store is not None:
        store.set("loop", enabled)


def toggle_repeat(config: dict[str, Any], store: SettingsStore | None = None) -> None:
    current = get_state(config)
    enabled = not current["repeat"]
    update_state(config, repeat=enabled)
    set_mpv_repeat(enabled)
    print(f"Song repeat: {enabled}")
    if store is not None:
        store.set("repeat", enabled)


# ---------------------------------------------------------------------------
# Music volume
# ---------------------------------------------------------------------------

def adjust_volume(delta: float, store: SettingsStore | None = None) -> None:
    """Add delta to the current mpv volume, clamped to 0–100 %."""
    fallback = store.get("music_volume", 100.0) if store is not None else 100.0
    current = get_mpv_property("volume")
    current_vol = clamp(float_setting(current, fallback), 0.0, 100.0)
    new_vol = clamp(current_vol + delta, 0.0, 100.0)
    send_ipc_command({"command": ["set_property", "volume", new_vol]})
    print(f"Music volume: {new_vol:.0f}%")
    if store is not None:
        store.set("music_volume", new_vol)


def volume_up(config: dict[str, Any], store: SettingsStore | None = None) -> None:
    adjust_volume(volume_step(config), store)


def volume_down(config: dict[str, Any], store: SettingsStore | None = None) -> None:
    adjust_volume(-volume_step(config), store)


def toggle_mute() -> None:
    send_ipc_command({"command": ["cycle", "mute"]})
    print("Mute toggled")


# ---------------------------------------------------------------------------
# Keyboard sounds
# ---------------------------------------------------------------------------

def toggle_keyboard_sounds(
    config: dict[str, Any],
    sound_player: KeyboardSoundPlayer | None = None,
    store: SettingsStore | None = None,
) -> None:
    current = get_state(config)
    enabled = not current["keyboard_sounds_enabled"]
    update_state(config, keyboard_sounds_enabled=enabled)
    if sound_player is not None:
        sound_player.set_enabled(enabled)
    print(f"Keyboard sounds: {enabled}")
    if store is not None:
        store.set("keyboard_sounds_enabled", enabled)


def adjust_keyboard_volume(
    config: dict[str, Any],
    delta: float,
    sound_player: KeyboardSoundPlayer | None = None,
    store: SettingsStore | None = None,
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
    sound_player: KeyboardSoundPlayer | None = None,
    store: SettingsStore | None = None,
) -> None:
    adjust_keyboard_volume(config, keyboard_volume_step(config), sound_player, store)


def keyboard_volume_down(
    config: dict[str, Any],
    sound_player: KeyboardSoundPlayer | None = None,
    store: SettingsStore | None = None,
) -> None:
    adjust_keyboard_volume(config, -keyboard_volume_step(config), sound_player, store)


# ---------------------------------------------------------------------------
# Audio detection ignores
# ---------------------------------------------------------------------------

def toggle_audio_source_ignore(
    config: dict[str, Any],
    source_key: str,
    store: SettingsStore | None = None,
) -> bool:
    """Toggle a TUI-selected audio source in the runtime ignore list."""
    current = get_state(config)
    ignored = string_list_setting(current.get("ignored_audio_sources"))
    if source_key in ignored:
        ignored = [key for key in ignored if key != source_key]
        enabled = False
    else:
        ignored = [*ignored, source_key]
        enabled = True
    update_state(config, ignored_audio_sources=ignored)
    print(f"Audio source ignored: {audio_source_label(source_key)} = {enabled}")
    if store is not None:
        store.set("ignored_audio_sources", ignored)
    return enabled


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def list_audio_devices() -> None:
    """Print all output audio devices and their default latency values."""
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
