"""Textual TUI for bgmusic.

Two-column layout inspired by musikcube/cmus:
  Left panel  — now-playing, progress, status badges, volume indicators
  Right panel — full-height scrollable playlist

The daemon's evdev hotkeys (Alt+p, Alt+l …) remain active at all times —
the TUI is a display layer and does not intercept them.

TUI-only controls:
  ↑ ↓      navigate the playlist
  Enter    jump to the highlighted track
  ?        show / hide the shortcut reference
  q        quit the TUI (daemon keeps running)
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from bgmusic.constants import HOTKEY_ACTION_LABELS
from bgmusic.ipc import get_mpv_property, send_ipc_command
from bgmusic.state import get_state


# ── Helpers ───────────────────────────────────────────────────────────────

def _fmt_time(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    t = int(seconds)
    return f"{t // 60}:{t % 60:02d}"


def _bar(ratio: float, width: int) -> str:
    n = round(max(0.0, min(1.0, ratio)) * width)
    return "█" * n + "░" * (width - n)


def _resolve_hotkeys(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Return [(display_combo, label)] with 'super' expanded to the real modifier."""
    super_key = str(config.get("super", "Alt")).capitalize()
    result: list[tuple[str, str]] = []
    for action, combo in config.get("hotkeys", {}).items():
        if not isinstance(combo, str):
            continue
        resolved = combo.replace("super", super_key)
        label = HOTKEY_ACTION_LABELS.get(action) or action.replace("_", " ").title()
        result.append((resolved, label))
    result += [
        ("↑ / ↓",  "Navigate playlist"),
        ("Enter",   "Jump to selected track"),
        ("?",       "Toggle this help"),
        ("q",       "Quit TUI (daemon keeps running)"),
    ]
    return result


# ── Help overlay ─────────────────────────────────────────────────────────

