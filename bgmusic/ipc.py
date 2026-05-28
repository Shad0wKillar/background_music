"""mpv IPC helpers.

All communication with the running mpv process goes through the Unix socket
at SOCKET_PATH.  Every function here is fire-and-forget safe — if mpv is not
running the calls return None / silently do nothing.
"""
from __future__ import annotations

import json
import socket
from typing import Any

from bgmusic.constants import SOCKET_PATH


def send_ipc_command(command_dict: dict[str, Any]) -> Any:
    """Send a JSON command to mpv and return its response, or None on failure."""
    try:
        if not SOCKET_PATH.exists():
            return None
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(SOCKET_PATH))
            client.sendall(json.dumps(command_dict).encode("utf-8") + b"\n")
            response = client.recv(4096)
        if not response:
            return None
        return json.loads(response.decode("utf-8"))
    except Exception:
        return None


def get_mpv_property(name: str) -> Any:
    """Read a property from mpv; returns None if mpv is unreachable."""
    response = send_ipc_command({"command": ["get_property", name]})
    if isinstance(response, dict) and response.get("error") == "success":
        return response.get("data")
    return None


def set_mpv_pause(paused: bool) -> None:
    send_ipc_command({"command": ["set_property", "pause", paused]})


def set_mpv_loop(enabled: bool) -> None:
    value = "inf" if enabled else "no"
    send_ipc_command({"command": ["set_property", "loop-playlist", value]})
