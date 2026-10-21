import unittest

from features import auto_rejoin


class PauseTests(unittest.TestCase):
    def tearDown(self):
        auto_rejoin._paused_until = 0.0

    def test_pause_all_counts_down(self):
        self.assertEqual(auto_rejoin.paused_seconds_left(), 0)
        auto_rejoin.pause_all(60)
        self.assertGreater(auto_rejoin.paused_seconds_left(), 55)


if __name__ == "__main__":
    unittest.main()
