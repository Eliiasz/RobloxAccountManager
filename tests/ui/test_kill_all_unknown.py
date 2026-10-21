from types import SimpleNamespace
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QPushButton

from classes.operation_result import OperationResult
from utils import ui


def _window():
    btn = QPushButton("  Kill All Roblox")
    return SimpleNamespace(
        _kill_roblox_btn=btn, _kill_in_progress=False, manager=MagicMock(),
        _bridge=SimpleNamespace(kill_roblox_done=SimpleNamespace(emit=MagicMock())),
    )


class _SyncThread:
    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()


class KillAllUnknownTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])

    def _press(self, w, locked, unknown):
        with patch.object(ui.actions, "locked_and_unknown_pids", return_value=(set(locked), set(unknown))), \
                patch.object(ui.actions, "kill_all_roblox_fast",
                             return_value=OperationResult.success(data={"games": 3, "kept": 0})) as kill, \
                patch.object(ui.threading, "Thread", _SyncThread):
            ui.AccountManagerUIQt._on_kill_all_roblox(w)
        result = w._bridge.kill_roblox_done.emit.call_args[0][0]
        ui.AccountManagerUIQt._on_kill_roblox_done(w, result)
        return kill.call_args.kwargs["keep_pids"], result

    def test_unknown_windows_are_kept_then_closed_on_second_press(self):
        w = _window()
        keep, result = self._press(w, {1}, {2, 3})
        self.assertEqual(keep, {1, 2, 3})
        self.assertEqual(result.data["unknown"], 2)
        self.assertIn("2 unknown", w._kill_roblox_btn.text())
        keep, _ = self._press(w, {1}, {2, 3})
        self.assertEqual(keep, {1})  # locked still kept, unknown closed

    def test_second_press_after_timeout_keeps_unknown_again(self):
        w = _window()
        self._press(w, {1}, {2})
        w._kill_unknown_until = time.monotonic() - 1
        keep, _ = self._press(w, {1}, {2})
        self.assertEqual(keep, {1, 2})


    def test_kill_all_pauses_queue_and_auto_rejoin(self):
        w = _window()
        w._launch_queue = {"paused": False, "force": False, "pause_reason": ""}
        w._poll_queue = MagicMock()
        with patch.object(ui.ar, "pause_all") as pause:
            self._press(w, set(), set())
        pause.assert_called_once()
        self.assertTrue(w._launch_queue["paused"])
        self.assertIn("Kill All", w._launch_queue["pause_reason"])

    def test_next_batch_is_not_paused(self):
        w = _window()
        w._launch_queue = {"paused": False, "force": True, "pause_reason": ""}
        w._poll_queue = MagicMock()
        with patch.object(ui.ar, "pause_all"):
            self._press(w, set(), set())
        self.assertFalse(w._launch_queue["paused"])


if __name__ == "__main__":
    unittest.main()
