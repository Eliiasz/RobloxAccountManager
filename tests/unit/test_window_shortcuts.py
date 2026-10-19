import unittest
from unittest.mock import MagicMock, patch

from features import window_grid


class WindowShortcutTests(unittest.TestCase):
    def test_default_keys_parse(self):
        self.assertEqual(window_grid._parse_hotkey("F11")[1], 0x7A)
        self.assertEqual(window_grid._parse_hotkey("F10")[1], 0x79)

    def test_minimize_and_restore_reach_every_window(self):
        user32 = MagicMock()
        with patch.object(window_grid, "_get_roblox_windows", return_value=[11, 22, 33]), \
                patch.object(window_grid, "_user32", user32):
            self.assertEqual(window_grid.minimize_roblox_windows(), 3)
            commands = {call.args[1] for call in user32.ShowWindowAsync.call_args_list}
            self.assertEqual(commands, {window_grid._SW_MINIMIZE})
            user32.reset_mock()
            self.assertEqual(window_grid.restore_roblox_windows(), 3)
            commands = {call.args[1] for call in user32.ShowWindowAsync.call_args_list}
            self.assertEqual(commands, {window_grid._SW_RESTORE})

    def test_no_roblox_windows_is_fine(self):
        with patch.object(window_grid, "_get_roblox_windows", return_value=[]):
            self.assertEqual(window_grid.minimize_roblox_windows(), 0)

    def test_hotkey_ids_are_distinct(self):
        ids = {window_grid.HOTKEY_ID, window_grid.KILL_ALL_HOTKEY_ID,
               window_grid.MINIMIZE_ALL_HOTKEY_ID, window_grid.RESTORE_ALL_HOTKEY_ID,
               window_grid.MINIMIZE_ONE_HOTKEY_ID}
        self.assertEqual(len(ids), 5)

    def test_minimize_one_prefers_the_focused_roblox_window(self):
        pick = window_grid._pick_window_to_minimize
        self.assertEqual(pick([11, 22, 33], 22, [99, 33, 22, 11]), 22)

    def test_minimize_one_otherwise_takes_the_top_roblox_window(self):
        pick = window_grid._pick_window_to_minimize
        self.assertEqual(pick([11, 22, 33], 99, [99, 33, 22, 11]), 33)
        self.assertEqual(pick([], 99, [99]), 0)

    def test_each_press_minimizes_exactly_one_window(self):
        user32 = MagicMock()
        gui = MagicMock()
        gui.IsIconic.side_effect = lambda hwnd: hwnd == 11
        gui.GetForegroundWindow.return_value = 0
        with patch.object(window_grid, "_get_roblox_windows", return_value=[11, 22, 33]), \
                patch.object(window_grid, "_windows_in_z_order", return_value=[22, 33, 11]), \
                patch.object(window_grid, "win32gui", gui), \
                patch.object(window_grid, "_user32", user32):
            self.assertEqual(window_grid.minimize_one_roblox_window(), 1)
        self.assertEqual(user32.ShowWindowAsync.call_count, 1)
        self.assertEqual(user32.ShowWindowAsync.call_args.args[1], window_grid._SW_MINIMIZE)

    def test_minimize_one_with_everything_minimized_does_nothing(self):
        gui = MagicMock()
        gui.IsIconic.return_value = True
        with patch.object(window_grid, "_get_roblox_windows", return_value=[11]), \
                patch.object(window_grid, "win32gui", gui):
            self.assertEqual(window_grid.minimize_one_roblox_window(), 0)


if __name__ == "__main__":
    unittest.main()
