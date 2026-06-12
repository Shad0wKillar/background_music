"""Textual TUI for bgmusic."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView

from bgmusic.actions import toggle_audio_source_ignore
from bgmusic.audio import audio_sources_snapshot
from bgmusic.ipc import get_mpv_property, send_ipc_command
from bgmusic.state import get_state
from bgmusic.tui.help import HelpScreen
from bgmusic.tui.helpers import resolve_hotkeys
from bgmusic.tui.now_playing import NowPlayingPanel
from bgmusic.tui.source_list import rebuild_source_list, source_signature


class BGMusicApp(App):
    TITLE = "♪ Background Music"
    CSS_PATH = Path(__file__).parent / "styles.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit TUI"),
        Binding("?", "help", "Help"),
        Binding("a", "focus_sources", "Audio Sources"),
        Binding("p", "focus_playlist", "Playlist"),
        Binding("space", "toggle_audio_ignore", "Toggle Ignore"),
    ]

    def __init__(
        self,
        config: dict[str, Any],
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._stop_event = stop_event
        self._hotkeys = resolve_hotkeys(config)
        self._last_track: str | None = ""
        self._last_pl_len = -1
        self._audio_sources: list[dict[str, Any]] = []
        self._last_source_signature: tuple[tuple[Any, ...], ...] = ()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield NowPlayingPanel(id="now-playing")
            with Vertical(id="right-panel"):
                yield Label("  Playlist", id="playlist-header")
                yield ListView(id="playlist")
                yield Label("  Audio Sources   [a focus] [Space toggle ignore]", id="sources-header")
                yield ListView(id="audio-sources")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#playlist", ListView).focus()
        self.set_interval(0.5, self._poll)

    async def _poll(self) -> None:
        if self._stop_event and self._stop_event.is_set():
            self.exit(return_code=1)
            return
        state = get_state(self._config)
        pl_raw = get_mpv_property("playlist") or []
        track = get_mpv_property("path")
        playlist_pos = next(
            (i for i, e in enumerate(pl_raw) if e.get("current") or e.get("playing")),
            0,
        )
        self.query_one(NowPlayingPanel).refresh_state(
            str(track) if track else "",
            float(get_mpv_property("time-pos") or 0),
            float(get_mpv_property("duration") or 0),
            bool(get_mpv_property("pause")),
            float(get_mpv_property("volume") or 100),
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
        await self._rebuild_audio_sources(state)

    async def _rebuild_playlist(self, pl_raw: list[dict]) -> None:
        view = self.query_one("#playlist", ListView)
        await view.clear()
        items: list[ListItem] = []
        playing_idx = 0
        for i, entry in enumerate(pl_raw):
            name = Path(entry.get("filename", f"Track {i + 1}")).stem
            is_cur = bool(entry.get("current") or entry.get("playing"))
            item = ListItem(Label(f"{'▶ ' if is_cur else '   '}{i + 1:>3}.  {name}"))
            if is_cur:
                item.add_class("playing")
                playing_idx = i
            items.append(item)
        if items:
            await view.mount(*items)
            view.index = playing_idx

    async def _rebuild_audio_sources(self, state: dict[str, Any]) -> None:
        try:
            sources = audio_sources_snapshot(self._config, state)
        except Exception as error:
            sources = [{"key": "error:pulse", "label": f"Could not list audio sources: {error}",
                        "active": False, "ignored": False, "ignored_reason": "",
                        "protected": True, "sink_count": 0}]
        signature = source_signature(sources)
        if signature == self._last_source_signature:
            return
        view = self.query_one("#audio-sources", ListView)
        await rebuild_source_list(view, self._audio_sources, sources)
        self._audio_sources = sources
        self._last_source_signature = signature

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "playlist":
            send_ipc_command({"command": ["set_property", "playlist-pos", event.index]})
        elif event.list_view.id == "audio-sources":
            self.action_toggle_audio_ignore()

    def action_help(self) -> None:
        self.push_screen(HelpScreen(self._hotkeys))

    def action_focus_sources(self) -> None:
        self.query_one("#audio-sources", ListView).focus()

    def action_focus_playlist(self) -> None:
        self.query_one("#playlist", ListView).focus()

    def action_toggle_audio_ignore(self) -> None:
        focused = self.focused
        if not isinstance(focused, ListView) or focused.id != "audio-sources":
            return
        index = focused.index if isinstance(focused.index, int) else -1
        if index < 0 or index >= len(self._audio_sources):
            return
        source = self._audio_sources[index]
        if source.get("protected"):
            return
        toggle_audio_source_ignore(self._config, str(source["key"]))
        self._last_source_signature = ()

    def action_quit(self) -> None:
        self.exit()
