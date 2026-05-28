"""Config loading and primitive value helpers.

load_config() deep-merges config.yaml on top of DEFAULT_CONFIG.
The helper functions (bool_setting, float_setting, …) are used
everywhere a config value might be missing or have the wrong type.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from bgmusic.constants import DEFAULT_CONFIG, PROJECT_DIR


def load_config(config_path: Path) -> dict[str, Any]:
    """Return the merged config; falls back to built-in defaults on missing file."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    if not config_path.exists():
        print(f"Config not found at {config_path}; using built-in defaults.")
        return config

    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read config.yaml. "
            "Install dependencies with: uv pip install -r requirements.txt"
        )

    with config_path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise RuntimeError(f"Config file must contain a YAML mapping: {config_path}")

    merge_dict(config, loaded)
    return config


def merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Recursively merge overrides into base in-place."""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_dict(base[key], value)
        else:
            base[key] = value


def resolve_project_path(value: Any) -> Path:
    """Expand ~ and resolve relative paths against PROJECT_DIR."""
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def bool_setting(value: Any, default: bool) -> bool:
    """Coerce value to bool; return default on unrecognised input."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return default


def float_setting(value: Any, default: float) -> float:
    """Coerce value to float; return default on error."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def int_setting(value: Any, default: int) -> int:
    """Coerce value to int; return default on error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def string_list_setting(value: Any) -> list[str]:
    """Return a cleaned list of non-empty strings; empty list on bad input."""
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
