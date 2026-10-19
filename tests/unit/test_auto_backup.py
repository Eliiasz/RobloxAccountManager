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

    def test_only_the_newest_backups_are_kept_and_copied(self):
        extra = os.path.join(self.folder, "usb")
        settings_store.save(auto_backup.EXTRA_FOLDER_SETTING, extra)
        manager = _Manager({"alice": {"cookie": "c1"}})
        for second in range(auto_backup.KEEP + 3):
            stamp = datetime(2026, 1, 1, 12, 0, second)
            with patch.object(auto_backup, "datetime") as fake:
                fake.now.return_value = stamp
                fake.strptime = datetime.strptime
                self.assertTrue(auto_backup.make_backup(manager))
        self.assertEqual(len(auto_backup.list_backups()), auto_backup.KEEP)
        self.assertEqual(len(auto_backup.list_backups(extra)), auto_backup.KEEP)

    def test_nothing_to_back_up(self):
        self.assertFalse(auto_backup.make_backup(_Manager({})))

    def test_change_backup_waits_an_hour_and_needs_a_change(self):
        settings_store.save(auto_backup.LAST_BACKUP_SETTING, 1000.0)
        self.assertFalse(auto_backup.backup_due("2:x", "2:x", now=10_000))
        self.assertFalse(auto_backup.backup_due("3:y", "2:x", now=1000 + 60))
        self.assertTrue(auto_backup.backup_due("3:y", "2:x", now=1000 + auto_backup.CHANGE_INTERVAL_SECONDS))


if __name__ == "__main__":
    unittest.main()
