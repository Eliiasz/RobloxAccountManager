import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFrame

from features import app_restart, color_themes
from utils import ui


def pixel(widget, x, y):
    return QColor(widget.grab().toImage().pixel(x, y)).name().upper()


class ThemeCardTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])

    def test_card_paints_the_theme_colours(self):
        colors = color_themes.resolve_colors({"color_theme": "violet_glow"})
        card = ui._ThemeCard("violet_glow", "Violet Glow", colors)
        card.resize(200, 118)
        self.assertEqual(pixel(card, 186, 50), colors["BG"])
        self.assertEqual(pixel(card, 14, 70), colors["PANEL"])

    def test_card_is_checkable_and_named(self):
        card = ui._ThemeCard("mono_glass", "Mono Glass", color_themes.resolve_colors({}))
        self.assertTrue(card.isCheckable())
        self.assertEqual(card.accessibleName(), "Mono Glass theme")


class ThemeGalleryTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])
        self.settings = {}
        self.enterContext(patch.object(ui.actions, "load_ui_settings", side_effect=lambda: dict(self.settings)))
        self.enterContext(patch.object(ui.actions, "save_ui_setting", side_effect=self.settings.__setitem__))
        self.cards = {
            key: ui._ThemeCard(key, label, color_themes.resolve_colors({"color_theme": key}))
            for key, label in color_themes.theme_choices()
        }
        self.banner = QFrame()
        self.window = SimpleNamespace(_theme_cards=self.cards, _theme_restart_banner=self.banner)
        for name in ("_theme_changed_since_start", "_on_theme_card_clicked", "_refresh_theme_restart_banner"):
            setattr(self.window, name, getattr(ui.AccountManagerUIQt, name).__get__(self.window))
        self.banner.show()
        self.addCleanup(self.banner.close)

    def test_every_theme_in_the_list_gets_a_card(self):
        self.assertEqual(list(self.cards), [key for key, _ in color_themes.theme_choices()])

    def test_choosing_a_theme_saves_it_highlights_it_and_asks_for_a_restart(self):
        with patch.object(ui, "_THEME_NAME", "mono_glass"):
            self.window._on_theme_card_clicked("violet_glow")
        self.assertEqual(self.settings["color_theme"], "violet_glow")
        self.assertTrue(self.cards["violet_glow"].isChecked())
        self.assertFalse(self.cards["mono_glass"].isChecked())
        self.assertTrue(self.banner.isVisible())

    def test_choosing_the_running_theme_again_hides_the_restart_banner(self):
        with patch.object(ui, "_THEME_NAME", "mono_glass"):
            self.window._on_theme_card_clicked("sky_paper")
            self.window._on_theme_card_clicked("mono_glass")
        self.assertFalse(self.banner.isVisible())


class RestartTests(unittest.TestCase):
    def test_restart_command_for_source_runs_the_same_script(self):
        command = app_restart.restart_command(["src/main.py", "--flag"])
        self.assertTrue(command[1].endswith("main.py"))
        self.assertEqual(command[-1], "--flag")

    def test_restart_failure_is_reported_not_raised(self):
        with patch.object(app_restart.subprocess, "Popen", side_effect=OSError("nope")):
            self.assertFalse(app_restart.start_new_copy())

    def test_restart_starts_a_detached_copy(self):
        with patch.object(app_restart.subprocess, "Popen", MagicMock()) as popen:
            self.assertTrue(app_restart.start_new_copy())
        self.assertIn("env", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
