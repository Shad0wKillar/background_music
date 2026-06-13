"""Key-mapping tables and hotkey parsing.

Converts between three key-name spaces:
  - Config strings (e.g. "super+]")
  - Hotkey tokens (e.g. "meta", "]")  used by HotkeyManager
  - Browser key codes (e.g. "BracketRight")  used by the soundpack JSON
  - evdev key names (e.g. "KEY_RIGHTBRACE")  from the Linux input subsystem

evdev_to_hotkey_token and evdev_to_browser_code are on the hot path for
every keypress, so they use plain dict lookups and early returns.
"""
from __future__ import annotations

from typing import Any

try:
    import evdev
except ImportError:
    evdev = None  # type: ignore[assignment]

from bgmusic.constants import HOTKEY_ACTION_LABELS

# Maps every config alias to one of the four canonical tokens.
MODIFIER_ALIASES: dict[str, str] = {
    "alt": "alt", "option": "alt",
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "meta": "meta", "super": "meta", "win": "meta",
    "windows": "meta", "cmd": "meta", "command": "meta",
}
MODIFIER_TOKENS: frozenset[str] = frozenset({"alt", "ctrl", "shift", "meta"})

# Non-modifier key aliases used in hotkey strings.
KEY_TOKEN_ALIASES: dict[str, str] = {
    "esc": "escape", "return": "enter",
    "bracketleft": "[", "leftbracket": "[", "openbracket": "[",
    "bracketright": "]", "rightbracket": "]", "closebracket": "]",
    "spacebar": "space",
    "minus": "-", "equal": "=", "equals": "=",
    "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right",
    "pageup": "pageup", "pagedown": "pagedown",
}

# Maps evdev key names to hotkey tokens.
EVDEV_HOTKEY_TOKENS: dict[str, str] = {
    "KEY_LEFTALT": "alt",   "KEY_RIGHTALT": "alt",
    "KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl",
    "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift",
    "KEY_LEFTMETA": "meta", "KEY_RIGHTMETA": "meta",
    "KEY_SPACE": "space",   "KEY_ENTER": "enter",  "KEY_KPENTER": "enter",
    "KEY_ESC": "escape",    "KEY_BACKSPACE": "backspace",
    "KEY_TAB": "tab",       "KEY_CAPSLOCK": "capslock",
    "KEY_LEFTBRACE": "[",   "KEY_RIGHTBRACE": "]",
    "KEY_MINUS": "-",       "KEY_EQUAL": "=",
    "KEY_GRAVE": "`",       "KEY_BACKSLASH": "\\",
    "KEY_SEMICOLON": ";",   "KEY_APOSTROPHE": "'",
    "KEY_COMMA": ",",       "KEY_DOT": ".",         "KEY_SLASH": "/",
    "KEY_UP": "up",         "KEY_DOWN": "down",
    "KEY_LEFT": "left",     "KEY_RIGHT": "right",
    "KEY_INSERT": "insert", "KEY_HOME": "home",
    "KEY_PAGEUP": "pageup", "KEY_DELETE": "delete",
    "KEY_END": "end",       "KEY_PAGEDOWN": "pagedown",
}

# Maps evdev key names to the W3C browser KeyboardEvent.code values used
# by the MechvibesDX soundpack JSON.
EVDEV_BROWSER_CODES: dict[str, str] = {
    "KEY_ESC": "Escape",       "KEY_SPACE": "Space",
    "KEY_ENTER": "Enter",      "KEY_BACKSPACE": "Backspace",
    "KEY_TAB": "Tab",          "KEY_CAPSLOCK": "CapsLock",
    "KEY_LEFTBRACE": "BracketLeft", "KEY_RIGHTBRACE": "BracketRight",
    "KEY_MINUS": "Minus",      "KEY_EQUAL": "Equal",
    "KEY_GRAVE": "Backquote",  "KEY_BACKSLASH": "Backslash",
    "KEY_SEMICOLON": "Semicolon", "KEY_APOSTROPHE": "Quote",
    "KEY_COMMA": "Comma",      "KEY_DOT": "Period",   "KEY_SLASH": "Slash",
    "KEY_LEFTSHIFT": "ShiftLeft",  "KEY_RIGHTSHIFT": "ShiftRight",
    "KEY_LEFTCTRL": "ControlLeft", "KEY_RIGHTCTRL": "ControlLeft",
    "KEY_LEFTALT": "AltLeft",  "KEY_RIGHTALT": "AltLeft",
    "KEY_UP": "ArrowUp",       "KEY_DOWN": "ArrowDown",
    "KEY_LEFT": "ArrowLeft",   "KEY_RIGHT": "ArrowRight",
    "KEY_INSERT": "Insert",    "KEY_HOME": "Home",
    "KEY_PAGEUP": "PageUp",    "KEY_DELETE": "Delete",
    "KEY_END": "End",          "KEY_PAGEDOWN": "PageDown",
    "KEY_NUMLOCK": "NumLock",
    "KEY_KPSLASH": "NumpadDivide",  "KEY_KPASTERISK": "NumpadMultiply",
    "KEY_KPMINUS": "NumpadSubtract", "KEY_KPPLUS": "NumpadAdd",
    "KEY_KPENTER": "NumpadEnter",   "KEY_KPDOT": "NumpadDecimal",
    "KEY_SYSRQ": "PrintScreen", "KEY_SCROLLLOCK": "ScrollLock",
    "KEY_PAUSE": "Pause",
}

