import unittest
from unittest.mock import patch

from wisp.stationary_localization import DARK, responses


class LocalizationTests(unittest.TestCase):
    def test_matched_fresh_lifetimes_and_action_tick(self):
        histories = []

        class Recorder:
            def __init__(self, genome, seed):
                self.frames = []
                histories.append((seed, self.frames))

            def choose_levels(self, *frame):
                self.frames.append(frame)
                return int(len(self.frames) == 2)

        levels = (.25, .75)
        with patch("wisp.stationary_localization.Wisp", Recorder):
            turns = responses(None, levels, 73)
        self.assertEqual(histories, [(73, [levels, levels]), (73, [levels, DARK]),
                                     (73, [DARK, DARK])])
        self.assertEqual(turns, dict(visible=1, blank=1, dark_only=1, decoder_visible=1,
                                     decoder_blank=0, last_seen_blank=1))
