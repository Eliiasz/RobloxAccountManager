import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLabel

from utils import ui


class DeadCookieRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QApplication.instance() or QApplication([])

    def test_dead_status_text_and_back(self):
        dot, text = QLabel(), QLabel()
        ui.AccountManagerUIQt._apply_account_status(dot, text, False, dead=True)
        self.assertEqual(text.text(), "Cookie dead · sign in again")
        self.assertIn(ui.DEAD_RED, text.styleSheet())
        ui.AccountManagerUIQt._apply_account_status(dot, text, False)
        self.assertEqual(text.text(), "Idle")
        self.assertEqual(text.toolTip(), "")

    def test_an_account_in_game_shows_in_game(self):
        dot, text = QLabel(), QLabel()
        ui.AccountManagerUIQt._apply_account_status(dot, text, True, dead=True)
        self.assertEqual(text.text(), "In game")

    def test_name_is_crossed_out_in_red(self):
        name = QLabel("alice")
        ui.AccountManagerUIQt._style_account_name(name, True)
        self.assertIn("line-through", name.styleSheet())
        self.assertIn(ui.DEAD_RED, name.styleSheet())
        ui.AccountManagerUIQt._style_account_name(name, False)
        self.assertEqual(name.styleSheet(), "")

    def test_ghost_avatar_is_drawn_and_cached(self):
        window = SimpleNamespace(_ghost_pixmaps={})
        pix = ui.AccountManagerUIQt._make_ghost_pixmap(window, 36)
        self.assertFalse(pix.isNull())
        self.assertEqual((pix.width(), pix.height()), (36, 36))
        self.assertIs(ui.AccountManagerUIQt._make_ghost_pixmap(window, 36), pix)
        # The ghost's body is red, the corners stay see-through.
        image = pix.toImage()
        self.assertEqual(image.pixelColor(18, 22).name().upper(), ui.DEAD_RED.upper())
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)


if __name__ == "__main__":
    unittest.main()
