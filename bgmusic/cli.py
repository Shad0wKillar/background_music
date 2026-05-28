"""CLI: argument parser, control sub-commands, and the main() entry point."""
from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from bgmusic.actions import (
    adjust_volume,
    keyboard_volume_down, keyboard_volume_up,
    list_audio_devices,
    next_track, previous_track,
    toggle_keyboard_sounds, toggle_loop, toggle_mute, toggle_music, toggle_repeat,
    volume_down, volume_up,
)
from bgmusic.config import load_config
from bgmusic.constants import DEFAULT_CONFIG_PATH
from bgmusic.daemon import handle_start


def handle_control(args: argparse.Namespace) -> None:
    """Dispatch a CLI control command to the running daemon via IPC."""
    config = load_config(Path(args.config))
    action = args.action

    if action == "toggle":
        toggle_music(config)
    elif action == "next":
        next_track()
    elif action == "previous":
        previous_track()
    elif action == "loop":
        toggle_loop(config)
    elif action == "repeat":
        toggle_repeat(config)
    elif action == "keyboard-sounds":
        toggle_keyboard_sounds(config)
    elif action == "volume-up":
        volume_up(config)
    elif action == "volume-down":
        volume_down(config)
    elif action == "mute":
        toggle_mute()
    elif action == "keyboard-volume-up":
        keyboard_volume_up(config)
    elif action == "keyboard-volume-down":
        keyboard_volume_down(config)
    elif action == "audio-devices":
        list_audio_devices()
    elif action == "volume":
        try:
            value = float(args.value)
        except ValueError:
            print("Invalid volume number")
            return
        adjust_volume(value)


def _handle_start_with_tui(args: argparse.Namespace) -> None:
    """Start the daemon in a background thread, then run the Textual TUI."""
    from bgmusic.tui.app import BGMusicApp

    config = load_config(Path(args.config))
    stop_event = threading.Event()

    def _daemon_target() -> None:
        try:
            handle_start(args, stop_event=stop_event)
        except RuntimeError as exc:
            print(f"Daemon error: {exc}", file=sys.stderr)
        finally:
            stop_event.set()

    daemon_thread = threading.Thread(
        target=_daemon_target,
        daemon=True,
        name="bgmusic-daemon",
    )
    daemon_thread.start()

    app = BGMusicApp(config=config, stop_event=stop_event)
    signal.signal(signal.SIGTERM, lambda _s, _f: app.exit())

    app.run()

    stop_event.set()
    daemon_thread.join(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OperaGX-style background music manager",
        epilog="Run without an action to start the daemon.",
    )
    parser.add_argument(
        "-c", "--config",
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

    sub = parser.add_subparsers(dest="action")

    start_p = sub.add_parser("start", help="Start the music daemon")
    start_p.add_argument("--shuffle", action="store_true", default=None,
                         help="Shuffle the playlist for this run")
    start_p.add_argument("--no-tui", action="store_true", default=False,
                         help="Run the daemon headlessly without the TUI")
    start_p.add_argument("--debug",      action="store_true", default=argparse.SUPPRESS)
    start_p.add_argument("--deep-debug", action="store_true", default=argparse.SUPPRESS)

    sub.add_parser("toggle",              help="Toggle manual pause")
    sub.add_parser("next",                help="Skip to the next song")
    sub.add_parser("previous",            help="Skip to the previous song")
    sub.add_parser("loop",                help="Toggle playlist looping")
    sub.add_parser("repeat",              help="Toggle single-song repeat")
    sub.add_parser("keyboard-sounds",     help="Toggle keyboard click sounds")
    sub.add_parser("volume-up",           help="Increase music volume by volume_step")
    sub.add_parser("volume-down",         help="Decrease music volume by volume_step")
    sub.add_parser("mute",                help="Toggle mpv mute")
    sub.add_parser("keyboard-volume-up",  help="Increase keyboard click volume")
    sub.add_parser("keyboard-volume-down",help="Decrease keyboard click volume")
    sub.add_parser("audio-devices",       help="List available audio output devices")

    vol_p = sub.add_parser("volume", help="Adjust music volume by a relative amount")
    vol_p.add_argument("value", help="Amount to change, e.g. +10 or -5")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.action in {None, "start"}:
            if getattr(args, "no_tui", False):
                handle_start(args)
            else:
                _handle_start_with_tui(args)
        else:
            handle_control(args)
    except RuntimeError as error:
        print(f"Error: {error}")
        sys.exit(1)
