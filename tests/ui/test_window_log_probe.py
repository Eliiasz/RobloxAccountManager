import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from features import window_log_probe as probe


class IsolatedScanHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    def test_native_scan_holding_child_gil_cannot_freeze_qt(self):
        # PyDLL deliberately retains the child's GIL during Sleep. A scan run
        # inside the UI process would prevent these Python Qt callbacks running.
        def command(folder):
            return [sys.executable, "-c",
                    "import ctypes, pathlib, sys; pathlib.Path(sys.argv[1], 'ready').write_text('1'); "
                    "ctypes.PyDLL('kernel32').Sleep(15000)", folder]
        helper = probe._Helper()
        self.addCleanup(helper.close)
        ticks = []
        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        results = []
        processes = []
        done = threading.Event()

        def scan():
            try:
                results.append(helper.probe((123, 100.0), threading.Event(), timeout=1.0))
            finally:
                done.set()

        real_reset = helper._reset

        def remember_process():
            if helper._process is not None:
                processes.append(helper._process)
            real_reset()

        with patch.object(probe, "_helper_command", side_effect=command), \
                patch.object(helper, "_reset", side_effect=remember_process):
            timer.start()
            worker = threading.Thread(target=scan)
            worker.start()
            deadline = time.monotonic() + 8
            while not done.is_set() and time.monotonic() < deadline:
                QTest.qWait(20)
            timer.stop()
            worker.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(results[0].status, "timeout")
        self.assertGreaterEqual(len(ticks), 15)
        self.assertLess(max(b - a for a, b in zip(ticks, ticks[1:])), 0.35)
        self.assertTrue(processes)
        self.assertIsNotNone(processes[-1].poll())

    def test_real_early_entry_does_not_initialize_app_data_or_ui(self):
        import psutil
        with tempfile.TemporaryDirectory() as folder:
            data = str(Path(folder) / "must-not-be-created")
            helper = probe._Helper()
            self.addCleanup(helper.close)
            key = (os.getpid(), psutil.Process().create_time())
            with patch.dict(os.environ, {"RAM_DATA_DIR": data}):
                result = helper.probe(key, threading.Event(), timeout=10)
            self.assertEqual(result.status, "process_changed")
            self.assertFalse(Path(data).exists())


if __name__ == "__main__":
    unittest.main()
