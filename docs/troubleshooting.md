# Troubleshooting

## Music Does Not Start

Make sure there are audio files in the configured `music.directory`. Run:

```bash
./run.sh --debug
```

Check that the printed playlist contains the files you expect.

## Music Pauses Unexpectedly

Run with debug logging and look for:

```text
external audio detected; pausing music: ...
```

The `app=`, `media=`, `role=`, `pid=`, and `binary=` fields identify the stream.
You can add a precise rule under `audio_detection.ignore_rules`.

## Browser Helper Streams

Chromium, Zen Browser, and Tor Browser can leave helper/idle streams around.
The default config ignores only the precise helper cases, such as corked browser
streams or the `Virtual Source output` stream. It does not blanket-ignore real
browser playback.

## Keyboard Sounds Or Hotkeys Do Not Work

Check that your user is in the `input` group:

```bash
id
```

If not, run:

```bash
sudo usermod -aG input "$USER"
```

Then fully log out and back in.

## Crackling Or Audio Dropouts

Raise `latency` and/or `blocksize`:

```yaml
keyboard_sounds:
  latency: 0.010
  blocksize: 128
```

Or increase `pipewire_quantum` to `256` or `512`.

## Key Sounds Play Twice

Keep `duplicate_suppression_ms` enabled. Some keyboards expose more than one
evdev keyboard interface and report the same physical key through both.

## Check Actual Latency

```bash
./run.sh --deep-debug
```

Inspect the `[deep ...] key=...` lines:

- `enqueue->callback`: wait for the PortAudio callback.
- `callback->dac`: PortAudio's downstream buffering estimate. On PipeWire/ALSA
  virtual devices this can be a measurement artifact.
