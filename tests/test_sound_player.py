from __future__ import annotations

import threading
import unittest

import numpy as np

from bgmusic.sound_player import KeyboardSoundPlayer
from bgmusic.sound_mix import DEFAULT_OUTPUT_CEILING, threshold_soft_clip_value


class FakeLogger:
    deep_enabled = False


def make_player(max_polyphony: int = 32) -> KeyboardSoundPlayer:
    player = object.__new__(KeyboardSoundPlayer)
    player.np = np
    player.lock = threading.Lock()
    player.enabled = True
    player.event_mode = "keydown"
    player.volume = 1.0
    player.max_polyphony = max_polyphony
    player.active = []
    player.evdev_clips = {}
    player.logger = FakeLogger()
    player.deep_key_count = 0
    player.deep_key_limit = 100
    return player


class KeyboardSoundPlayerTests(unittest.TestCase):
    def test_callback_mixes_overlapping_key_sounds(self) -> None:
        player = make_player()
        clip_a = np.full((4, 2), 0.20, dtype=np.float32)
        clip_b = np.full((4, 2), 0.25, dtype=np.float32)
        player.active = [[clip_a, 0, None], [clip_b, 0, None]]

        outdata = np.zeros((2, 2), dtype=np.float32)
        player._callback(outdata, 2, None, None)

        np.testing.assert_allclose(outdata, np.full((2, 2), 0.225, dtype=np.float32))
        self.assertEqual(len(player.active), 2)
        self.assertEqual(player.active[0][1], 2)
        self.assertEqual(player.active[1][1], 2)

    def test_play_retains_newest_voices_when_polyphony_is_full(self) -> None:
        player = make_player(max_polyphony=2)
        player.evdev_clips = {
            ("KEY_A", 1): np.full((2, 2), 0.10, dtype=np.float32),
            ("KEY_B", 1): np.full((2, 2), 0.20, dtype=np.float32),
            ("KEY_C", 1): np.full((2, 2), 0.30, dtype=np.float32),
        }

        player.play("KEY_A", 1)
        player.play("KEY_B", 1)
        player.play("KEY_C", 1)

        outdata = np.zeros((2, 2), dtype=np.float32)
        player._callback(outdata, 2, None, None)

        np.testing.assert_allclose(outdata, np.full((2, 2), 0.25, dtype=np.float32))

    def test_hybrid_mixer_soft_clips_heavy_overlap(self) -> None:
        player = make_player()
        player.active = [
            [np.full((2, 2), 0.90, dtype=np.float32), 0, None],
            [np.full((2, 2), 0.90, dtype=np.float32), 0, None],
        ]

        outdata = np.zeros((2, 2), dtype=np.float32)
        player._callback(outdata, 2, None, None)

        expected = threshold_soft_clip_value(0.90)
        np.testing.assert_allclose(outdata, np.full((2, 2), expected, dtype=np.float32))
        self.assertLess(float(np.max(np.abs(outdata))), DEFAULT_OUTPUT_CEILING)
        self.assertFalse(np.any(outdata == 1.0))


if __name__ == "__main__":
    unittest.main()
