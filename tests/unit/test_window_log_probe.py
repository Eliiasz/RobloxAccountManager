import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil

from features import window_log_probe as probe


class ProbeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.result = str(Path(self.directory) / "result.json")
        self.key = (123, 100.0)

    def read(self, payload):
        Path(self.result).write_text(json.dumps(payload), encoding="utf-8")
        return probe._read_result(self.result, self.key)

    def test_success_is_tied_to_pid_and_creation_time(self):
        path = str(Path(self.directory) / "player_last.log")
        result = self.read(dict(status="ok", pid=123, create_time=100.0, paths=[path]))
        self.assertEqual(result, probe.ProbeResult("ok", frozenset({os.path.normcase(path)})))
        self.assertEqual(self.read(dict(status="ok", pid=123, create_time=200.0,
                                        paths=[path])).status, "error")

    def test_rejects_invalid_payloads_and_paths(self):
        for payload in ([], {}, dict(status="ok", pid=123, create_time=100.0,
                                      paths=["relative_last.log"]),
                        dict(status="ok", pid=123, create_time=100.0,
                             paths=[str(Path(self.directory) / "other.txt")])):
            with self.subTest(payload=payload):
                self.assertEqual(self.read(payload).status, "error")

    def test_rejects_oversized_output(self):
        Path(self.result).write_bytes(b" " * (probe._MAX_RESULT_BYTES + 1))
        self.assertEqual(probe._read_result(self.result, self.key).status, "error")

    def test_helper_rechecks_pid_after_scan(self):
        old = MagicMock()
        old.create_time.return_value = 100.0
        old.name.return_value = "RobloxPlayerBeta.exe"
        old.open_files.return_value = [SimpleNamespace(path=str(Path(self.directory) / "player_last.log"))]
        reused = MagicMock()
        reused.create_time.return_value = 200.0
        with patch.object(psutil, "Process", side_effect=[old, reused]):
            self.assertEqual(probe._scan(123, 100.0)["status"], "process_changed")

    def test_wrong_process_is_not_scanned(self):
        process = MagicMock()
        process.create_time.return_value = 100.0
        process.name.return_value = "other.exe"
        with patch.object(psutil, "Process", return_value=process):
            self.assertEqual(probe._scan(123, 100.0)["status"], "process_changed")
        process.open_files.assert_not_called()

    def test_frozen_command_reuses_executable(self):
        with patch.object(probe.sys, "frozen", True, create=True):
            command = probe._helper_command(self.directory)
        self.assertEqual(command[:3], [probe.sys.executable, probe.HELPER_OPTION, self.directory])
        self.assertEqual(command[3], str(os.getpid()))


class HelperLoopTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        me = psutil.Process()
        self.args = [str(self.folder), str(me.pid), repr(me.create_time())]

    def run_helper(self, args=None):
        codes = []
        worker = threading.Thread(target=lambda: codes.append(probe.helper_main(args or self.args)))
        worker.start()
        return worker, codes

    def test_serves_requests_without_stdout_until_asked_to_stop(self):
        probe._write_json(self.folder / "req-1.json", {"pid": 123, "create_time": 100.0})
        with patch.object(probe, "_scan", return_value={"status": "process_changed"}), \
                patch("sys.stdout", None):
            worker, codes = self.run_helper()
            deadline = time.monotonic() + 5
            while not (self.folder / "res-1.json").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            (self.folder / "stop").write_bytes(b"")
            worker.join(5)
        self.assertEqual(codes, [0])
        self.assertTrue((self.folder / "ready").exists())
        self.assertFalse((self.folder / "req-1.json").exists())
        self.assertEqual(json.loads((self.folder / "res-1.json").read_text())["status"], "process_changed")

    def test_helper_ends_when_the_app_is_gone(self):
        args = [str(self.folder), self.args[1], "1.0"]  # same PID, different start time
        worker, codes = self.run_helper(args)
        worker.join(5)
        self.assertEqual(codes, [0])

    def test_bad_arguments_cannot_start_ui(self):
        self.assertEqual(probe.helper_main([]), 2)
        self.assertEqual(probe.helper_main([str(self.folder), "123", "nan"]), 2)
        self.assertEqual(probe.helper_main([str(self.folder / "missing"), "123", "1.0"]), 2)


def sleeper_command(folder):
    # Starts like the helper, then hangs the way a stuck native scan does.
    return [sys.executable, "-c",
            "import pathlib, sys, time; pathlib.Path(sys.argv[1], 'ready').write_text('1'); time.sleep(60)",
            folder]


class HelperLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.helper = probe._Helper()
        self.addCleanup(self.helper.close)

    def test_one_helper_serves_every_scan_and_closes_with_the_app(self):
        key = (os.getpid(), psutil.Process().create_time())  # not Roblox: process_changed
        first = self.helper.probe(key, threading.Event(), timeout=10)
        process = self.helper._process
        second = self.helper.probe(key, threading.Event(), timeout=10)
        self.assertEqual((first.status, second.status), ("process_changed", "process_changed"))
        self.assertIs(self.helper._process, process)
        folder = self.helper._folder
        self.helper.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(folder.exists())

    def test_a_hung_helper_is_stopped_and_replaced(self):
        with patch.object(probe, "_helper_command", side_effect=sleeper_command):
            result = self.helper.probe((123, 100.0), threading.Event(), timeout=0.2)
            self.assertEqual(result.status, "timeout")
            self.assertIsNone(self.helper._process)

    def test_cancelled_scan_does_not_start_a_process(self):
        stop = threading.Event()
        stop.set()
        with patch.object(probe.subprocess, "Popen") as start:
            result = probe.probe_open_log_paths((123, 100.0), stop)
        self.assertEqual(result.status, "cancelled")
        start.assert_not_called()

    def test_a_cancelled_scan_keeps_the_helper(self):
        stop = threading.Event()
        with patch.object(probe, "_helper_command", side_effect=sleeper_command):
            threading.Timer(0.3, stop.set).start()
            result = self.helper.probe((123, 100.0), stop, timeout=10)
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(self.helper._process.poll())


class EndProcessTests(unittest.TestCase):
    def test_the_unpacked_app_is_ended_so_the_starter_can_clean_up(self):
        starter = MagicMock(pid=1)
        starter.poll.return_value = None
        child = MagicMock()
        with patch.object(psutil, "Process") as process:
            process.return_value.children.return_value = [child]
            probe._end_process(starter)
        child.kill.assert_called_once()
        starter.kill.assert_not_called()  # the starter deletes its Temp folder and exits
        starter.wait.assert_called_once_with(timeout=probe.STOP_TIMEOUT_SECONDS)

    def test_a_starter_that_does_not_exit_is_killed(self):
        starter = MagicMock(pid=1)
        starter.poll.return_value = None
        starter.wait.side_effect = [subprocess.TimeoutExpired("x", 5), 0]
        with patch.object(psutil, "Process") as process:
            process.return_value.children.return_value = [MagicMock()]
            probe._end_process(starter)
        starter.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
