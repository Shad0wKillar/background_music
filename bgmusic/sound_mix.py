"""Small DSP helpers for keyboard sound mixing."""
from __future__ import annotations

from typing import Any

DEFAULT_HEADROOM_GAIN = 0.5
DEFAULT_SOFT_CLIP_THRESHOLD = 0.8
DEFAULT_OUTPUT_CEILING = 0.98


def compressed_excess(excess: Any, limit: float) -> Any:
    """Compress positive excess so it approaches, but never crosses, limit."""
    return excess / (1.0 + (excess / limit))


def soft_clip_span(threshold: float, ceiling: float) -> float:
    """Return the clip span after validating threshold and ceiling."""
    if threshold < 0.0 or ceiling <= threshold:
        raise ValueError("soft clip ceiling must be greater than a non-negative threshold")
    return ceiling - threshold


def threshold_soft_clip_value(
    value: float,
    threshold: float = DEFAULT_SOFT_CLIP_THRESHOLD,
    ceiling: float = DEFAULT_OUTPUT_CEILING,
) -> float:
    """Return a scalar soft-clipped sample with untouched low-level signal."""
    span = soft_clip_span(threshold, ceiling)
    abs_value = abs(value)
    if abs_value <= threshold:
        return value
    soft_excess = compressed_excess(abs_value - threshold, span)
    sign = 1.0 if value >= 0.0 else -1.0
    return sign * (threshold + soft_excess)


def threshold_soft_clip_buffer(
    samples: Any,
    np: Any,
    threshold: float = DEFAULT_SOFT_CLIP_THRESHOLD,
    ceiling: float = DEFAULT_OUTPUT_CEILING,
) -> None:
    """Soft clip a numpy buffer in-place after threshold."""
    span = soft_clip_span(threshold, ceiling)
    if len(samples) == 0:
        return
    abs_samples = np.abs(samples)
    mask = abs_samples > threshold
    if not np.any(mask):
        return
    excess = abs_samples[mask] - threshold
    samples[mask] = (
        np.sign(samples[mask])
        * (threshold + compressed_excess(excess, span))
    )


def finalize_keyboard_mix(
    samples: Any,
    np: Any,
    volume: float,
    headroom_gain: float = DEFAULT_HEADROOM_GAIN,
    threshold: float = DEFAULT_SOFT_CLIP_THRESHOLD,
    ceiling: float = DEFAULT_OUTPUT_CEILING,
) -> None:
    """Apply fixed headroom, user volume, and final soft clipping in-place."""
    if len(samples) == 0:
        return
    samples *= volume * headroom_gain
    threshold_soft_clip_buffer(samples, np, threshold, ceiling)
