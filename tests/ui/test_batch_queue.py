import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication

from utils import ui

Q = ui.AccountManagerUIQt


def _window(size=2):
    QApplication.instance() or QApplication([])
    sent = []
    w = SimpleNamespace(
        _queue_enabled_chk=SimpleNamespace(isChecked=lambda: True),
        _queue_size_edit=SimpleNamespace(text=lambda: str(size)),
        _update_queue_status=MagicMock(),
        _ensure_queue_timer=MagicMock(),
        _poll_queue=MagicMock(),
        _on_kill_all_roblox=MagicMock(),
        _launch_queue=None,
        _QUEUE_START_TIMEOUT=Q._QUEUE_START_TIMEOUT,
        sent=sent,
    )
    for name in ("_queue_size", "_queue_or_all", "_on_queue_poll", "_launch_next_batch",
                 "_finish_queue", "_on_queue_pause_clicked", "_on_queue_next_clicked"):
        setattr(w, name, (lambda f: lambda *a: f(w, *a))(getattr(Q, name)))
    first = w._queue_or_all(["a", "b", "c", "d", "e"], lambda batch: sent.append(batch))
    return w, first


class BatchQueueTests(unittest.TestCase):
    def test_first_batch_then_auto_next_when_all_closed(self):
        w, first = _window()
        self.assertEqual(first, ["a", "b"])
        w._on_queue_poll(2)          # clients appeared
        w._on_queue_poll(0)          # first empty poll is ignored
        self.assertEqual(w.sent, [])
        w._on_queue_poll(0)          # still empty: batch closed -> next opens
        self.assertEqual(w.sent, [["c", "d"]])
        w._on_queue_poll(1)
        w._on_queue_poll(0)
        w._on_queue_poll(0)
        self.assertEqual(w.sent, [["c", "d"], ["e"]])
        w._on_queue_poll(1)
        w._on_queue_poll(0)
        w._on_queue_poll(0)
        self.assertIsNone(w._launch_queue)   # finished

    def test_pause_holds_next_batch_until_resume(self):
        w, _ = _window()
        w._on_queue_poll(2)
        w._on_queue_pause_clicked()
        w._on_queue_poll(0)
        w._on_queue_poll(0)
        w._on_queue_poll(0)
        self.assertEqual(w.sent, [])
        w._on_queue_pause_clicked()          # resume
        w._on_queue_poll(0)
        self.assertEqual(w.sent, [["c", "d"]])

    def test_next_batch_works_even_when_paused(self):
        w, _ = _window()
        w._on_queue_poll(2)
        w._on_queue_pause_clicked()
        w._on_queue_next_clicked()
        w._on_kill_all_roblox.assert_called_once()
        w._on_queue_poll(0)
        self.assertEqual(w.sent, [["c", "d"]])

    def test_pressing_next_batch_twice_only_opens_one_batch(self):
        w, _ = _window()
        w._on_queue_poll(2)
        w._on_queue_next_clicked()
        w._on_queue_next_clicked()
        w._on_kill_all_roblox.assert_called_once()
        w._on_queue_poll(0)
        self.assertEqual(w.sent, [["c", "d"]])

    def test_small_selection_skips_queue(self):
        w, _ = _window(size=10)
        self.assertIsNone(w._launch_queue)


if __name__ == "__main__":
    unittest.main()
