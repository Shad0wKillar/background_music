# Background Music Manager

An OperaGX-inspired background music daemon for Linux. It plays music from your local
`music/` folder, automatically pauses when another app starts playing audio, and
resumes when that audio stops. It also adds optional mechanical keyboard sounds
using the bundled CherryMX Blue soundpack in `assets/`.

## Features

- Background music through `mpv`.
- Automatic pause/resume when other PulseAudio or PipeWire-Pulse apps make sound.
- Keyboard click sounds are treated as internal audio, so they do not pause the
  background music.
- Clean playlist discovery for common audio/video-container formats, including
  `.mp3`, `.ogg`, `.flac`, `.m4a`, and `.webm`.
- Global keyboard shortcuts on Wayland/Linux through `evdev`.
- Mechanical keyboard click sounds for normal typing, with overlapping playback
  for fast typing.
- Separate volume controls for background music and keyboard click sounds, both
  hard-capped at 100 % to protect your hardware.
- **Persistent user settings**: keyboard volume, music volume, loop toggle,
  keyboard-sounds toggle, and the last playing track are all saved automatically.
  The next time you start the daemon, it picks up exactly where you left off.
- Editable YAML config for music paths, loop behavior, volumes, and hotkeys.
- One-line `super` modifier setting so all `super+...` shortcuts can move from
  `Alt` to another modifier.

---

## First-time Setup

### 1. System packages

Install these with your package manager before anything else.

**Arch Linux / Manjaro**
```bash
sudo pacman -S mpv python uv
```

**Ubuntu / Debian**
```bash
sudo apt install mpv python3 pipx
pipx install uv
```

You also need PulseAudio **or** PipeWire with the PulseAudio compatibility service
(`pipewire-pulse`). Most modern distros running GNOME, KDE, or Sway already ship
with PipeWire + pipewire-pulse.

### 2. Free-threaded Python 3.14t (recommended)

The daemon uses a free-threaded (no-GIL) Python build for lowest audio latency.
`uv` can download and manage this automatically:

```bash
uv python install cpython-3.14t
```

The `.python-version` file at the project root pins this version, so `uv` picks
it up automatically. If Python 3.14t is unavailable on your platform, the daemon
falls back to a standard Python build with a tuned GIL switch interval — latency
will be slightly higher but everything still works.

### 3. Python dependencies

```bash
cd bg_music
uv venv --python 3.14t   # creates .venv with free-threaded Python
uv pip install -r requirements.txt
```

If you skip `--python 3.14t`, uv uses whichever Python the `.python-version` file
requests (also `3.14t`).

### 4. Verify the install

```bash
PYTHON_GIL=0 uv run python -c "import sys, yaml, numpy, sounddevice, soundfile, evdev, pulsectl; print('GIL:', sys._is_gil_enabled())"
```

Expected output: `GIL: False`

### 5. Add music

