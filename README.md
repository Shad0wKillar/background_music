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

From a fresh clone, run:

```bash
./run.sh
```

The launcher automatically:

1. Installs `uv` if missing, using its [official installer](https://docs.astral.sh/uv/getting-started/installation/)
   into `~/.local/bin` without changing shell profiles. This needs `curl` or `wget`.
2. Downloads the Python version in `.python-version` and creates `.venv` if missing.
3. Installs missing native dependencies on Arch/Manjaro and Debian/Ubuntu:
   `mpv`, audio libraries, and the build tools needed by `evdev`.
4. Installs Python dependencies from `requirements.txt`, including the Textual UI.
   Later launches check for missing or newly added dependencies without upgrading
   packages that already satisfy the requirements.
5. Checks keyboard access. When needed, it creates the `input` group, adds your
   user, and installs a keyboard udev rule if devices do not already use that group.
6. Checks the desktop audio connection, then starts the app.

Run as your normal user. Setup asks for your **sudo password** only when system
packages or keyboard permissions need changing. Membership in `input` allows
reading raw input devices for global hotkeys and keyboard sounds. If the current
session does not yet have that group, the launcher starts the app as your user
with the group active immediately. A full logout/login makes the group available
throughout the desktop and removes the need for this extra sudo launch.

You need a running PulseAudio server or PipeWire with `pipewire-pulse`. Setup
reports a missing audio session rather than replacing your desktop's audio stack.
On other Linux distributions, install the reported native packages manually and
rerun the launcher. Setup failures stop before opening the TUI; rerun after
resolving the reported problem.

To prepare everything without starting playback:

```bash
./run.sh --setup
```

Help and control commands do not require keyboard access. The launcher can also
be called by its full path from another directory; `-c` paths remain relative to
your current directory.

### Add music

Put audio files in `music/`, or set `music.directory` in `config.yaml`.
Supported formats: `.mp3`, `.flac`, `.wav`, `.ogg`, `.opus`, `.m4a`, `.aac`,
`.webm`, `.mp4`, `.mkv`.

```bash
mkdir -p music
cp ~/Music/*.mp3 music/
```

### Manual Python setup

If you prefer to manage setup yourself, install the native dependencies and
keyboard permissions first, then run:

```bash
uv venv --python 3.14t
uv pip install -r requirements.txt
PYTHON_GIL=0 uv run python -c "import sys, yaml, numpy, sounddevice, soundfile, evdev, pulsectl, textual; print('GIL:', sys._is_gil_enabled())"
```

Expected output: `GIL: False`.

## Running the Daemon

### Recommended (lowest latency)

```bash
./run.sh
```

`run.sh` performs setup checks, sets `PYTHON_GIL=0`, and passes application
arguments through to `bgmusic.py`. The GIL stays off even when evdev asks to
re-enable it.

### Direct launch (after manual setup)

```bash
uv run bgmusic.py
```

### Debug modes

```bash
./run.sh --debug        # verbose playback and audio-detection logs
./run.sh --deep-debug   # per-key latency diagnostics for the first 100 keystrokes
./run.sh start --shuffle  # shuffle the playlist for this run only
```

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
uv run bgmusic.py keyboard-volume-up    # raise keyboard click volume by volume_step (default 5 %, saved)
uv run bgmusic.py keyboard-volume-down  # lower keyboard click volume (saved)
uv run bgmusic.py volume +10         # relative volume change; capped at 100 %
uv run bgmusic.py volume -5
uv run bgmusic.py audio-devices      # list audio output devices
```

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

## More Documentation

- [Configuration](docs/configuration.md)
- [Troubleshooting](docs/troubleshooting.md)

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
| `bgmusic.py` | Thin entry-point (`python bgmusic.py`); all logic lives in `bgmusic/` |
| `bgmusic/` | Python package — daemon, actions, hotkeys, audio, config, IPC, etc. |
| `config.yaml` | Static settings; read once at startup |
| `run.sh` | Preferred launcher (sets `PYTHON_GIL=0`) |
| `requirements.txt` | Python dependencies |
| `music/` | Put your audio files here |
| `assets/` | Bundled keyboard soundpack |
| `bgmusic_settings.json` | Persistent user settings (auto-created, gitignored) |
| `/tmp/bgmusic_state.json` | Runtime IPC state (created by daemon) |
| `/tmp/mpv_bg_socket` | Unix socket for mpv IPC (created by daemon) |
