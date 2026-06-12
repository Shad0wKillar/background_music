# Configuration

`config.yaml` is read once at startup. Relative paths are resolved from the
project directory.

```yaml
music:
  directory: music
  loop: true
  shuffle: false
  volume_step: 5

audio_detection:
  ignore_app_names: []
  ignore_media_names:
    - MechVibes Playground
  ignore_process_binaries: []
  ignore_rules:
    - name: virtual-source-output
      any:
        - media: "Virtual Source output"
    - name: chromium-idle-helper
      all:
        - binary: ["chromium", "chromium-browser", "chrome", "google-chrome*"]
        - corked: true
    - name: zen-idle-helper
      all:
        - binary: ["zen*", "zen-browser", "zen-bin"]
        - corked: true
    - name: tor-idle-helper
      all:
        - binary: ["tor-browser*", "firefox.real", "firefox"]
        - app: ["Tor Browser*", "tor-browser*"]
        - corked: true

keyboard_sounds:
  enabled: true
  soundpack_directory: assets
  event: keydown
  volume: 0.5
  volume_step: 0.05
  max_polyphony: 32
  pipewire_quantum: 128
  performance_preset: low_latency
  latency: 0.005
  blocksize: 64
  duplicate_suppression_ms: 12
  trim_leading_silence: true
```

## Flexible Ignore Rules

`audio_detection.ignore_rules` supports `app`, `media`, `role`, `binary`,
`pid`, `corked`, and `playing`.

Use `all` when every condition must match:

```yaml
- name: browser-idle-only
  all:
    - binary: "chromium"
    - corked: true
```

Use `any` when one condition is enough:

```yaml
- name: virtual-helper-streams
  any:
    - media: "Virtual Source output"
    - media: "*helper*"
```

String values accept shell-style wildcards and match case-insensitively.
List values mean “match any of these values.”
