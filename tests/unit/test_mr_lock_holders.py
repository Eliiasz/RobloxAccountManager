import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from features import account_actions as actions
from features import file_holders


class LockRetryTests(unittest.TestCase):
    def tearDown(self):
        actions.mr_lock_holders = []

    def test_retries_then_names_the_holder(self):
        calls = []

        def fail(_name):
            calls.append(1)
            raise OSError("in use")

        with patch.object(actions, "_lock_roblox_storage_file", side_effect=fail), \
                patch.object(actions.time, "sleep"), \
                patch.object(actions.time, "monotonic", side_effect=[0.0, 0.5, 1.0, 1.5, 9.0]), \
                patch.object(file_holders, "who_has", return_value=[(4242, "RobloxCrashHandler.exe")]):
            with self.assertRaises(OSError) as caught:
                actions._lock_storage_with_retry("RobloxCookies.dat")
        self.assertGreater(len(calls), 1)
        self.assertIn("RobloxCrashHandler.exe (PID 4242)", str(caught.exception))
        self.assertEqual(actions.mr_lock_holders, [(4242, "RobloxCrashHandler.exe")])

    def test_success_after_a_retry(self):
        results = [OSError("busy"), {"file": None}]

        def lock(_name):
            value = results.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(actions, "_lock_roblox_storage_file", side_effect=lock), \
                patch.object(actions.time, "sleep"):
            self.assertEqual(actions._lock_storage_with_retry("appStorage.json"), {"file": None})

    def test_close_never_closes_this_app(self):
        actions.mr_lock_holders = [(os.getpid(), "ANaneRAM.exe")]
        with patch.object(actions.psutil, "Process") as process:
            self.assertEqual(actions.close_lock_holders(), 0)
        process.assert_not_called()

    def test_other_app_copy_is_recognised(self):
        self.assertTrue(file_holders.is_this_app("ANaneRAM.exe"))
        self.assertTrue(file_holders.is_this_app("EvanovarRAM-v2.10.5.exe"))
        self.assertFalse(file_holders.is_this_app("RobloxCrashHandler.exe"))


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class RestartManagerTests(unittest.TestCase):
    def test_finds_the_process_holding_a_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        try:
            with open(path, "rb"):
                pids = [pid for pid, _name in file_holders.who_has(path)]
            self.assertIn(os.getpid(), pids)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
