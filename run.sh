#!/usr/bin/env bash
# Prepare a fresh checkout, then launch with free-threaded Python.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(uname -s)" != Linux ]]; then
    echo "bgmusic requires Linux with PulseAudio or PipeWire-Pulse." >&2
    exit 1
fi
if (( EUID == 0 )); then
    echo "Run ./run.sh as your normal user. Setup uses sudo only where needed." >&2
    exit 1
fi

uv_bin="$(command -v uv || true)"
if [[ -z "$uv_bin" && -x "$HOME/.local/bin/uv" ]]; then
    uv_bin="$HOME/.local/bin/uv"
fi
if [[ -z "$uv_bin" ]]; then
    echo "Installing uv into $HOME/.local/bin (without changing shell profiles)..."
    installer="$(mktemp)"
    trap 'rm -f -- "$installer"' EXIT
    if command -v curl >/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh -o "$installer"
    elif command -v wget >/dev/null; then
        wget -q https://astral.sh/uv/install.sh -O "$installer"
    else
        echo "Install curl or wget, then run ./run.sh again." >&2
        exit 1
    fi
    env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$installer"
    rm -f -- "$installer"
    trap - EXIT
    uv_bin="$HOME/.local/bin/uv"
fi

if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    "$uv_bin" venv --python "$(cat "$project_dir/.python-version")" "$project_dir/.venv"
fi

# Force the GIL off even when evdev asks to re-enable it.
exec env PYTHON_GIL=0 "$project_dir/.venv/bin/python" -B \
    "$project_dir/bgmusic/bootstrap.py" "$uv_bin" "$@"
