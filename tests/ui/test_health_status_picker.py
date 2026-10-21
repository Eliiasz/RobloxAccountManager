import unittest
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from features import account_health as health
from utils import ui


class _Page:
    """Just the Account Health page methods, without the whole main window."""


for _name in ("_build_ban_check_panel", "_on_ban_check_result", "_tick_ban_status",
              "_refresh_ban_status_buttons", "_ticked_ban_usernames", "_update_ban_delete_buttons",
              "_apply_ban_filter", "_set_ban_ticks", "_on_ban_scan", "_on_ban_stop", "_on_ban_delete",
              "_BAN_COLORS"):
    setattr(_Page, _name, getattr(ui.AccountManagerUIQt, _name))


def _result(name, status):
    return SimpleNamespace(username=name, status=status, label=health.LABELS[status], detail="")


class StatusPickerTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])
        self.page = _Page()
        self.panel = self.page._build_ban_check_panel()
        rows = [("ok1", health.OK), ("ok2", health.OK), ("ban1", health.BANNED), ("ban2", health.BANNED),
                ("lock1", health.LOCKED), ("verify1", health.NEEDS_VERIFY), ("unk1", health.UNKNOWN)]
        for index, (name, status) in enumerate(rows, 1):
            self.page._on_ban_check_result(_result(name, status), index, len(rows))
        self.page._set_ban_ticks(False)

    def ticked(self):
        return set(self.page._ticked_ban_usernames())

    def test_no_button_for_ok(self):
        self.assertNotIn(health.OK, self.page._ban_status_btns)
        self.assertEqual(self.page._ban_status_btns[health.BANNED].text(), "Banned (2)")
        self.assertFalse(self.page._ban_status_btns[health.TERMINATED].isEnabled())

    def test_click_ticks_only_that_status_and_click_again_unticks(self):
        banned = self.page._ban_status_btns[health.BANNED]
        banned.click()
        self.assertEqual(self.ticked(), {"ban1", "ban2"})
        self.assertTrue(banned.isChecked())
        self.page._ban_status_btns[health.LOCKED].click()
        self.assertEqual(self.ticked(), {"ban1", "ban2", "lock1"})
        banned.click()
        self.assertEqual(self.ticked(), {"lock1"})
        self.assertFalse(banned.isChecked())
        self.assertEqual(self.page._ban_delete_btn.text(), "Delete Ticked (1)")

    def test_ok_accounts_can_never_be_ticked(self):
        for btn in self.page._ban_status_btns.values():
            if btn.isEnabled():
                btn.click()
        self.page._set_ban_ticks(True)
        self.page._tick_ban_status(health.OK, True)
        self.assertFalse({"ok1", "ok2"} & self.ticked())
        for name in ("ok1", "ok2"):
            item = self.page._ban_items[name]
            self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_button_follows_manual_ticks(self):
        item = self.page._ban_items["ban1"]
        item.setCheckState(0, Qt.CheckState.Checked)
        self.assertFalse(self.page._ban_status_btns[health.BANNED].isChecked())
        self.page._ban_items["ban2"].setCheckState(0, Qt.CheckState.Checked)
        self.assertTrue(self.page._ban_status_btns[health.BANNED].isChecked())


if __name__ == "__main__":
    unittest.main()