Put audio files in the `music/` directory (create it if it doesn't exist).
Supported formats: `.mp3`, `.flac`, `.wav`, `.ogg`, `.opus`, `.m4a`, `.aac`,
`.webm`, `.mp4`, `.mkv`.

```bash
mkdir -p music
cp ~/Music/*.mp3 music/
```

### 6. Keyboard input permission

Global hotkeys and keyboard sounds use `evdev`, which reads `/dev/input/event*`
directly. Your user needs permission to do this.

The simplest approach on most distros:
```bash
sudo usermod -aG input "$USER"
```

Then **fully log out and log back in** (a new terminal is not enough). Verify with:
```bash
id   # should include "input" in the groups list
```

To test without logging out:
```bash
newgrp input
uv run bgmusic.py
```

If you add a udev rule instead, the app does not grab or block the keyboard — normal
typing continues to work. See `ls -l /dev/input/event*`; expected permissions are
`crw-rw----` with group `input`.

---

## Running the Daemon

### Recommended (lowest latency)

```bash
./run.sh
```

`run.sh` sets `PYTHON_GIL=0` and passes all arguments through to `bgmusic.py`. This
ensures the GIL stays off even when evdev asks to re-enable it.

### Alternative (standard Python fallback)

```bash
uv run bgmusic.py
```

### Debug modes

```bash
./run.sh --debug        # verbose playback and audio-detection logs
./run.sh --deep-debug   # per-key latency diagnostics for the first 100 keystrokes
./run.sh start --shuffle  # shuffle the playlist for this run only
```

---

## Persistent User Settings

Settings are saved automatically to `bgmusic_settings.json` inside the project
directory. The file is created on first run and updated in real time as you make
changes. It is listed in `.gitignore` so it is never committed.

The following are persisted across restarts:

| Setting | Description |
|---|---|
| `keyboard_volume` | Volume of keyboard click sounds (0–100 %) |
| `keyboard_sounds_enabled` | Whether keyboard sounds are on or off |
| `loop` | Whether playlist looping is enabled |
| `music_volume` | Background music volume (0–100 %) |
| `last_track` | Absolute path of the last playing track |

On the next start, the daemon restores all of these immediately — no 1-second
startup delay. `last_track` is matched against the current playlist; if the file
is still there, playback begins from that track. `manual_pause` is intentionally
**not** saved — the daemon always starts playing.

Settings are written to disk the instant anything changes (hotkey press, track
change, etc.) using an atomic temp-file rename, so nothing is lost even if the
process is killed with `kill -9`.

---

## Control Commands

Run these from a second terminal while the daemon is running:

```bash
uv run bgmusic.py toggle             # pause / resume
uv run bgmusic.py next               # skip to next track
uv run bgmusic.py previous           # go back one track
uv run bgmusic.py loop               # toggle playlist loop (saved automatically)
uv run bgmusic.py keyboard-sounds    # toggle keyboard click sounds (saved)
uv run bgmusic.py volume-up          # raise music volume by volume_step (default 5 %)
uv run bgmusic.py volume-down        # lower music volume
uv run bgmusic.py mute               # toggle mpv mute
uv run bgmusic.py keyboard-volume-up    # raise keyboard click volume (saved)
uv run bgmusic.py keyboard-volume-down  # lower keyboard click volume (saved)
uv run bgmusic.py volume +10         # relative volume change; capped at 100 %
uv run bgmusic.py volume -5
uv run bgmusic.py audio-devices      # list audio output devices
```

---

## Default Hotkeys

The default `config.yaml` maps `super` to `Alt`:

| Action | Hotkey |
|---|---|
| Toggle background music | `Alt+p` |
| Next track | `Alt+]` |
| Previous track | `Alt+[` |
| Toggle playlist loop | `Alt+l` |
| Toggle keyboard sounds | `Alt+m` |
| Music volume up | `Alt+=` |
| Music volume down | `Alt+-` |
| Music mute | `Alt+0` |
| Keyboard volume up | `Alt+Shift+=` |
| Keyboard volume down | `Alt+Shift+-` |

To change every shortcut to a different modifier, edit one line in `config.yaml`:

```yaml
super: Alt   # change to Control, Shift, Meta, or Super
```

---

## Configuration

`config.yaml` is read once at startup. Relative paths are resolved from the
project directory.

```yaml
music:
  directory: music        # folder scanned recursively for audio files
  loop: true
  shuffle: false
  volume_step: 5          # % per volume-up / volume-down press

audio_detection:
  # Add names here to prevent specific apps from triggering auto-pause.
  # Values are case-insensitive and support shell wildcards like "*Playground*".
  ignore_app_names: []
  ignore_media_names:
    - MechVibes Playground
  ignore_process_binaries: []

keyboard_sounds:
  enabled: true
  soundpack_directory: assets
  event: keydown          # keydown | keyup | both
  volume: 0.5             # 0.0–1.0; saved at runtime
  volume_step: 0.1
  max_polyphony: 32
  pipewire_quantum: 32    # frames at 48 kHz; lower = less latency; 0 = leave alone
  performance_preset: low_latency
  latency: 0.001          # PortAudio ring-buffer size in seconds
  blocksize: 32           # PortAudio frames per callback; keep ≤ pipewire_quantum
  trim_leading_silence: true
```

---

## Troubleshooting

**Music does not start / no music files found**
Make sure there are audio files in the `music/` directory (or wherever `music.directory`
points). Run `./run.sh --debug` and look at the printed playlist.

**Music pauses unexpectedly**
Run `./run.sh --debug`. Look for `external audio detected; pausing music: ...`.
The `app=`, `media=`, and `binary=` values in that line identify the offending
stream. Add the relevant value to `audio_detection.ignore_app_names` (or
`ignore_media_names` / `ignore_process_binaries`) in `config.yaml`.

**Keyboard sounds / hotkeys do not work**
Check that your user is in the `input` group (`id`). If not, see
[Keyboard Input Permission](#6-keyboard-input-permission) above.

**Crackling or audio dropouts**
Raise `latency` and/or `blocksize` in `config.yaml`:

```yaml
keyboard_sounds:
  latency: 0.010
  blocksize: 128
```

Or increase `pipewire_quantum` to `256` or `512`.

**How do I check the actual latency?**
```bash
./run.sh --deep-debug
```

Press a few letter keys and inspect the `[deep ...] key=...` lines:
- `enqueue->callback`: wait for the PortAudio callback — the main tunable latency.
- `callback->dac`: PortAudio's estimate of downstream buffering. On PipeWire/ALSA
  virtual devices, this number (~155 ms) is a measurement artifact — ignore it.
  Real latency is the sum of `enqueue->callback` plus the PipeWire pipeline (~2–4 ms
  at quantum=32).

---

## Keyboard Soundpack

The bundled files in `assets/` are a CherryMX Blue PBT keycap soundpack from
MechvibesDX (see `assets/README.md` for credits and format documentation):

- `assets/sound.ogg` — all key sounds in one audio file.
- `assets/config.json` — timing slices mapping each key to its position in the file.

---

## File Reference

| Path | Purpose |
|---|---|
| `bgmusic.py` | Single entry-point; all logic lives here |
| `config.yaml` | Static settings; read once at startup |
| `run.sh` | Preferred launcher (sets `PYTHON_GIL=0`) |
| `requirements.txt` | Python dependencies |
| `music/` | Put your audio files here |
| `assets/` | Bundled keyboard soundpack |
| `bgmusic_settings.json` | Persistent user settings (auto-created, gitignored) |
| `/tmp/bgmusic_state.json` | Runtime IPC state (created by daemon) |
| `/tmp/mpv_bg_socket` | Unix socket for mpv IPC (created by daemon) |
