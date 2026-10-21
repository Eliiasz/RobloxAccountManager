from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from features import account_actions as actions
from features import roblox_identity as identity
from features.presence import RobloxLogEntry


def _proc(cmdline=""):
    process = MagicMock()
    process.cmdline.return_value = cmdline.split() if cmdline else []
    return process


def _utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _entry(ts, path, user_id, tracker=""):
    return RobloxLogEntry(timestamp=_utc(ts), path=path, user_id=user_id, browser_tracker_id=tracker)


def _manager(accounts, records=None):
    return SimpleNamespace(accounts=accounts, get_launch_records=lambda: dict(records or {}))


ACCOUNTS = {f"acc{i}": {"user_id": str(100 + i)} for i in range(6)}
ACCOUNTS["acc3"]["locked"] = True


class IdentityTests(unittest.TestCase):
    def setUp(self):
        identity.clear_cache()

    def _run(self, manager, processes, entries=(), probe=None):
        probe = probe or (lambda key, stop: SimpleNamespace(status="timeout", paths=frozenset()))
        with patch.object(identity.presence_mod, "get_roblox_processes", return_value=processes), \
                patch.object(identity.presence_mod, "get_roblox_log_entries", return_value=list(entries)), \
                patch.object(identity, "_probe", side_effect=probe) as probe_mock:
            result = actions.locked_and_unknown_pids(manager, quiet=True)
        return result, probe_mock

    def test_many_windows_at_once_keep_the_locked_one(self):
        # Six windows started within the same second: timing alone cannot tell them apart.
        records = {str(9000 + i): (str(100 + i), 1000.0) for i in range(6)}
        processes = {
            50 + i: (1000.2 + i * 0.05, _proc(f"RobloxPlayerBeta.exe --app -t x -b {9000 + i}"))
            for i in range(6)
        }
        (locked, unknown), _ = self._run(_manager(ACCOUNTS, records), processes)
        self.assertEqual(locked, {53})
        self.assertEqual(unknown, set())

    def test_launch_id_in_log_file_is_used_when_not_launched_by_this_session(self):
        processes = {7: (1000.0, _proc("x browserTrackerId=4242")), 8: (1000.1, _proc("x -b 4343"))}
        entries = [_entry(1000.5, "a.log", "103", "4242"), _entry(1000.6, "b.log", "101", "4343")]
        (locked, unknown), _ = self._run(_manager(ACCOUNTS), processes, entries)
        self.assertEqual((locked, unknown), ({7}, set()))

    def test_open_log_file_identifies_a_window(self):
        processes = {7: (1000.0, _proc()), 8: (1000.1, _proc())}
        entries = [_entry(1000.5, "C:/logs/a.log", "103"), _entry(1000.6, "C:/logs/b.log", "101")]
        open_files = {7: frozenset({"C:/logs/a.log"}), 8: frozenset({"C:/logs/b.log"})}
        probe = lambda key, stop: SimpleNamespace(status="ok", paths=open_files[key[0]])
        (locked, unknown), probe_mock = self._run(_manager(ACCOUNTS), processes, entries, probe)
        self.assertEqual((locked, unknown), ({7}, set()))
        # Certain matches are remembered, so the next check needs no helper.
        (locked, unknown), probe_mock = self._run(_manager(ACCOUNTS), processes, entries, probe)
        self.assertEqual((locked, unknown), ({7}, set()))
        probe_mock.assert_not_called()

    def test_timing_guess_is_refused_when_ambiguous(self):
        # The old code picked the nearest log and could hand the locked log to another window.
        processes = {7: (1000.0, _proc()), 8: (1000.1, _proc())}
        entries = [_entry(1000.5, "a.log", "101"), _entry(1000.6, "b.log", "103")]
        (locked, unknown), _ = self._run(_manager(ACCOUNTS), processes, entries)
        self.assertEqual(locked, set())
        self.assertEqual(unknown, {7, 8})

    def test_single_leftover_window_uses_timing(self):
        records = {"9000": ("101", 1000.0)}
        processes = {7: (1000.0, _proc("x -b 9000")), 8: (1005.0, _proc())}
        entries = [_entry(1006.0, "b.log", "103")]
        (locked, unknown), _ = self._run(_manager(ACCOUNTS, records), processes, entries)
        self.assertEqual((locked, unknown), ({8}, set()))

    def test_duplicate_launch_ids_are_not_trusted(self):
        records = {"9000": ("103", 1000.0)}
        processes = {7: (1000.0, _proc("x -b 9000")), 8: (1000.1, _proc("x -b 9000"))}
        (locked, unknown), _ = self._run(_manager(ACCOUNTS, records), processes)
        self.assertEqual(locked, set())
        self.assertEqual(unknown, {7, 8})

    def test_no_locked_accounts_skips_everything(self):
        with patch.object(identity, "identify_clients") as lookup:
            self.assertEqual(actions.locked_and_unknown_pids(_manager({"a": {}})), (set(), set()))
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
