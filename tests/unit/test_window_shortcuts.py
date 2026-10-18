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
               window_grid.MINIMIZE_ALL_HOTKEY_ID, window_grid.RESTORE_ALL_HOTKEY_ID}
        self.assertEqual(len(ids), 4)


if __name__ == "__main__":
    unittest.main()
