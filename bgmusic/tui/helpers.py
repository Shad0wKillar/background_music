"""Small TUI formatting helpers."""
from __future__ import annotations

from typing import Any

from bgmusic.constants import HOTKEY_ACTION_LABELS


def fmt_time(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    t = int(seconds)
    return f"{t // 60}:{t % 60:02d}"


def bar(ratio: float, width: int) -> str:
    n = round(max(0.0, min(1.0, ratio)) * width)
    return "█" * n + "░" * (width - n)


def resolve_hotkeys(config: dict[str, Any]) -> list[tuple[str, str]]:
    super_key = str(config.get("super", "Alt")).capitalize()
    result: list[tuple[str, str]] = []
    for action, combo in config.get("hotkeys", {}).items():
        if not isinstance(combo, str):
            continue
        resolved = combo.replace("super", super_key)
        label = HOTKEY_ACTION_LABELS.get(action) or action.replace("_", " ").title()
        result.append((resolved, label))
    result += [
        ("↑ / ↓", "Navigate playlist"),
        ("Enter", "Jump to selected track"),
        ("a", "Focus audio sources"),
        ("p", "Focus playlist"),
        ("Space", "Toggle selected source ignore"),
        ("?", "Toggle this help"),
        ("q", "Quit TUI (daemon keeps running)"),
    ]
    return result