class HelpScreen(ModalScreen):
    """Shortcut-reference overlay, dismissed with Esc / q / ?."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q",      "dismiss", "Close"),
        Binding("?",      "dismiss", "Close"),
    ]

    def __init__(self, hotkeys: list[tuple[str, str]]) -> None:
        super().__init__()
        self._hotkeys = hotkeys

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label("  Keyboard Shortcuts", id="help-title")
            # Build with rich.Text so combo strings are treated as plain text —
            # no markup parsing, so [ ] never misfire as tags.
            COL = 24
            body = Text()
            for combo, label in self._hotkeys:
                body.append(f"  {combo:<{COL}}", style="cyan")
                body.append(f"  {label}\n",       style="dim")
            yield Static(body, id="help-body")
            yield Label(
                "  [dim]Esc / q / ?  close[/]",
                id="help-footer",
                markup=True,
            )


# ── Left panel ────────────────────────────────────────────────────────────

class NowPlayingPanel(Widget):
    """Track info, progress, badges, and volume indicators."""

    # ── Sub-widget IDs ──
    _IDS = (
        "np-track", "np-track-num",
        "np-progress", "np-time",
        "np-div1",
        "np-status",
        "np-div2",
        "np-music-vol",
        "np-kb",
    )

    def compose(self) -> ComposeResult:
        yield Label("♪  —",        id="np-track")
        yield Label("",            id="np-track-num")
        yield Label("",            id="np-progress")
        yield Label("",            id="np-time")
        yield Label("",            id="np-div1")
        yield Label("",            id="np-status")
        yield Label("",            id="np-div2")
        yield Label("",            id="np-music-vol")
        yield Label("",            id="np-kb")

    def _lbl(self, id_: str) -> Label:
        return self.query_one(f"#{id_}", Label)

    def refresh_state(
        self,
        track: str,
        pos: float,
        dur: float,
        paused: bool,
        volume: float,
        loop: bool,
        repeat: bool,
        kb_enabled: bool,
        kb_volume: float,
        playlist_pos: int,
        playlist_total: int,
    ) -> None:
        name = Path(track).stem if track else "—"
        self._lbl("np-track").update(f"[bold yellow]♪  {name}[/]")

        if playlist_total > 0:
            self._lbl("np-track-num").update(
                f"[dim]   Track {playlist_pos + 1} of {playlist_total}[/]"
            )
        else:
            self._lbl("np-track-num").update("")

        # Progress bar — 36 chars wide fits the 46-char left panel
        prog = pos / dur if dur > 0 else 0.0
        self._lbl("np-progress").update(f"[cyan]{_bar(prog, 36)}[/]")
        self._lbl("np-time").update(
            f"[dim]   {_fmt_time(pos)}  /  {_fmt_time(dur or None)}[/]"
        )

        self._lbl("np-div1").update(
            "[dim]─" * 36 + "[/]"
        )

        play_b = "[dim]⏸ PAUSED[/]" if paused else "[green]▶ PLAYING[/]"
        loop_b = "[bold cyan]⟳ LOOP[/]"   if loop   else "[dim]⟳ loop[/]"
        rep_b  = "[bold cyan]↺ REPEAT[/]" if repeat else "[dim]↺ repeat[/]"
        self._lbl("np-status").update(f"{play_b}   {loop_b}   {rep_b}")

        self._lbl("np-div2").update("[dim]─" * 36 + "[/]")

        m_bar = _bar(volume / 100, 16)
        self._lbl("np-music-vol").update(
            f"[dim]Music[/]  [yellow]{m_bar}[/]  [yellow]{int(volume)}%[/]"
        )

        k_ratio = max(0.0, min(1.0, kb_volume))
        k_bar   = _bar(k_ratio, 16)
        if kb_enabled:
            self._lbl("np-kb").update(
                f"[dim]Keys [/]  [cyan]{k_bar}[/]  [cyan]{int(k_ratio * 100)}%[/]"
                f"  [bold green]ON[/]"
            )
        else:
            self._lbl("np-kb").update(
                f"[dim]Keys   {k_bar}  {int(k_ratio * 100)}%[/]"
                f"  [bold red]OFF[/]"
            )


# ── Main application ──────────────────────────────────────────────────────

class BGMusicApp(App):
    TITLE = "♪ Background Music"
    CSS_PATH = Path(__file__).parent / "styles.tcss"

    BINDINGS = [
        Binding("q",   "quit",  "Quit TUI"),
        Binding("?",   "help",  "Help"),
    ]

    def __init__(
        self,
        config: dict[str, Any],
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._stop_event = stop_event
        self._hotkeys = _resolve_hotkeys(config)
        self._last_track: str | None = ""
        self._last_pl_len: int = -1

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield NowPlayingPanel(id="now-playing")
            with Vertical(id="right-panel"):
                yield Label("  Playlist", id="playlist-header")
                yield ListView(id="playlist")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.5, self._poll)

    # ── Polling ───────────────────────────────────────────────────────────

    async def _poll(self) -> None:
        if self._stop_event and self._stop_event.is_set():
            self.exit(return_code=1)
            return

        track   = get_mpv_property("path")
        pos     = float(get_mpv_property("time-pos") or 0)
        dur     = float(get_mpv_property("duration") or 0)
        paused  = bool(get_mpv_property("pause"))
        volume  = float(get_mpv_property("volume") or 100)
        pl_raw  = get_mpv_property("playlist") or []
        state   = get_state(self._config)

        # Derive current playlist position from mpv data
        playlist_pos = next(
            (i for i, e in enumerate(pl_raw)
             if e.get("current") or e.get("playing")),
            0,
        )

        self.query_one(NowPlayingPanel).refresh_state(
            str(track) if track else "",
            pos, dur, paused, volume,
            state.get("loop", False),
            state.get("repeat", False),
            bool(state.get("keyboard_sounds_enabled", True)),
            float(state.get("keyboard_volume", 1.0)),
            playlist_pos,
            len(pl_raw),
        )

        track_str = str(track) if track else None
        if len(pl_raw) != self._last_pl_len or track_str != self._last_track:
            self._last_track = track_str
            self._last_pl_len = len(pl_raw)
            await self._rebuild_playlist(pl_raw)

    async def _rebuild_playlist(self, pl_raw: list[dict]) -> None:
        lv = self.query_one("#playlist", ListView)
        await lv.clear()

        playing_idx = 0
        items: list[ListItem] = []
        for i, entry in enumerate(pl_raw):
            name   = Path(entry.get("filename", f"Track {i + 1}")).stem
            is_cur = bool(entry.get("current") or entry.get("playing"))
            prefix = "▶ " if is_cur else "   "
            li     = ListItem(Label(f"{prefix}{i + 1:>3}.  {name}"))
            if is_cur:
                li.add_class("playing")
                playing_idx = i
            items.append(li)

        if items:
            await lv.mount(*items)
            lv.index = playing_idx

    # ── Event handlers ────────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "playlist":
            send_ipc_command({"command": ["set_property", "playlist-pos", event.index]})

    def action_help(self) -> None:
        self.push_screen(HelpScreen(self._hotkeys))

    def action_quit(self) -> None:
        self.exit()
