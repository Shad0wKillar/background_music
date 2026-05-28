# CherryMX Blue – PBT Keycaps Soundpack

Mechanical keyboard sound pack for **CherryMX Blue switches with PBT keycaps**.  
Source: [MechvibesDX](https://github.com/hainguyents13/mechvibes-dx) · License: MIT · Author: Mechvibes

## Files

| File | Description |
|------|-------------|
| `sound.ogg` | Single audio file containing every key's sound, concatenated end-to-end (~2.5 MB) |
| `config.json` | Key-to-timing map — tells you where in `sound.ogg` each key's sound lives |
| `blue.jpg` | Pack icon (15 KB JPEG) |

---

## How `config.json` works

```jsonc
{
  "id": "keyboard-cherrymx-blue-pbt",
  "name": "CherryMX Blue - PBT keycaps",
  "audio_file": "sound.ogg",          // the audio file to load
  "definition_method": "single",      // one file, sliced by timing
  "options": {
    "random_pitch": false,
    "recommended_volume": 1.0
  },
  "definitions": {
    "KeyA": {                          // browser KeyboardEvent.code value
      "timing": [
        [28961.0, 29043.0],           // keyDOWN: [startMs, endMs]
        [29043.0, 29125.0]            // keyUP:   [startMs, endMs]
      ]
    },
    ...
  }
}
```

### Key points

- **Key names** match the browser's `KeyboardEvent.code` property exactly (`"KeyA"`, `"Space"`, `"ShiftLeft"`, `"F1"`, etc.) — not `keyCode` or `key`.
- **Timing values** are in **milliseconds** from the start of `sound.ogg`.
- Each key has **two timing pairs**: index `[0]` = keydown sound, index `[1]` = keyup sound.
- Some keys share timing slices (e.g. `Delete` reuses `ArrowUp`'s slice) — this is intentional.

---

## Playing a sound slice (Web Audio API)

```js
// 1. Load the audio file once
const audioCtx = new AudioContext();
const response = await fetch('sound.ogg');
const arrayBuffer = await response.arrayBuffer();
const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);

// 2. Fetch the config
const config = await fetch('config.json').then(r => r.json());

// 3. On keydown — play the keydown slice
window.addEventListener('keydown', (e) => {
  const def = config.definitions[e.code];
  if (!def) return;

  const [startMs, endMs] = def.timing[0];   // timing[0] = keydown
  playSlice(startMs, endMs);
});

// 4. On keyup — play the keyup slice
window.addEventListener('keyup', (e) => {
  const def = config.definitions[e.code];
  if (!def) return;

  const [startMs, endMs] = def.timing[1];   // timing[1] = keyup
  playSlice(startMs, endMs);
});

// Helper: slice and play
function playSlice(startMs, endMs) {
  const source = audioCtx.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(audioCtx.destination);
  source.start(0, startMs / 1000, (endMs - startMs) / 1000);
}
```

### Volume control

```js
const gainNode = audioCtx.createGain();
gainNode.gain.value = 1.0;           // 0.0–2.0; config recommends 1.0
gainNode.connect(audioCtx.destination);

// Connect source to gainNode instead of destination:
source.connect(gainNode);
```

### Preventing key-repeat double-fires

```js
const held = new Set();
window.addEventListener('keydown', (e) => {
  if (held.has(e.code)) return;      // ignore browser key-repeat events
  held.add(e.code);
  // ... play sound
});
window.addEventListener('keyup', (e) => {
  held.delete(e.code);
  // ... play sound
});
```

### Preventing browser shortcuts from intercepting keystrokes

```js
window.addEventListener('keydown', (e) => {
  // Block Vimium/extensions/browser shortcuts while not holding Ctrl/Alt/Meta
  if (!e.ctrlKey && !e.altKey && !e.metaKey) e.preventDefault();
  // ... rest of handler
});
```

---

## Supported keys (all 88 definitions)

`Escape` `F1–F12` `Backquote` `Digit0–9` `Minus` `Equal` `Backspace`  
`Tab` `KeyQ–Z` `BracketLeft` `BracketRight` `Backslash`  
`CapsLock` `Semicolon` `Quote` `Enter`  
`ShiftLeft` `ShiftRight` `Comma` `Period` `Slash`  
`ControlLeft` `AltLeft` `Space`  
`Insert` `Home` `PageUp` `Delete` `End` `PageDown`  
`ArrowUp` `ArrowDown` `ArrowLeft` `ArrowRight`  
`NumLock` `NumpadDivide` `NumpadMultiply` `NumpadSubtract`  
`Numpad7–9` `Numpad4–6` `Numpad1–3` `Numpad0` `NumpadDecimal`  
`NumpadAdd` `NumpadEnter` `PrintScreen` `ScrollLock` `Pause`
