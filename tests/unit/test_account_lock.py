from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from features import account_actions as actions


def _manager(accounts):
    return SimpleNamespace(accounts=accounts, save_accounts=Mock())


def _process(pid, name):
    process = MagicMock()
    process.pid = pid
    process.info = {"pid": pid, "name": name}
    return process


class AccountLockTests(unittest.TestCase):
    def test_lock_and_unlock_are_saved(self):
        manager = _manager({"a": {"cookie": "x"}, "b": {"cookie": "y"}})
        self.assertEqual(actions.set_accounts_locked(manager, ["a", "b", "missing"], True), 2)
        self.assertEqual(actions.locked_usernames(manager), {"a", "b"})
        manager.save_accounts.assert_called_once_with()
        self.assertEqual(actions.set_accounts_locked(manager, ["a"], False), 1)
        self.assertEqual(actions.locked_usernames(manager), {"b"})
        self.assertNotIn(actions.LOCK_KEY, manager.accounts["a"])

    def test_locking_again_changes_nothing(self):
        manager = _manager({"a": {"locked": True}})
        self.assertEqual(actions.set_accounts_locked(manager, ["a"], True), 0)
        manager.save_accounts.assert_not_called()

    def test_no_locked_accounts_skips_the_lookup(self):
        with patch.object(actions.presence_mod, "get_roblox_processes") as lookup:
            self.assertEqual(actions.locked_roblox_pids(_manager({"a": {}})), set())
        lookup.assert_not_called()


class KillAllKeepsLockedTests(unittest.TestCase):
    def test_kept_clients_are_not_closed(self):
        kept, other = _process(10, "RobloxPlayerBeta.exe"), _process(20, "RobloxPlayerBeta.exe")
        with patch.object(actions.psutil, "process_iter", return_value=[kept, other]), \
                patch.object(actions.psutil, "wait_procs", return_value=([other], [])), \
                patch.object(actions.presence_mod, "get_roblox_processes"):
            result = actions.kill_all_roblox_fast(keep_pids={10})
        self.assertTrue(result)
        kept.kill.assert_not_called()
        other.kill.assert_called_once_with()
        self.assertEqual(result.data["games"], 1)
        self.assertEqual(result.data["kept"], 1)

    def test_survivors_are_closed_by_pid_when_something_is_locked(self):
        stubborn = _process(20, "RobloxPlayerBeta.exe")
        with patch.object(actions.psutil, "process_iter", return_value=[stubborn]), \
                patch.object(actions.psutil, "wait_procs", return_value=([], [stubborn])), \
                patch.object(actions, "_still_running", return_value=False), \
                patch.object(actions.subprocess, "run") as run, \
                patch.object(actions.presence_mod, "get_roblox_processes"):
            actions.kill_all_roblox_fast(keep_pids={10})
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [["taskkill", "/F", "/T", "/PID", "20"]])


if __name__ == "__main__":
    unittest.main()