def normalize_modifier(value: Any) -> str:
    """Convert a config 'super' value (e.g. "Alt") to a canonical token."""
    token = str(value).strip().lower()
    if token not in MODIFIER_ALIASES:
        raise RuntimeError(
            f"Unsupported super modifier '{value}'. "
            "Use Alt, Control, Shift, Meta, or Super."
        )
    return MODIFIER_ALIASES[token]


def normalize_hotkey_token(token: str, super_modifier: str) -> str:
    """Normalise one segment of a hotkey combo string to a canonical token."""
    normalized = token.strip().lower()
    if normalized == "super":
        return super_modifier
    if normalized in MODIFIER_ALIASES:
        return MODIFIER_ALIASES[normalized]
    if normalized in KEY_TOKEN_ALIASES:
        return KEY_TOKEN_ALIASES[normalized]
    # Strip "Key" / "Digit" prefixes that some users write in configs.
    if normalized.startswith("key") and len(normalized) == 4:
        return normalized[-1]
    if normalized.startswith("digit") and len(normalized) == 6:
        return normalized[-1]
    return normalized


def parse_hotkeys(
    config: dict[str, Any],
) -> list[tuple[frozenset[str], str, str]]:
    """Return a list of (key-set, action, original-combo-string) tuples."""
    hotkey_config = config.get("hotkeys", {})
    if not isinstance(hotkey_config, dict):
        print("Warning: hotkeys config must be a mapping; hotkeys disabled.")
        return []

    super_modifier = normalize_modifier(config.get("super", "Alt"))
    parsed = []
    for action, combo in hotkey_config.items():
        if action not in HOTKEY_ACTION_LABELS:
            print(f"Warning: unknown hotkey action '{action}' ignored.")
            continue
        if not isinstance(combo, str) or not combo.strip():
            print(f"Warning: hotkey for '{action}' must be a non-empty string.")
            continue
        tokens = [
            normalize_hotkey_token(part, super_modifier)
            for part in combo.split("+") if part.strip()
        ]
        if not tokens:
            print(f"Warning: hotkey for '{action}' has no usable keys.")
            continue
        parsed.append((frozenset(tokens), action, combo))
    return parsed

def evdev_code_name(event_code: int) -> str | None:
    """Return the KEY_* name for an evdev code, or None if evdev is missing."""
    if evdev is None:
        return None
    name = evdev.ecodes.KEY.get(event_code)
    if isinstance(name, (list, tuple)):
        return name[0]
    return name


def evdev_to_hotkey_token(name: str) -> str | None:
    """Map an evdev key name to a hotkey token, or None if unmapped."""
    if name in EVDEV_HOTKEY_TOKENS:
        return EVDEV_HOTKEY_TOKENS[name]
    if name.startswith("KEY_"):
        suffix = name[4:]
        if len(suffix) == 1 and suffix.isalpha():
            return suffix.lower()
        if len(suffix) == 1 and suffix.isdigit():
            return suffix
        if suffix.startswith("F") and suffix[1:].isdigit():
            return suffix.lower()
        if suffix.startswith("KP") and suffix[2:].isdigit():
            return f"numpad{suffix[2:]}"
    return None


def evdev_to_browser_code(name: str) -> str | None:
    """Map an evdev key name to a W3C browser code, or None if unmapped."""
    if name in EVDEV_BROWSER_CODES:
        return EVDEV_BROWSER_CODES[name]
    if name.startswith("KEY_"):
        suffix = name[4:]
        if len(suffix) == 1 and suffix.isalpha():
            return f"Key{suffix}"
        if len(suffix) == 1 and suffix.isdigit():
            return f"Digit{suffix}"
        if suffix.startswith("F") and suffix[1:].isdigit():
            return suffix
        if suffix.startswith("KP") and suffix[2:].isdigit():
            return f"Numpad{suffix[2:]}"
    return None
