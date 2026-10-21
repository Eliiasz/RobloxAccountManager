import os
import subprocess
import tempfile
import unittest
from unittest import mock

from classes.operation_result import OperationResult
from classes.roblox_api import RobloxAPI
from features import account_actions as actions
from features import roblox_install_guard as guard


class DirectArgsTests(unittest.TestCase):
    def test_game_launch_args(self):
        args = guard.direct_args("TICKET", 1700, "555", "https://x/PlaceLauncher.ashx?placeId=1")
        self.assertEqual(args[:5], ["--app", "-t", "TICKET", "-j", "https://x/PlaceLauncher.ashx?placeId=1"])
        self.assertIn("-b", args)
        self.assertEqual(args[args.index("-b") + 1], "555")
        self.assertIn("--launchtime=1700", args)

    def test_home_launch_has_no_place_url(self):
        self.assertNotIn("-j", guard.direct_args("T", 1, "2"))

    def test_launch_sends_matching_direct_args(self):
        with mock.patch.object(RobloxAPI, "get_auth_ticket", return_value=OperationResult.success(data="TICKET")), \
                mock.patch.object(RobloxAPI, "_execute_launch", return_value=OperationResult.success()) as run, \
                mock.patch.object(guard, "watch_error_windows"):
            RobloxAPI.launch_roblox("alice", "cookie", game_id="1818")
        url, _pref, _custom, direct = run.call_args.args
        place_url = direct[direct.index("-j") + 1]
        self.assertIn("+placelauncherurl:" + place_url + "+browsertrackerid:", url)
        self.assertIn("&placeId=1818&", place_url)


class ExecuteLaunchTests(unittest.TestCase):
    def _run(self, enabled, started):
        status = guard.DIRECT_STARTED if started else guard.DIRECT_UNAVAILABLE
        if started == "failed":
            status = guard.DIRECT_FAILED
        with mock.patch.object(guard, "enabled", return_value=enabled), \
                mock.patch.object(guard, "start_direct", return_value=(status, started or "")) as direct, \
                mock.patch("classes.roblox_api.os.startfile", create=True) as startfile:
            result = RobloxAPI._execute_launch("roblox-player:1", "default", "", ["--app"])
        return result, direct, startfile

    def test_open_roblox_is_used_directly(self):
        result, direct, startfile = self._run(True, r"C:\Roblox\Versions\version-1\RobloxPlayerBeta.exe")
        self.assertTrue(result)
        direct.assert_called_once_with(["--app"])
        startfile.assert_not_called()

    def test_falls_back_to_the_normal_launcher(self):
        result, _direct, startfile = self._run(True, "")
        self.assertTrue(result)
        startfile.assert_called_once_with("roblox-player:1")

    def test_failed_direct_start_never_launches_twice(self):
        result, _direct, startfile = self._run(True, "failed")
        self.assertFalse(result)
        self.assertEqual(result.code, "ROBLOX_LAUNCH_FAILED")
        startfile.assert_not_called()

    def test_off_uses_the_normal_launcher(self):
        _result, direct, startfile = self._run(False, "x")
        direct.assert_not_called()
        startfile.assert_called_once()


class StartDirectTests(unittest.TestCase):
    def setUp(self):
        guard._direct_failures = 0

    def _start(self, wait):
        process = mock.Mock()
        if isinstance(wait, Exception):
            process.wait.side_effect = wait
        else:
            process.wait.return_value = wait
        with mock.patch.object(guard, "running_client_exe", return_value=__file__), \
                mock.patch.object(guard.subprocess, "Popen", return_value=process) as popen:
            return guard.start_direct(["--app", "-t", "T"]), popen

    def test_no_open_roblox_means_normal_launcher(self):
        with mock.patch.object(guard, "running_client_exe", return_value=""):
            self.assertEqual(guard.start_direct(["--app"])[0], guard.DIRECT_UNAVAILABLE)

    def test_still_running_means_started(self):
        (status, exe), popen = self._start(subprocess.TimeoutExpired("x", 3))
        self.assertEqual((status, exe), (guard.DIRECT_STARTED, __file__))
        self.assertEqual(popen.call_args.args[0], [__file__, "--app", "-t", "T"])

    def test_handing_off_with_exit_code_zero_is_started(self):
        # Roblox often hands off to a new process; that used to cause a second launch.
        self.assertEqual(self._start(0)[0][0], guard.DIRECT_STARTED)

    def test_error_exit_fails_and_repeated_failures_switch_direct_off(self):
        self.assertEqual(self._start(1)[0][0], guard.DIRECT_FAILED)
        self.assertEqual(self._start(1)[0][0], guard.DIRECT_FAILED)
        self.assertEqual(self._start(1)[0][0], guard.DIRECT_UNAVAILABLE)


class UpdateCheckTests(unittest.TestCase):
    def test_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "Roblox", "Versions", "version-abc")
            os.makedirs(target)
            open(os.path.join(target, "RobloxPlayerBeta.exe"), "w").close()
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                self.assertEqual(guard.installed_version(), "version-abc")
                with mock.patch.object(guard, "latest_version", return_value="version-new"):
                    self.assertTrue(guard.update_pending())
                with mock.patch.object(guard, "latest_version", return_value="version-abc"):
                    self.assertFalse(guard.update_pending())

    def test_not_installed_makes_no_request(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": folder}), \
                mock.patch.object(guard, "latest_version") as latest:
            self.assertFalse(guard.update_pending())
        latest.assert_not_called()


class UpdateFirstBatchTests(unittest.TestCase):
    def _batch(self, pending, running, delay):
        order = []

        def launch_one(user, parallel):
            order.append((user, parallel))
            return OperationResult.success()

        with mock.patch.object(guard, "enabled", return_value=True), \
                mock.patch.object(guard, "update_pending", return_value=pending), \
                mock.patch.object(actions.presence_mod, "get_roblox_processes", return_value=dict.fromkeys(range(running))), \
                mock.patch.object(actions, "_wait_for_first_client", return_value=True) as wait, \
                mock.patch.object(actions, "load_ui_settings", return_value={}), \
                mock.patch.object(actions.time, "sleep"):
            actions._run_launch_batch(["a", "b", "c"], launch_one, delay, "test")
        return order, wait

    def test_pending_update_opens_first_alone(self):
        order, wait = self._batch(True, 0, 0)
        self.assertEqual(order[0], ("a", False))
        wait.assert_called_once()

    def test_pending_update_with_delay_waits_for_first(self):
        _order, wait = self._batch(True, 0, 2)
        wait.assert_called_once()

    def test_no_update_launches_together(self):
        order, wait = self._batch(False, 0, 0)
        wait.assert_not_called()
        self.assertTrue(all(parallel for _u, parallel in order))

    def test_update_while_roblox_open_does_not_wait(self):
        _order, wait = self._batch(True, 2, 0)
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
