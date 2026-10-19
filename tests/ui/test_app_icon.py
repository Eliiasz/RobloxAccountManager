import struct
import unittest

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from utils import ui
from utils.app_paths import get_resource_path


class AppIconTests(unittest.TestCase):
    def setUp(self):
        QApplication.instance() or QApplication([])

    def test_window_icon_has_every_taskbar_size(self):
        icon = ui._app_icon()
        self.assertFalse(icon.isNull())
        sizes = {size.width() for size in icon.availableSizes()}
        for wanted in (16, 24, 32, 48, 256):
            self.assertIn(wanted, sizes)
        self.assertFalse(icon.pixmap(QSize(32, 32)).isNull())

    def test_ico_file_uses_classic_uncompressed_images(self):
        with open(get_resource_path("assets", "icon.ico"), "rb") as handle:
            data = handle.read()
        count = struct.unpack("<H", data[4:6])[0]
        self.assertGreaterEqual(count, 5)
        for index in range(count):
            _w, _h, _c, _r, planes, bits, _size, offset = struct.unpack(
                "<BBBBHHII", data[6 + 16 * index:22 + 16 * index])
            self.assertEqual(planes, 1)
            self.assertEqual(bits, 32)
            self.assertNotEqual(data[offset:offset + 4], b"\x89PNG")  # BMP, not PNG


if __name__ == "__main__":
    unittest.main()
