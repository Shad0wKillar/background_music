"""Global hotkey combo tracking."""
from __future__ import annotations

from typing import Any

from bgmusic.keymaps import MODIFIER_TOKENS, evdev_to_hotkey_token, parse_hotkeys


class HotkeyManager:
    """Tracks held keys and fires matching callbacks."""

    def __init__(self, config: dict[str, Any], callbacks: dict[str, Any]) -> None:
        self.hotkeys = parse_hotkeys(config)
        self.callbacks = callbacks
        self.active_tokens: set[str] = set()
        self.triggered_combos: set[frozenset[str]] = set()

    def handle_key(self, evdev_name: str, event_value: int) -> None:
        token = evdev_to_hotkey_token(evdev_name)
        if token is None:
            return
        if event_value == 0:
            self.active_tokens.discard(token)
            self.triggered_combos = {
                c for c in self.triggered_combos if c.issubset(self.active_tokens)
            }
            return
        if event_value == 2:
            return

        self.active_tokens.add(token)
        active_modifiers = self.active_tokens & MODIFIER_TOKENS
        for combo, action, label in self.hotkeys:
            combo_modifiers = combo & MODIFIER_TOKENS
            if (
                token in combo
                and combo.issubset(self.active_tokens)
                and combo_modifiers == active_modifiers
                and combo not in self.triggered_combos
            ):
                self.triggered_combos.add(combo)
                callback = self.callbacks.get(action)
                if callback is None:
                    continue
                try:
                    callback()
                except Exception as error:
                    print(f"Hotkey '{label}' failed: {error}")
