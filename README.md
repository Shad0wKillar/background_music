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
- Separate volume controls for background music and keyboard click sounds.
- Editable YAML config for music paths, loop behavior, volumes, and hotkeys.
- One-line `super` modifier setting so all `super+...` shortcuts can move from
  `Alt` to another modifier.

## Requirements

System packages:

- Python 3.10 or newer
- `uv`
- `mpv`
- PulseAudio or PipeWire with the PulseAudio compatibility service
- Permission to read keyboard devices under `/dev/input` for global hotkeys and
  keyboard sounds

Python packages are listed in `requirements.txt`.

## Setup

```bash
uv venv
uv pip install -r requirements.txt
```

If `mpv` is not installed, install it with your system package manager. For
example, on Arch Linux:

```bash
sudo pacman -S mpv
```

## Usage

Start the daemon:

```bash
uv run bgmusic.py
```

This is the same as:

```bash
uv run python bgmusic.py start
```

Start with debug logging:

```bash
uv run bgmusic.py --debug
```

Start with per-key latency diagnostics:

```bash
uv run bgmusic.py --deep-debug
```

Start with shuffle for this run:

```bash
uv run python bgmusic.py start --shuffle
```

Control a running daemon from another terminal:

```bash
uv run python bgmusic.py toggle
uv run python bgmusic.py next
uv run python bgmusic.py previous
uv run python bgmusic.py loop
uv run python bgmusic.py keyboard-sounds
uv run python bgmusic.py volume-up
uv run python bgmusic.py volume-down
uv run python bgmusic.py mute
uv run python bgmusic.py keyboard-volume-up
uv run python bgmusic.py keyboard-volume-down
uv run python bgmusic.py volume +10
uv run python bgmusic.py volume -5
uv run python bgmusic.py audio-devices
```

Debug mode prints the playlist, the active track, whether music is playing or
paused, and which Pulse/PipeWire sink input caused an auto-pause. If music stops
unexpectedly, look for a line like `external audio detected; pausing music`.
That line gives you the `app`, `media`, and `binary` values you can ignore in
`config.yaml` when an app exposes a silent-but-uncorked stream.

Deep debug mode prints timing for the first 100 key sounds:

- `kernel->receipt`: time from Linux input event timestamp to Python receiving it.
- `receipt->enqueue`: Python work before adding the sound to the mixer.
- `enqueue->callback`: wait until the PortAudio callback first renders it.
- `callback->dac`: PortAudio's estimate of remaining output-buffer latency.
- `receipt->estimated_dac`: best in-process estimate before the audio backend.

If `receipt->estimated_dac` is small but it still feels late, the remaining delay
is likely after PortAudio, such as PipeWire/Pulse routing, Bluetooth/headset
latency, or device buffering.

## Default Hotkeys

The default config calls `Alt` the `super` key:

| Action | Hotkey |
| --- | --- |
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

To change every shortcut from `Alt` to another modifier, edit only this line in
`config.yaml`:

```yaml
super: Alt
```

Supported values are `Alt`, `Control`, `Shift`, `Meta`, and `Super`.

You can also edit individual hotkeys:

```yaml
hotkeys:
  toggle_music: "super+p"
  next_track: "super+]"
  previous_track: "super+["
  toggle_loop: "super+l"
  toggle_keyboard_sounds: "super+m"
  volume_up: "super+="
  volume_down: "super+-"
  toggle_mute: "super+0"
  keyboard_volume_up: "super+shift+equal"
  keyboard_volume_down: "super+shift+minus"
```

## Configuration

The app reads `config.yaml` by default. You can use a different file with:

```bash
uv run python bgmusic.py --config path/to/config.yaml start
```

Important settings:

