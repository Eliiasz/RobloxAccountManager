import os
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import patch

from classes.operation_result import OperationResult
from features import account_backup, auto_backup, settings_store


class _Manager:
    def __init__(self, accounts):
        self.accounts = accounts
        self._accounts_lock = threading.RLock()
        self.secure = {}

    def get_secure_setting(self, key, default=""):
        return self.secure.get(key, default)

    def set_secure_setting(self, key, value):
        self.secure[key] = value
        return OperationResult.success()


class AutoBackupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.enterContext(patch.object(auto_backup, "get_data_dir", return_value=self.folder))
        self.enterContext(patch.object(settings_store, "_SETTINGS_PATH", os.path.join(self.folder, "ui.json")))
        settings_store.invalidate()
        self.addCleanup(settings_store.invalidate)

    def test_backup_opens_with_the_stored_key(self):
        manager = _Manager({"alice": {"cookie": "c1"}, "bob": {"cookie": "c2"}})
        result = auto_backup.make_backup(manager, "test")
        self.assertTrue(result, result.message)
        backups = auto_backup.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["count"], 2)
        passwords = auto_backup.candidate_passwords(manager)
        self.assertEqual(len(passwords), 1)
        opened = account_backup.read_backup(backups[0]["path"], passwords[0])
        self.assertTrue(opened)
        self.assertEqual(set(opened.data["accounts"]), {"alice", "bob"})

    def test_user_password_makes_backups_portable(self):
        manager = _Manager({"alice": {"cookie": "c1"}})
        manager.secure[auto_backup.PASSWORD_KEY] = "my-long-password"
        auto_backup.make_backup(manager)
        path = auto_backup.list_backups()[0]["path"]
        self.assertTrue(account_backup.read_backup(path, "my-long-password"))
        self.assertNotIn(auto_backup.AUTO_KEY, manager.secure)

    def _backup_at(self, manager, second, automatic):
        with patch.object(auto_backup, "datetime") as fake:
            fake.now.return_value = datetime(2026, 1, 1, 12, 0, second)
            fake.strptime = datetime.strptime
            self.assertTrue(auto_backup.make_backup(manager, automatic=automatic))

    def test_only_the_newest_three_automatic_backups_are_kept_and_copied(self):
        extra = os.path.join(self.folder, "usb")
        settings_store.save(auto_backup.EXTRA_FOLDER_SETTING, extra)
        manager = _Manager({"alice": {"cookie": "c1"}})
        self.assertEqual(auto_backup.KEEP, 3)
        for second in range(auto_backup.KEEP + 1):
            self._backup_at(manager, second, automatic=True)
        for folder in (None, extra):
            backups = auto_backup.list_backups(folder)
            self.assertEqual(len(backups), 3)
            self.assertTrue(all(backup["automatic"] for backup in backups))
            self.assertEqual(min(backup["when"].second for backup in backups), 1)  # the oldest went

    def test_manual_backups_are_never_deleted(self):
        manager = _Manager({"alice": {"cookie": "c1"}})
        for second in range(5):
            self._backup_at(manager, second, automatic=False)
        for second in range(10, 16):
            self._backup_at(manager, second, automatic=True)
        backups = auto_backup.list_backups()
        self.assertEqual(sum(1 for b in backups if not b["automatic"]), 5)
        self.assertEqual(sum(1 for b in backups if b["automatic"]), 3)

    def test_older_backups_without_the_auto_prefix_count_as_manual(self):
        os.makedirs(auto_backup.backup_folder(), exist_ok=True)
        open(os.path.join(auto_backup.backup_folder(), "accounts-20250101-120000-2.rambak"), "w").close()
        manager = _Manager({"alice": {"cookie": "c1"}})
        for second in range(5):
            self._backup_at(manager, second, automatic=True)
        names = {os.path.basename(b["path"]) for b in auto_backup.list_backups()}
        self.assertIn("accounts-20250101-120000-2.rambak", names)

    def test_without_a_password_backups_are_still_encrypted(self):
        manager = _Manager({"alice": {"cookie": "secret-cookie"}})
        auto_backup.make_backup(manager, automatic=True)
        path = auto_backup.list_backups()[0]["path"]
        with open(path, "rb") as handle:
            self.assertNotIn(b"secret-cookie", handle.read())
        self.assertIn(auto_backup.AUTO_KEY, manager.secure)

    def test_the_last_export_password_is_used_for_automatic_backups(self):
        manager = _Manager({"alice": {"cookie": "c1"}})
        auto_backup.remember_export_password(manager, "export-password-1")
        auto_backup.make_backup(manager, automatic=True)
        path = auto_backup.list_backups()[0]["path"]
        self.assertTrue(account_backup.read_backup(path, "export-password-1"))
        auto_backup.remember_export_password(manager, "")
        self.assertEqual(manager.secure[auto_backup.PASSWORD_KEY], "export-password-1")

    def test_nothing_to_back_up(self):
        self.assertFalse(auto_backup.make_backup(_Manager({})))

    def test_an_automatic_backup_is_due_every_24_hours(self):
        self.assertTrue(auto_backup.backup_due(now=10_000))  # never made one
        settings_store.save(auto_backup.LAST_AUTO_SETTING, 10_000.0)
        self.assertFalse(auto_backup.backup_due(now=10_000 + 23 * 3600))
        self.assertTrue(auto_backup.backup_due(now=10_000 + 24 * 3600))
        self.assertTrue(auto_backup.backup_due(now=5_000))  # clock went back

    def test_a_manual_backup_does_not_reset_the_24_hour_timer(self):
        manager = _Manager({"alice": {"cookie": "c1"}})
        auto_backup.make_backup(manager)
        self.assertTrue(auto_backup.backup_due())
        auto_backup.make_backup(manager, automatic=True)
        self.assertFalse(auto_backup.backup_due())


if __name__ == "__main__":
    unittest.main()
