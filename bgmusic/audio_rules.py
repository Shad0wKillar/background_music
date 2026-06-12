"""Configurable audio ignore rules."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bgmusic.audio_backend import matches_any_pattern, proplist_value
from bgmusic.config import bool_setting, string_list_setting


PROPERTY_KEYS = {
    "app": "application.name",
    "media": "media.name",
    "role": "media.role",
    "pid": "application.process.id",
}


def sink_property(sink: Any, key: str) -> str:
    if key == "binary":
        return Path(proplist_value(sink, "application.process.binary")).name
    if key in PROPERTY_KEYS:
        return proplist_value(sink, PROPERTY_KEYS[key])
    if key in {"corked", "playing"}:
        return str(bool(getattr(sink, "corked", False)))
    return ""


def condition_matches(sink: Any, condition: Any) -> bool:
    if not isinstance(condition, dict):
        return False
    for key, expected in condition.items():
        if key == "corked":
            if bool(getattr(sink, "corked", False)) != bool_setting(expected, False):
                return False
            continue
        if key == "playing":
            if (not bool(getattr(sink, "corked", False))) != bool_setting(expected, True):
                return False
            continue
        patterns = string_list_setting(expected) if isinstance(expected, list) else []
        if isinstance(expected, str):
            patterns = [expected]
        if not patterns or not matches_any_pattern(sink_property(sink, key), patterns):
            return False
    return True


def rule_conditions(rule: dict[str, Any], mode: str) -> list[Any]:
    value = rule.get(mode)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def rule_matches(sink: Any, rule: Any) -> bool:
    if not isinstance(rule, dict):
        return False
    all_conditions = rule_conditions(rule, "all")
    any_conditions = rule_conditions(rule, "any")

    if all_conditions and not all(condition_matches(sink, c) for c in all_conditions):
        return False
    if any_conditions and not any(condition_matches(sink, c) for c in any_conditions):
        return False
    if all_conditions or any_conditions:
        return True

    condition = {
        key: value
        for key, value in rule.items()
        if key in {*PROPERTY_KEYS, "binary", "corked", "playing"}
    }
    return condition_matches(sink, condition)


def ignore_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
    detection = config.get("audio_detection", {})
    if not isinstance(detection, dict):
        return []
    rules = detection.get("ignore_rules", [])
    return [rule for rule in rules if isinstance(rule, dict)]


def matching_rule_name(sink: Any, config: dict[str, Any]) -> str | None:
    for rule in ignore_rules(config):
        if rule_matches(sink, rule):
            name = rule.get("name")
            return str(name) if name else "rule"
    return None
