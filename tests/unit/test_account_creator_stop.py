import threading
import unittest
from unittest.mock import MagicMock, patch

from classes.operation_result import OperationResult
from features import account_creator


class CreatorStopTests(unittest.TestCase):
    def run_creator(self, stop_after: int | None):
        manager = MagicMock()
        manager.accounts = {}
        stop = threading.Event()
        created = []

        def add_account(**_kwargs):
            name = f"user{len(created)}"
            created.append(name)
            manager.accounts[name] = {}
            if stop_after is not None and len(created) >= stop_after:
                stop.set()
            return OperationResult.success()

        manager.add_account.side_effect = add_account
        finished = threading.Event()
        outcome = {}
        progress = []

        def done(success, message, report=None):
            outcome.update(success=success, message=message, report=report)
            finished.set()

        browser = OperationResult.success(data={"browser": "chrome"})
        with patch.object(account_creator, "get_browser_result", return_value=browser), \
                patch.object(account_creator, "MAX_CREATOR_BROWSERS", 1):
            account_creator.create_accounts(manager, 5, {}, on_done=done, stop_event=stop,
                                            on_progress=lambda *a: progress.append(a))
            self.assertTrue(finished.wait(10))
        return outcome, progress

    def test_stop_starts_no_new_accounts_and_reports_what_was_made(self):
        outcome, progress = self.run_creator(stop_after=2)
        report = outcome["report"]
        self.assertTrue(report["stopped"])
        self.assertEqual(report["created"], ["user0", "user1"])
        self.assertEqual(report["requested"], 5)
        self.assertTrue(outcome["success"])
        self.assertEqual(progress[-1][0], 2)

    def test_a_full_run_reports_every_account(self):
        outcome, _ = self.run_creator(stop_after=None)
        self.assertFalse(outcome["report"]["stopped"])
        self.assertEqual(len(outcome["report"]["created"]), 5)


if __name__ == "__main__":
    unittest.main()
