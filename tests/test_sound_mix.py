from __future__ import annotations

import unittest

import numpy as np

from bgmusic.sound_mix import (
    DEFAULT_OUTPUT_CEILING,
    DEFAULT_SOFT_CLIP_THRESHOLD,
    compressed_excess,
    finalize_keyboard_mix,
    soft_clip_span,
    threshold_soft_clip_buffer,
    threshold_soft_clip_value,
)


class SoundMixTests(unittest.TestCase):
    def test_soft_clip_leaves_safe_samples_untouched(self) -> None:
        samples = np.array([[0.25, -0.75], [0.80, -0.80]], dtype=np.float32)

        threshold_soft_clip_buffer(samples, np)

        np.testing.assert_allclose(
            samples,
            np.array([[0.25, -0.75], [0.80, -0.80]], dtype=np.float32),
        )

    def test_soft_clip_compresses_only_excess(self) -> None:
        value = 1.5
        limit = DEFAULT_OUTPUT_CEILING - DEFAULT_SOFT_CLIP_THRESHOLD
        expected = DEFAULT_SOFT_CLIP_THRESHOLD + compressed_excess(0.7, limit)

        self.assertAlmostEqual(threshold_soft_clip_value(value), expected)

    def test_soft_clip_is_symmetric_for_negative_samples(self) -> None:
        positive = threshold_soft_clip_value(1.5)
        negative = threshold_soft_clip_value(-1.5)

        self.assertAlmostEqual(negative, -positive)

    def test_soft_clip_approaches_output_ceiling(self) -> None:
        clipped = threshold_soft_clip_value(1000.0)

        self.assertLess(clipped, DEFAULT_OUTPUT_CEILING)
        self.assertGreater(clipped, DEFAULT_OUTPUT_CEILING - 0.001)

    def test_finalize_keyboard_mix_applies_fixed_headroom_first(self) -> None:
        samples = np.array([[0.30, -0.30]], dtype=np.float32)

        finalize_keyboard_mix(samples, np, volume=1.0)

        np.testing.assert_allclose(samples, np.array([[0.15, -0.15]], dtype=np.float32))

    def test_soft_clip_rejects_invalid_threshold(self) -> None:
        with self.assertRaises(ValueError):
            soft_clip_span(0.8, 0.8)


if __name__ == "__main__":
    unittest.main()
