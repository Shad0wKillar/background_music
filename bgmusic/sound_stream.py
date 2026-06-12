"""PortAudio stream setup for keyboard sounds."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from bgmusic.constants import KEYBOARD_APP_NAME


@contextmanager
def keyboard_stream_environment(latency: str | float):
    latency_secs = latency if isinstance(latency, float) else 0.020
    pw_frames = max(32, round(latency_secs * 48000))
    props = {
        "PULSE_PROP_application.name": KEYBOARD_APP_NAME,
        "PULSE_PROP_media.name": KEYBOARD_APP_NAME,
        "PULSE_PROP_media.role": "event",
        "PIPEWIRE_LATENCY": f"{pw_frames}/48000",
        "PULSE_LATENCY_MSEC": str(max(1, round(latency_secs * 1000))),
    }
    previous = {k: os.environ.get(k) for k in props}
    os.environ.update(props)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def describe_output_device(sd: Any, stream: Any) -> str:
    try:
        device = getattr(stream, "device", None)
        if isinstance(device, tuple):
            device = device[1]
        if device is None or device == -1:
            device = sd.default.device[1]
        info = sd.query_devices(device)
        hostapi = sd.query_hostapis(info["hostapi"])["name"]
        return f"[{device}] {info['name']} via {hostapi}"
    except Exception:
        return "unknown"


def open_keyboard_stream(
    sd: Any,
    callback: Any,
    sample_rate: int,
    channels: int,
    latency: str | float,
    blocksize: int,
    logger: Any,
) -> Any:
    attempts = [(latency, max(0, blocksize), "configured"), ("low", 128, "fallback")]
    last_error: Exception | None = None
    for attempt_latency, attempt_blocksize, label in attempts:
        stream = None
        try:
            logger.log(
                f"opening keyboard audio stream: latency={attempt_latency}, "
                f"blocksize={attempt_blocksize}, sample_rate={sample_rate}, channels={channels}"
            )
            stream = sd.OutputStream(
                samplerate=sample_rate, blocksize=attempt_blocksize,
                channels=channels, dtype="float32", latency=attempt_latency,
                callback=callback, prime_output_buffers_using_stream_callback=True,
            )
            stream.start()
            logger.log(
                f"keyboard audio stream opened ({label}); "
                f"actual latency={getattr(stream, 'latency', 'unknown')}; "
                f"device={describe_output_device(sd, stream)}"
            )
            return stream
        except Exception as error:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            last_error = error
            logger.log(f"keyboard audio stream open failed ({label}): {error}")
    raise RuntimeError(f"Could not open keyboard audio stream: {last_error}")
