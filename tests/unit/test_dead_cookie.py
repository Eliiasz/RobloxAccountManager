import threading
import unittest
from unittest import mock

from classes.account_manager import RobloxAccountManager
from classes.operation_result import OperationResult
from classes.roblox_api import RobloxAPI
from features import cookie_validator


def make_manager(cookie_valid=None):
    manager = RobloxAccountManager.__new__(RobloxAccountManager)
    manager._accounts_lock = threading.RLock()
    manager._launch_records = {}
    manager._pre_launch_hook = None
    manager.accounts = {"alice": {"cookie": "TEST_COOKIE", "user_id": "42", "cookie_valid": cookie_valid}}
    manager.save_accounts = mock.Mock()
    manager.on_cookie_status = mock.Mock()
    return manager


class DeadCookieAfterLaunchTests(unittest.TestCase):
    def launch(self, manager, result, check):
        with mock.patch.object(RobloxAPI, "launch_roblox", return_value=result), \
                mock.patch.object(cookie_validator, "_check", return_value=check) as checked:
            manager.launch_roblox("alice", "1818")
        return checked

    def test_a_refused_cookie_that_fails_the_check_is_marked_dead(self):
        manager = make_manager(True)
        refused = OperationResult.failure("COOKIE_INVALID", "Account Cookie Invalid", "Rejected")
        self.launch(manager, refused, (cookie_validator.INVALID, "HTTP 401", False))
        self.assertIs(manager.accounts["alice"]["cookie_valid"], False)
        manager.save_accounts.assert_called()
        manager.on_cookie_status.assert_called_once_with("alice", cookie_validator.INVALID)

    def test_a_refusal_the_check_cannot_confirm_changes_nothing(self):
        manager = make_manager(True)
        refused = OperationResult.failure("COOKIE_INVALID", "Account Cookie Invalid", "Rejected")
        self.launch(manager, refused, (cookie_validator.UNKNOWN, "timeout", False))
        self.assertIs(manager.accounts["alice"]["cookie_valid"], True)
        manager.save_accounts.assert_not_called()

    def test_other_launch_failures_do_not_check_the_cookie(self):
        manager = make_manager(True)
        failed = OperationResult.failure("AUTH_REQUEST_FAILED", "Failed", "Roblox is down")
        checked = self.launch(manager, failed, (cookie_validator.INVALID, "", False))
        checked.assert_not_called()
        manager.on_cookie_status.assert_not_called()

    def test_a_working_launch_clears_the_dead_mark(self):
        manager = make_manager(False)
        self.launch(manager, OperationResult.success(), (cookie_validator.VALID, "", False))
        self.assertIs(manager.accounts["alice"]["cookie_valid"], True)
        manager.on_cookie_status.assert_called_once_with("alice", "valid")


if __name__ == "__main__":
    unittest.main()
