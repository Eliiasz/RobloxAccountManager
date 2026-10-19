from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from classes.operation_result import OperationResult
from features import account_actions as actions
from utils import ui


def _window(queue):
    window = SimpleNamespace(
        _launch_queue=queue,
        _update_queue_status=Mock(),
        _poll_queue=Mock(),
    )
    window._note_queue_failures = ui.AccountManagerUIQt._note_queue_failures.__get__(window)
    return window


def _queue():
    return {"failed": [], "phase": "starting", "phase_since": time.monotonic(), "empty_polls": 0}


class QueueFailedAccountsTests(unittest.TestCase):
    def failure(self, failed, batch):
        return OperationResult(False, code="ROBLOX_LAUNCH_FAILED", message="x",
                               data={"failed": failed, "batch": batch})

    def test_failed_accounts_are_collected_without_a_popup_when_enabled(self):
        window = _window(_queue())
        with patch.object(ui.actions, "load_ui_settings", return_value={"queue_select_failed": True}):
            self.assertTrue(window._note_queue_failures(self.failure(["a", "b"], ["a", "b", "c"])))
            self.assertTrue(window._note_queue_failures(self.failure(["b", "d"], ["d", "e"])))
        self.assertEqual(window._launch_queue["failed"], ["a", "b", "d"])
        self.assertEqual(window._launch_queue["phase"], "starting")

    def test_a_batch_that_fully_failed_moves_the_queue_on(self):
        window = _window(_queue())
        with patch.object(ui.actions, "load_ui_settings", return_value={"queue_select_failed": True}):
            window._note_queue_failures(self.failure(["a", "b"], ["a", "b"]))
        self.assertEqual(window._launch_queue["phase"], "waiting")
        window._poll_queue.assert_called_once_with()

    def test_nothing_changes_when_the_option_is_off_or_no_queue_runs(self):
        with patch.object(ui.actions, "load_ui_settings", return_value={}):
            self.assertFalse(_window(_queue())._note_queue_failures(self.failure(["a"], ["a"])))
        with patch.object(ui.actions, "load_ui_settings", return_value={"queue_select_failed": True}):
            self.assertFalse(_window(None)._note_queue_failures(self.failure(["a"], ["a"])))


class BatchResultFailedListTests(unittest.TestCase):
    def test_batch_result_lists_the_accounts_that_failed(self):
        bad = OperationResult.failure("ROBLOX_LAUNCH_UNCONFIRMED", "t", "m")
        partial = actions._batch_launch_result("Joined", 3, 1, [("a", bad), ("c", bad)])
        self.assertFalse(partial)
        self.assertEqual(partial.data["failed"], ["a", "c"])
        self.assertIn(actions.LAUNCH_NOT_OPEN_MESSAGE, partial.detail)
        total = actions._batch_launch_result("Joined", 2, 0, [("a", bad), ("b", bad)])
        self.assertEqual(total.data["failed"], ["a", "b"])
        self.assertEqual(total.message, actions.LAUNCH_NOT_OPEN_MESSAGE)


class FirstAloneLaunchTests(unittest.TestCase):
    def test_first_account_starts_alone_then_the_rest_together(self):
        order = []

        def launch_one(account, parallel):
            order.append((account, parallel))
            return OperationResult.success()

        with patch.object(actions, "_wait_for_first_client", return_value=True) as wait, \
                patch.object(actions.presence_mod, "get_roblox_processes", return_value={}):
            success, failures, _ = actions._run_launch_batch(["a", "b", "c"], launch_one, 0, "test", first_alone=True)
        self.assertEqual(success, 3)
        self.assertEqual(order[0], ("a", False))
        self.assertEqual(sorted(order[1:]), [("b", True), ("c", True)])
        wait.assert_called_once_with(0)

    def test_if_the_first_fails_the_rest_still_start(self):
        calls = []

        def launch_one(account, parallel):
            calls.append(account)
            return OperationResult.failure("ROBLOX_LAUNCH_FAILED", "t", "m") if account == "a" else OperationResult.success()

        with patch.object(actions, "_wait_for_first_client") as wait, \
                patch.object(actions.presence_mod, "get_roblox_processes", return_value={}):
            success, failures, _ = actions._run_launch_batch(["a", "b"], launch_one, 0, "test", first_alone=True)
        self.assertEqual(success, 1)
        self.assertEqual([name for name, _ in failures], ["a"])
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class EveryJoinUsesTheQueueTests(unittest.TestCase):
    def test_send_with_queue_sends_only_the_first_batch_now(self):
        sent = []
        window = SimpleNamespace(_queue_or_all=lambda names, send: names[:5])
        ui.AccountManagerUIQt._send_with_queue(window, [f"a{i}" for i in range(20)], sent.append)
        self.assertEqual(sent, [[f"a{i}" for i in range(5)]])

    def test_join_user_box_goes_through_the_queue(self):
        window = SimpleNamespace(
            _join_target_edit=SimpleNamespace(currentText=lambda: "target"),
            _get_selected_usernames=lambda: ["a", "b", "c"],
            _guard_invalid=lambda names: True,
            _confirm_launch=lambda label, names: True,
            _remember_join_target=Mock(),
            _send_with_queue=Mock(),
        )
        ui.AccountManagerUIQt._on_join_target(window)
        window._send_with_queue.assert_called_once()
        self.assertEqual(window._send_with_queue.call_args.args[0], ["a", "b", "c"])


class GroupBulkMoveTests(unittest.TestCase):
    def test_moving_many_accounts_saves_once(self):
        from features import groups
        with patch.object(groups, "_load", return_value={"assignments": {"a": "old"}}), \
                patch.object(groups, "_save") as save:
            groups.set_accounts_group(["a", "b"], "new")
            saved = save.call_args.args[0]
            self.assertEqual(saved["assignments"], {"a": "new", "b": "new"})
            groups.set_accounts_group(["a"], None)
        self.assertEqual(save.call_count, 2)
