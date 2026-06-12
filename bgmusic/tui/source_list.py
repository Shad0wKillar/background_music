"""Audio-source list rendering."""
from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual.widgets import Label, ListItem, ListView


def source_signature(sources: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            source.get("key"),
            source.get("label"),
            source.get("active"),
            source.get("ignored"),
            source.get("ignored_reason"),
            source.get("protected"),
            source.get("sink_count"),
        )
        for source in sources
    )


def selected_source_key(view: ListView, sources: list[dict[str, Any]]) -> str | None:
    index = view.index if isinstance(view.index, int) else -1
    if 0 <= index < len(sources):
        return str(sources[index].get("key"))
    return None


def source_item(source: dict[str, Any]) -> ListItem:
    key = str(source.get("key", ""))
    label = escape(str(source.get("label") or key))
    active = bool(source.get("active"))
    ignored = bool(source.get("ignored"))
    protected = bool(source.get("protected"))
    count = int(source.get("sink_count") or 0)
    status = "[green]LIVE[/]" if active else "[dim]idle[/]"
    if ignored:
        reason = escape(str(source.get("ignored_reason") or "ignored"))
        marker = f"[bold cyan]IGNORED:{reason}[/]"
    else:
        marker = "[yellow]DETECT[/]"
    lock = " [dim]locked[/]" if protected else ""
    suffix = f" [dim]x{count}[/]" if count > 1 else ""
    item = ListItem(Label(f"  {marker:<28} {status}  {label}{suffix}{lock}", markup=True))
    if ignored:
        item.add_class("ignored-source")
    if protected:
        item.add_class("protected-source")
    return item


async def rebuild_source_list(
    view: ListView,
    old_sources: list[dict[str, Any]],
    new_sources: list[dict[str, Any]],
) -> None:
    selected_key = selected_source_key(view, old_sources)
    await view.clear()
    next_index = 0
    items: list[ListItem] = []
    for i, source in enumerate(new_sources):
        if str(source.get("key")) == selected_key:
            next_index = i
        items.append(source_item(source))
    if items:
        await view.mount(*items)
        view.index = next_index
