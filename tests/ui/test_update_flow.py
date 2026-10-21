import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTextEdit, QWidget

from classes.operation_result import OperationResult
from utils import ui


def release(version="9.9.9", notes="notes"):
    return OperationResult.success(data={"version": version, "notes": notes, "url": "https://example.invalid"})


class RunNow:
    def __init__(self, *args, **kwargs):
        self.target = kwargs.get("target")

    def start(self):
        self.target()


class StartupCheckTests(unittest.TestCase):
    def check(self, result, settings=None):
        window = SimpleNamespace(_bridge=SimpleNamespace(update_available=MagicMock()))
        settings = {"check_updates_on_startup": True, **(settings or {})}
        with patch.object(ui.actions, "load_ui_settings", return_value=settings), \
                patch.object(ui.updater_mod, "get_latest_release", return_value=result) as fetch, \
                patch.object(ui.threading, "Thread", RunNow):
            ui.AccountManagerUIQt._start_update_check(window)
        return window, fetch

    def test_a_newer_version_is_announced_and_its_notes_are_kept(self):
        window, _ = self.check(release("9.9.9", "what is new"))
        window._bridge.update_available.emit.assert_called_once_with("9.9.9")
        self.assertEqual(window._latest_release["notes"], "what is new")

    def test_the_current_or_an_older_version_is_not_announced(self):
        for version in (ui.APP_VERSION, "0.0.1"):
            with self.subTest(version=version):
                window, _ = self.check(release(version))
                window._bridge.update_available.emit.assert_not_called()

    def test_updates_cannot_be_skipped_even_with_an_old_skip_saved(self):
        window, _ = self.check(release("9.9.9"), {"skipped_update_version": "9.9.9"})
        window._bridge.update_available.emit.assert_called_once_with("9.9.9")

    def test_a_failed_check_is_quiet(self):
        window, _ = self.check(OperationResult.failure("UPDATE_CHECK_FAILED", "t", "m", detail="HTTP 500"))
        window._bridge.update_available.emit.assert_not_called()

    def test_the_old_off_switch_no_longer_skips_the_check(self):
        window, fetch = self.check(release("9.9.9"), {"check_updates_on_startup": False})
        fetch.assert_called_once()
        window._bridge.update_available.emit.assert_called_once_with("9.9.9")


class ManualCheckTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])
        self.button = QPushButton("Checking...")
        self.button.setEnabled(False)
        self.window = SimpleNamespace(
            _sett_update_now_btn=self.button,
            _show_operation_error=MagicMock(),
            _show_update_dialog=MagicMock(),
        )

    def finish(self, result):
        with patch.object(ui, "_show_info") as info:
            ui.AccountManagerUIQt._on_update_check_finished(self.window, result)
        return info

    def test_the_button_is_restored_every_time(self):
        for result in (release("0.0.1"), release("9.9.9"), OperationResult.failure("X", "t", "m")):
            self.button.setEnabled(False)
            self.button.setText("Checking...")
            self.finish(result)
            self.assertTrue(self.button.isEnabled())
            self.assertEqual(self.button.text(), "Check for Updates Now")

    def test_a_failure_is_shown_as_an_error(self):
        failure = OperationResult.failure("UPDATE_CHECK_FAILED", "Update Check Failed", "No connection.")
        info = self.finish(failure)
        self.window._show_operation_error.assert_called_once_with(failure)
        info.assert_not_called()
        self.window._show_update_dialog.assert_not_called()

    def test_being_up_to_date_says_so(self):
        info = self.finish(release(ui.APP_VERSION))
        info.assert_called_once()
        self.assertIn(ui.APP_VERSION, info.call_args.args[2])
        self.window._show_update_dialog.assert_not_called()

    def test_a_newer_version_opens_the_dialog_with_its_notes(self):
        self.finish(release("9.9.9", "the notes"))
        self.window._show_update_dialog.assert_called_once_with("9.9.9")
        self.assertEqual(self.window._latest_release["notes"], "the notes")


class UpdateDialogTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])
        self.window = QWidget()
        self.window._latest_release = {"version": "9.9.9", "notes": "- a\n- b"}

    def open_dialog(self, version="9.9.9", act=None):
        seen = {}

        def fake_exec(dialog):
            seen["dialog"] = dialog
            seen["notes"] = dialog.findChildren(QTextEdit)
            seen["buttons"] = {b.text(): b for b in dialog.findChildren(QPushButton)}
            if act:
                act(seen)
            return 0

        with patch.object(QDialog, "exec", fake_exec), patch.object(ui.actions, "save_ui_setting") as save:
            ui.AccountManagerUIQt._show_update_dialog(self.window, version)
        return seen, save

    def test_release_notes_are_shown_read_only(self):
        seen, _ = self.open_dialog()
        self.assertEqual(len(seen["notes"]), 1)
        self.assertEqual(seen["notes"][0].toPlainText(), "- a\n- b")
        self.assertTrue(seen["notes"][0].isReadOnly())

    def test_no_notes_box_without_notes_or_for_another_version(self):
        self.window._latest_release = {"version": "9.9.9", "notes": ""}
        self.assertEqual(self.open_dialog()[0]["notes"], [])
        self.window._latest_release = {"version": "1.2.3", "notes": "old notes"}
        self.assertEqual(self.open_dialog("9.9.9")[0]["notes"], [])
        self.window._latest_release = None
        self.assertEqual(self.open_dialog()[0]["notes"], [])

    def test_the_update_is_required(self):
        seen, _ = self.open_dialog()
        for label in ("Manual Download", "Later", "Skip This Version", "Download Automatically"):
            self.assertNotIn(label, seen["buttons"])
        self.assertEqual(len(seen["buttons"]), 1)
        dialog = seen["dialog"]
        self.assertEqual(dialog.windowTitle(), "Update Required")
        self.assertFalse(dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint)

    def test_escape_does_nothing_and_closing_quits(self):
        self.window._quit_for_update = MagicMock()

        def act(seen):
            dialog = seen["dialog"]
            dialog.show()
            dialog.reject()  # what Esc does
            self.assertTrue(dialog.isVisible())
            dialog.close()  # Alt+F4 / taskbar close
            self.assertTrue(dialog.isVisible())
            dialog.hide()

        with patch.object(ui.QTimer, "singleShot", side_effect=lambda _ms, *a: a[-1]()), \
                patch.object(ui.updater_mod, "download_update"):
            self.window._bridge = MagicMock()
            self.open_dialog(act=act)
        self.window._quit_for_update.assert_called_once()

    def test_download_starts_by_itself_and_failure_offers_only_retry(self):
        self.window._bridge = ui._Bridge()
        calls = []

        def act(seen):
            QApplication.processEvents()
            self.assertEqual(len(calls), 1)  # started without a click
            calls[0]["on_done"](False, "No internet")
            QApplication.processEvents()
            buttons = [b for b in seen["dialog"].findChildren(QPushButton)]
            self.assertEqual([b.text() for b in buttons], ["Retry"])
            self.assertTrue(buttons[0].isEnabled())
            buttons[0].click()
            self.assertEqual(len(calls), 2)

        with patch.object(ui.updater_mod, "download_update", side_effect=lambda **kw: calls.append(kw)):
            self.open_dialog(act=act)

    def test_finished_download_waits_for_ok_then_restarts(self):
        self.window._bridge = ui._Bridge()
        self.window._quit_for_update = MagicMock()
        calls = []

        def act(seen):
            QApplication.processEvents()
            calls[0]["on_done"](True, "")
            QApplication.processEvents()
            self.window._quit_for_update.assert_not_called()  # nothing happens by itself
            buttons = seen["dialog"].findChildren(QPushButton)
            self.assertEqual([b.text() for b in buttons], ["OK"])
            with patch.object(ui.QTimer, "singleShot", side_effect=lambda _ms, *a: a[-1]()):
                buttons[0].click()
            self.window._quit_for_update.assert_called_once()

        with patch.object(ui.updater_mod, "download_update", side_effect=lambda **kw: calls.append(kw)):
            self.open_dialog(act=act)

if __name__ == "__main__":
    unittest.main()
