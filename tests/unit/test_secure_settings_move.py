import threading
import unittest
from unittest.mock import MagicMock, patch

from classes.account_manager import RobloxAccountManager


def _manager():
    manager = RobloxAccountManager.__new__(RobloxAccountManager)
    manager._accounts_lock = threading.RLock()
    manager.secure_settings = {}
    manager._secure_settings_encryptor = None
    manager._unavailable_secure_settings = None
    manager.save_accounts = MagicMock()
    return manager


LEGACY_BLOB = {"encrypted": True, "method": "hardware", "version": 1, "data": {"x": 1}}


class SecureSettingsOnAnotherPcTests(unittest.TestCase):
    def test_settings_locked_to_another_pc_are_detected(self):
        manager = _manager()
        failing = MagicMock()
        failing.decrypt_data.side_effect = ValueError("other PC")
        with patch.object(manager, "_get_secure_settings_encryptor", return_value=failing):
            self.assertEqual(manager._deserialize_secure_settings(LEGACY_BLOB), {})
        self.assertTrue(manager.secure_settings_unavailable)
        self.assertFalse(manager.set_secure_setting("backup_password", "pw"))

    def test_reset_makes_them_usable_again_without_touching_accounts(self):
        manager = _manager()
        manager._unavailable_secure_settings = dict(LEGACY_BLOB)
        manager.accounts = {"a": {"cookie": "c"}}
        self.assertTrue(manager.reset_unavailable_secure_settings())
        self.assertFalse(manager.secure_settings_unavailable)
        self.assertEqual(manager.accounts, {"a": {"cookie": "c"}})
        self.assertTrue(manager.set_secure_setting("backup_password", "pw"))
        self.assertEqual(manager.get_secure_setting("backup_password"), "pw")

    def test_plain_secure_settings_move_with_the_accounts(self):
        manager = _manager()
        self.assertEqual(manager._deserialize_secure_settings({"backup_password": "pw"}),
                         {"backup_password": "pw"})
        self.assertFalse(manager.secure_settings_unavailable)
        # Saved again in plain form (the account file's own encryption, if any, covers it).
        manager.secure_settings = {"backup_password": "pw"}
        self.assertEqual(manager._serialize_secure_settings(), {"backup_password": "pw"})


if __name__ == "__main__":
    unittest.main()
