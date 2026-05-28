# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

```bash
# Normal start (uses free-threaded Python for lowest audio latency)
./run.sh

# With verbose logging
./run.sh --debug

# With per-key latency diagnostics (first 100 keystrokes)
./run.sh --deep-debug

# Standard Python fallback (if free-threaded build unavailable)
uv run bgmusic.py
```

`run.sh` passes `PYTHON_GIL=0` and uses `cpython-3.14t` (free-threaded). The `.python-version` file pins `3.14t` so plain `uv run` also picks it up.

## Dev environment setup

```bash
# First time (or after switching Python versions)
uv venv --python 3.14t
uv pip install -r requirements.txt

# Verify GIL is off and all deps load
PYTHON_GIL=0 uv run python -c "import sys, yaml, numpy, sounddevice, soundfile, evdev, pulsectl; print('GIL:', sys._is_gil_enabled())"
```

No build step, no test suite. The single entry point is `bgmusic.py`.

## Architecture

Everything lives in `bgmusic.py`. There is no package structure. Key classes:

**`KeyboardSoundPlayer`** – owns the sounddevice `OutputStream` and the in-process mixer. At init it slices all clips from `assets/sound.ogg` using `assets/config.json` timings, optionally trims leading silence from each clip, and pre-builds an evdev-code → numpy-array lookup table (`evdev_clips`). The PortAudio callback (`_callback`) runs in a native C thread; it sums active clip slices into the output buffer each call. A background `_state_watcher` thread polls `STATE_FILE` for volume/enabled changes made by control commands.

**`KeyboardMonitor`** – reads raw evdev events via `select.select` in a daemon thread. For each `EV_KEY` event it calls `sound_player.play()` then `hotkey_manager.handle_key()`. evdev access requires the user to be in the `input` group.

**`HotkeyManager`** – pure Python, stateful. Tracks currently held keys and fires action callbacks on matching combos. No external dependencies.

**`handle_start`** – the daemon entry point. Sets up PipeWire quantum (`pw-metadata`), starts mpv subprocess, opens keyboard features, then runs the PulseAudio monitoring loop that auto-pauses mpv when external audio is detected.

**State persistence** – `STATE_FILE` (`/tmp/bgmusic_state.json`) is the IPC bridge. The daemon writes it; control sub-commands (`toggle`, `volume-up`, etc.) read/write it and send mpv IPC commands via `SOCKET_PATH` (`/tmp/mpv_bg_socket`). `KeyboardSoundPlayer._state_watcher` polls `STATE_FILE` on a configurable interval to pick up external changes.

## Audio latency architecture

The callback pipeline (all on one machine, low to high latency):
1. `kernel→receipt` (~0.1 ms) – evdev kernel timestamp to Python
2. `receipt→enqueue` (~0.05 ms) – Python before `play()`
3. `enqueue→callback` (0–5 ms) – wait for next PortAudio callback cycle; dominated by PipeWire graph quantum (`pipewire_quantum` in config, set via `clock.force-quantum`)
4. PortAudio ring buffer (`latency` config, ~1–3 ms)
5. PipeWire pipeline (~2–4 ms at quantum=32)

`callback→dac` reported by `--deep-debug` (~155 ms) is a **measurement artifact** from `snd_pcm_delay()` on pipewire-alsa virtual devices — ignore it. Real downstream latency is the sum of steps 4–5.

Key tuning levers (in `config.yaml`):
- `pipewire_quantum` – frames at 48 kHz; sets `clock.force-quantum` on start, restores on exit. 32 = ~0.67 ms/cycle (lowest latency).
- `latency` – PortAudio ring buffer size in seconds. Minimum stable = 2 × quantum period.
- `blocksize` – PortAudio frames per callback. Keep ≤ `pipewire_quantum × 44100/48000`.

## Config and state files

| File | Purpose |
|---|---|
| `config.yaml` | Static settings; read once at startup |
| `/tmp/bgmusic_state.json` | Runtime state: `manual_pause`, `loop`, `keyboard_sounds_enabled`, `keyboard_volume` |
| `/tmp/mpv_bg_socket` | Unix socket for mpv IPC |
| `.python-version` | Pins `3.14t` for uv |

## Non-obvious constraints

- The evdev `_input` C extension re-enables the GIL when loaded; `PYTHON_GIL=0` in `run.sh` forces it back off. Safe because evdev is only ever accessed from one thread.
- `prime_output_buffers_using_stream_callback=True` on the PortAudio stream pre-warms the output buffer before playback starts — do not remove it.
- The PipeWire quantum is restored to 0 (system default) in the cleanup handler. If the process is `kill -9`'d, the quantum stays set until PipeWire restarts.
- mpv is started with `--audio-client-name=My_Background_Music` so the PulseAudio sink watcher can reliably exclude it from the "external audio" check.
