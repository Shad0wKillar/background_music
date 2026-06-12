"""Now-playing panel widget."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from bgmusic.tui.helpers import bar, fmt_time


class NowPlayingPanel(Widget):
    """Track info, progress, badges, and volume indicators."""

    def compose(self) -> ComposeResult:
        yield Label("♪  —", id="np-track")
        yield Label("", id="np-track-num")
        yield Label("", id="np-progress")
        yield Label("", id="np-time")
        yield Label("", id="np-div1")
        yield Label("", id="np-status")
        yield Label("", id="np-div2")
        yield Label("", id="np-music-vol")
        yield Label("", id="np-kb")

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
        self._lbl("np-track-num").update(
            f"[dim]   Track {playlist_pos + 1} of {playlist_total}[/]"
            if playlist_total > 0 else ""
        )
        prog = pos / dur if dur > 0 else 0.0
        self._lbl("np-progress").update(f"[cyan]{bar(prog, 36)}[/]")
        self._lbl("np-time").update(f"[dim]   {fmt_time(pos)}  /  {fmt_time(dur or None)}[/]")
        self._lbl("np-div1").update("[dim]─" * 36 + "[/]")
        play_b = "[dim]⏸ PAUSED[/]" if paused else "[green]▶ PLAYING[/]"
        loop_b = "[bold cyan]⟳ LOOP[/]" if loop else "[dim]⟳ loop[/]"
        rep_b = "[bold cyan]↺ REPEAT[/]" if repeat else "[dim]↺ repeat[/]"
        self._lbl("np-status").update(f"{play_b}   {loop_b}   {rep_b}")
        self._lbl("np-div2").update("[dim]─" * 36 + "[/]")
        self._lbl("np-music-vol").update(
            f"[dim]Music[/]  [yellow]{bar(volume / 100, 16)}[/]  [yellow]{int(volume)}%[/]"
        )
        self._refresh_keyboard(kb_enabled, kb_volume)

    def _refresh_keyboard(self, enabled: bool, volume: float) -> None:
        ratio = max(0.0, min(1.0, volume))
        k_bar = bar(ratio, 16)
        if enabled:
            self._lbl("np-kb").update(
                f"[dim]Keys [/]  [cyan]{k_bar}[/]  [cyan]{int(ratio * 100)}%[/]"
                f"  [bold green]ON[/]"
            )
        else:
            self._lbl("np-kb").update(
                f"[dim]Keys   {k_bar}  {int(ratio * 100)}%[/]  [bold red]OFF[/]"
            )
