"""Shortcut help modal."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class HelpScreen(ModalScreen):
    """Shortcut-reference overlay, dismissed with Esc / q / ?."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("?", "dismiss", "Close"),
    ]

    def __init__(self, hotkeys: list[tuple[str, str]]) -> None:
        super().__init__()
        self._hotkeys = hotkeys

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label("  Keyboard Shortcuts", id="help-title")
            body = Text()
            for combo, label in self._hotkeys:
                body.append(f"  {combo:<24}", style="cyan")
                body.append(f"  {label}\n", style="dim")
            yield Static(body, id="help-body")
            yield Label("  [dim]Esc / q / ?  close[/]", id="help-footer", markup=True)
