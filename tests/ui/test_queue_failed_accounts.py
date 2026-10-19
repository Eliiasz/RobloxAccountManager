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
        QUEUE_RETRY_SETTING=ui.AccountManagerUIQt.QUEUE_RETRY_SETTING,
        _QUEUE_COOLDOWN=ui.AccountManagerUIQt._QUEUE_COOLDOWN,
    )
    window._queue_retry_limit = ui.AccountManagerUIQt._queue_retry_limit.__get__(window)
    window._note_queue_failures = ui.AccountManagerUIQt._note_queue_failures.__get__(window)
    return window


def _queue(remaining=None):
    return {"failed": [], "security": [], "attempts": {}, "ever_failed": set(), "remaining": list(remaining or []),
            "size": 2, "batch": 1, "batches": 3, "paused": False, "phase": "starting",
            "phase_since": time.monotonic(), "empty_polls": 0}


class QueueFailedAccountsTests(unittest.TestCase):
    def failure(self, failed, batch, codes):
        return OperationResult(False, code="PARTIAL_LAUNCH_FAILURE", message="x",
                               data={"failed": failed, "batch": batch, "failed_codes": codes})

    def test_installer_failures_are_retried_at_the_end_and_security_checks_are_not(self):
        window = _window(_queue(remaining=["e"]))
        with patch.object(ui.actions, "get_ui_setting", return_value=2):
            window._note_queue_failures(self.failure(
                ["a", "b"], ["a", "b", "c"],
                {"a": "ROBLOX_LAUNCH_FAILED", "b": actions.SECURITY_CHECK_CODE}))
        q = window._launch_queue
        self.assertEqual(q["remaining"], ["e", "a"])
        self.assertEqual(q["security"], ["b"])
        self.assertEqual(q["failed"], [])
        self.assertEqual(q["phase"], "starting")

    def test_retries_stop_after_the_limit(self):
        window = _window(_queue())
        with patch.object(ui.actions, "get_ui_setting", return_value=1):
            for _ in range(2):
                window._launch_queue["phase"] = "running"
                window._note_queue_failures(self.failure(["a"], ["a", "b"], {"a": "ROBLOX_CLIENT_EXITED"}))
        self.assertEqual(window._launch_queue["remaining"], ["a"])
        self.assertEqual(window._launch_queue["failed"], ["a"])

    def test_a_batch_that_fully_failed_with_roblox_trouble_waits_then_continues(self):
        window = _window(_queue())
        with patch.object(ui.actions, "get_ui_setting", return_value=2):
            window._note_queue_failures(self.failure(["a", "b"], ["a", "b"],
                                                     {"a": "ROBLOX_LAUNCH_FAILED", "b": "NETWORK_TIMEOUT"}))
        q = window._launch_queue
        self.assertEqual(q["phase"], "waiting")
        self.assertFalse(q["paused"])
        self.assertGreater(q["cooldown_until"], time.monotonic())

    def test_a_batch_that_fully_failed_for_another_reason_pauses_instead_of_racing(self):
        window = _window(_queue())
        with patch.object(ui.actions, "get_ui_setting", return_value=2):
            window._note_queue_failures(self.failure(["a", "b"], ["a", "b"],
                                                     {"a": "LAUNCH_ACCOUNT_ID_MISSING", "b": "LAUNCH_ACCOUNT_ID_MISSING"}))
        q = window._launch_queue
        self.assertTrue(q["paused"])
        self.assertIn("failed right away", q["pause_reason"])

    def test_nothing_is_collected_when_no_queue_runs(self):
        self.assertFalse(_window(None)._note_queue_failures(self.failure(["a"], ["a"], {})))


class BatchResultFailedListTests(unittest.TestCase):
    def test_batch_result_lists_the_accounts_that_failed(self):
        bad = OperationResult.failure("ROBLOX_LAUNCH_UNCONFIRMED", "t", "m")
        partial = actions._batch_launch_result("Joined", 3, 1, [("a", bad), ("c", bad)])
        self.assertFalse(partial)
        self.assertEqual(partial.data["failed"], ["a", "c"])
        self.assertEqual(partial.data["failed_codes"], {"a": "ROBLOX_LAUNCH_UNCONFIRMED", "c": "ROBLOX_LAUNCH_UNCONFIRMED"})
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
