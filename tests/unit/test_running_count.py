import unittest
from unittest.mock import MagicMock, patch

from features import presence


class _Proc:
    def __init__(self, pid, name="RobloxPlayerBeta.exe", create_time=1.0):
        self.info = {"pid": pid, "name": name, "create_time": create_time}


class RunningCountTests(unittest.TestCase):
    def setUp(self):
        presence._REPORTED.clear()
        self.addCleanup(presence._REPORTED.clear)

    def count(self, windows, hidden=None, processes=None):
        pids = set(windows)
        with patch.object(presence, "get_roblox_processes", return_value={pid: (1.0, None) for pid in pids}), \
                patch.object(presence, "get_windows_by_pid", return_value=windows), \
                patch.object(presence.psutil, "process_iter", return_value=processes or []), \
                patch.object(presence.win32gui, "IsWindow", return_value=True), \
                patch.object(presence.win32gui, "GetWindow", return_value=0), \
                patch.object(presence.win32gui, "GetWindowText", side_effect=lambda h: "Roblox" if h > 0 else ""), \
                patch.object(presence.win32gui, "IsWindowVisible", side_effect=lambda h: h != 99), \
                patch.object(presence.win32gui, "GetWindowRect", return_value=(0, 0, 800, 600)):
            return presence.game_window_pids(hidden)

    def test_only_processes_with_a_game_window_are_counted(self):
        self.assertEqual(self.count({1: [10], 2: [], 3: [-1]}), {1})

    def test_a_window_hidden_by_the_app_still_counts(self):
        self.assertEqual(self.count({1: [99]}), set())
        self.assertEqual(self.count({1: [99]}, hidden={1}), {1})

    def test_a_process_without_a_window_is_described_once_in_the_console(self):
        with patch.object(presence, "describe_process", return_value="RobloxPlayerBeta.exe (PID 2)") as describe, \
                patch("builtins.print") as printed:
            self.count({1: [10], 2: []}, processes=[_Proc(1), _Proc(2)])
            self.count({1: [10], 2: []}, processes=[_Proc(1), _Proc(2)])
        describe.assert_called_once_with(2)
        self.assertIn("Not counted as running", printed.call_args.args[0])

    def test_describe_process_names_the_path_and_parent_but_not_the_command_line(self):
        parent = MagicMock(pid=4)
        parent.name.return_value = "explorer.exe"
        parent.exe.return_value = r"C:\Windows\explorer.exe"
        process = MagicMock()
        process.name.return_value = "RobloxPlayerBeta.exe"
        process.exe.return_value = r"C:\Roblox\RobloxPlayerBeta.exe"
        process.parent.return_value = parent
        with patch.object(presence.psutil, "Process", return_value=process):
            text = presence.describe_process(7)
        self.assertIn(r"C:\Roblox\RobloxPlayerBeta.exe", text)
        self.assertIn("explorer.exe (PID 4", text)
        process.cmdline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
