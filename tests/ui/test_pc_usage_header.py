from types import SimpleNamespace
import unittest
from unittest.mock import patch

from utils import ui


def _window(usage, snapshot=None):
    return SimpleNamespace(_pc_usage=usage, _activity_snapshot=snapshot or {},
                           _format_mb=ui.AccountManagerUIQt._format_mb)


class PcUsageHeaderTests(unittest.TestCase):
    def _text(self, usage, settings=None, snapshot=None):
        with patch.object(ui.actions, "load_ui_settings", return_value=settings or {}):
            return ui.AccountManagerUIQt._running_header_text(_window(usage, snapshot), 4)

    def test_shows_whole_pc_ram_and_cpu(self):
        text, _tip = self._text((11.2 * 1024, 16 * 1024, 23.0))
        self.assertTrue(text.startswith("4 running"))
        self.assertIn("RAM 11.2 / 16 GB (70%)", text)
        self.assertIn("CPU 23%", text)
        self.assertNotIn(ui.PC_WARN_COLOR, text)

    def test_near_the_limit_turns_orange(self):
        text, _tip = self._text((15 * 1024, 16 * 1024, 95.0))
        self.assertEqual(text.count(ui.PC_WARN_COLOR), 2)

    def test_can_be_turned_off_and_old_setting_is_respected(self):
        usage = (8 * 1024, 16 * 1024, 10.0)
        self.assertEqual(self._text(usage, {ui.PC_USAGE_SETTING: False})[0], "4 running")
        self.assertEqual(self._text(usage, {"ram_show_total": False})[0], "4 running")

    def test_accounts_total_moves_to_the_tooltip(self):
        snapshot = {"a": {"ram_available": True, "ram_mb": 800.0}, "b": {"ram_available": True, "ram_mb": 700.0}}
        text, tip = self._text(None, {"presence_indicator": True}, snapshot)
        self.assertEqual(text, "4 running")
        self.assertIn("1.5 GB", tip)

    def test_reads_real_numbers(self):
        used, total, cpu = ui.AccountManagerUIQt._read_pc_usage()
        self.assertGreater(total, 0)
        self.assertGreaterEqual(used, 0)
        self.assertGreaterEqual(cpu, 0)


if __name__ == "__main__":
    unittest.main()
