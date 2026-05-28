#!/usr/bin/env bash
# Launch bgmusic with free-threaded Python and GIL forced off.
# PYTHON_GIL=0 keeps the GIL disabled even when evdev asks to re-enable it.
# evdev is safe without the GIL here because it's only used from one thread.
exec env PYTHON_GIL=0 uv run bgmusic.py "$@"