```yaml
music:
  directory: music
  loop: true
  shuffle: false
  volume_step: 5
  supported_extensions:
    - .mp3
    - .flac
    - .wav
    - .ogg
    - .opus
    - .m4a
    - .aac
    - .webm
    - .mp4
    - .mkv

audio_detection:
  ignore_app_names: []
  ignore_media_names:
    - MechVibes Playground
  ignore_process_binaries: []

keyboard_sounds:
  enabled: true
  soundpack_directory: assets
  event: keydown
  volume: 0.5
  volume_step: 0.1
  max_polyphony: 32
  performance_preset: low_latency
  latency: 0.005
  blocksize: 64
  state_sync_interval: 0.1
  trim_leading_silence: true
  trim_threshold_ratio: 0.02
  trim_max_ms: 8
  trim_preroll_ms: 0.5
```

Relative paths are resolved from the project directory.

## Keyboard Input Permissions

Global hotkeys and keyboard sounds use `evdev`, which reads Linux input devices
directly. This works under Wayland, but your user must be allowed to read
`/dev/input/event*`.

If the app prints a permission warning, use one of these approaches:

- Add your user to the distro's input-related group if your system provides one.
- Add a udev rule for your keyboard device.
- Run the daemon with suitable permissions.

The app does not grab or block the keyboard; normal typing continues to work.

On Arch-style systems, the simplest setup is usually:

```bash
sudo usermod -aG input "$USER"
```

Then fully log out and log back in. A new terminal is not enough. To test in the
current terminal without logging out:

```bash
newgrp input
id
uv run bgmusic.py
```

`id` should include `input`. You can also check device ownership:

```bash
ls -l /dev/input/event*
```

Expected devices usually look like `root input` with `crw-rw----` permissions.
Membership in `input` can read raw keyboard input system-wide, so only grant it
to a trusted local user.

## Music

Put music files in `music/`. The daemon scans that folder recursively and sends
only supported media files to `mpv`, so files like `music_names` and partial
downloads are ignored. `.webm` is supported by default. The playlist loops by
default; press `Alt+l` or run `uv run python bgmusic.py loop` to toggle looping
while the daemon is running.

Background music pauses for external audio sources only. The daemon ignores its
own `mpv` stream and its own keyboard sound stream, so typing will not stop the
music.

The keyboard sound stream is also ignored by sink index when it is created at
startup. This handles audio backends that expose the keyboard stream as a generic
Python or PortAudio sink instead of using the configured stream name.

Pulse/PipeWire's `corked` flag means a stream is open and not paused; it does
not prove the stream is actually audible. Browsers sometimes keep silent streams
uncorked. If debug mode shows a false-positive source, add it to:

```yaml
audio_detection:
  ignore_app_names: []
  ignore_media_names:
    - MechVibes Playground
  ignore_process_binaries: []
```

Ignore rules are case-insensitive and support shell-style wildcards like
`"*Playground*"`. Prefer ignoring a specific `media` name over a whole browser
app name, because ignoring `Zen` or `Firefox` would also ignore real videos.

## Keyboard Soundpack

The bundled files in `assets/` contain a CherryMX Blue PBT keycap soundpack:

- `assets/sound.ogg` stores all key sounds in one audio file.
- `assets/config.json` maps each key to a timing slice inside that file.
- `assets/README.md` documents the soundpack format.

The soundpack comes from MechvibesDX and is credited in `assets/README.md`.

Keyboard sounds use a small mixer inside the daemon. Each key press starts a new
slice, and active slices are summed together up to `max_polyphony`, so fast
typing can layer clicks naturally.

For lower click latency, keyboard sounds default to `performance_preset:
low_latency`, `latency: 0.005`, and `blocksize: 64`. The daemon opens the
low-latency stream first and falls back to `latency: low` plus `blocksize: 128`
if the audio backend rejects it.

If you hear crackling or dropouts, try:

```yaml
keyboard_sounds:
  latency: low
  blocksize: 128
```

or increase `blocksize` to `256`. The delay is mostly audio backend buffering
through PortAudio and Pulse/PipeWire, plus a few milliseconds of leading silence
inside some key samples, not Python executing the key handler. The daemon trims
that leading silence once at startup when `trim_leading_silence` is enabled.

To inspect output devices and their default latency values:

```bash
uv run python bgmusic.py audio-devices
```

For deeper timing, run:

```bash
uv run bgmusic.py --deep-debug
```

Then press a few normal letter keys and inspect the `[deep ...] key=...` lines.
