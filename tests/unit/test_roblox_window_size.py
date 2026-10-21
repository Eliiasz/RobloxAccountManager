import unittest
from unittest.mock import patch

from features import roblox_window_size as ws

WORK = (0, 0, 1920, 1040)


class MathTests(unittest.TestCase):
    def test_five_by_four_on_1080p_matches_the_screenshot_size(self):
        self.assertEqual(ws.fit_size(WORK, (5, 4)), (384, 260))

    def test_slots_go_left_to_right_then_down(self):
        self.assertEqual(ws.slot_rect(WORK, (5, 4), 0)[:2], (0, 0))
        self.assertEqual(ws.slot_rect(WORK, (5, 4), 1)[:2], (384, 0))
        self.assertEqual(ws.slot_rect(WORK, (5, 4), 5)[:2], (0, 260))

    def test_how_many_fit(self):
        self.assertEqual(ws.how_many_fit(WORK, (320, 240)), (6, 4))

    def test_free_slot_skips_taken_spots(self):
        taken = [ws.slot_rect(WORK, (5, 4), 0), ws.slot_rect(WORK, (5, 4), 1)]
        self.assertEqual(ws.free_slot(WORK, (5, 4), taken), 2)

    def test_presets_include_the_promised_sizes(self):
        for size in ((800, 600), (640, 480), (480, 360), (400, 300), (320, 240), (384, 270), (854, 480)):
            self.assertIn(size, ws.PRESET_SIZES)

    def test_bad_saved_values_fall_back(self):
        with patch.object(ws.settings_store, "load", return_value={
                ws.MODE_SETTING: "weird", ws.SIZE_SETTING: "x", ws.LAYOUT_SETTING: [99, 0]}):
            choice = ws.load_choice()
        self.assertEqual(choice.mode, ws.MODE_OFF)
        self.assertEqual(choice.size, ws.DEFAULT_SIZE)
        self.assertEqual(choice.layout, (12, 1))


class WatcherTests(unittest.TestCase):
    def test_new_windows_are_resized_once_after_settling(self):
        watcher = ws.WindowSizeWatcher()
        watcher._handled = {1}  # already open before: left alone
        placed = []
        choice = ws.Choice(ws.MODE_FIT, (640, 480), (5, 4))
        with patch.object(ws, "load_choice", return_value=choice), \
                patch.object(ws, "roblox_windows", return_value=[1, 2]), \
                patch.object(ws, "place_new", side_effect=lambda c, h, others: placed.append((h, others))):
            self.assertEqual(watcher.tick(now=100.0), [])       # just appeared: wait
            self.assertEqual(watcher.tick(now=101.5), [2])      # settled: placed
            self.assertEqual(watcher.tick(now=103.0), [])       # never again
        self.assertEqual(placed, [(2, [1])])

    def test_off_touches_nothing(self):
        watcher = ws.WindowSizeWatcher()
        with patch.object(ws, "load_choice", return_value=ws.Choice(ws.MODE_OFF, (640, 480), (5, 4))), \
                patch.object(ws, "roblox_windows") as windows:
            self.assertEqual(watcher.tick(now=1.0), [])
        windows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
