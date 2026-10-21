from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication, QPushButton

from features import account_actions as actions
from utils import ui


def _manager(enabled, method="hardware"):
    config = MagicMock()
    config.is_encryption_enabled.return_value = enabled
    config.get_encryption_method.return_value = method
    return SimpleNamespace(encryption_config=config)


class EncryptionBadgeTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])

    def test_new_names(self):
        self.assertEqual(actions.get_encryption_status(_manager(True, "hardware"))[0], "Device Locked")
        self.assertEqual(actions.get_encryption_status(_manager(True, "password"))[0], "Password Locked")
        self.assertEqual(actions.get_encryption_status(_manager(False))[0], "Unprotected")

    def test_badge_shows_name_and_explains_it(self):
        for method, name in (("hardware", "Device Locked"), ("password", "Password Locked")):
            window = SimpleNamespace(manager=_manager(True, method), _enc_badge=QPushButton())
            ui.AccountManagerUIQt._update_encryption_badge(window)
            badge = window._enc_badge
            self.assertEqual(badge.text().strip(), name)
            self.assertNotIn("[", badge.text())
            self.assertIn("Click to open", badge.toolTip())
            self.assertFalse(badge.isHidden())

    def test_click_opens_misc_settings(self):
        window = SimpleNamespace(_show_page=MagicMock(), _settings_switch_cat=MagicMock())
        ui.AccountManagerUIQt._open_encryption_settings(window)
        window._show_page.assert_called_once_with(4)
        window._settings_switch_cat.assert_called_once_with(ui.SETTINGS_MISC_TAB)


if __name__ == "__main__":
    unittest.main()
