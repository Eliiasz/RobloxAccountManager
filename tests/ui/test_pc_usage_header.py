from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from features import pc_usage
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
        reader = pc_usage.PcUsageReader()
        try:
            used, total, cpu = reader.read()
        finally:
            reader.close()
        self.assertGreater(total, 0)
        self.assertGreaterEqual(used, 0)
        self.assertTrue(0 <= cpu <= 100)

    def test_uses_task_manager_counter_on_windows(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")
        reader = pc_usage.PcUsageReader()
        try:
            self.assertIn(reader.source, ("utility", "time"))
        finally:
            reader.close()

    def test_falls_back_when_counter_fails(self):
        reader = pc_usage.PcUsageReader()
        reader.close()
        reader._query = object()  # a broken counter
        with patch.object(pc_usage.psutil, "cpu_percent", return_value=37.0):
            self.assertEqual(reader.cpu_percent(), 37.0)
        reader._query = None

    def test_boosted_values_are_capped_like_task_manager(self):
        reader = pc_usage.PcUsageReader()
        reader.close()
        with patch.object(pc_usage.psutil, "cpu_percent", return_value=130.0):
            self.assertEqual(reader.cpu_percent(), 100.0)

    def test_tick_refreshes_the_line_every_second(self):
        self.assertEqual(ui.PC_USAGE_INTERVAL_MS, 1000)
        reader = SimpleNamespace(read=lambda: (1.0, 2.0, 3.0))
        refresh = []
        window = SimpleNamespace(_pc_usage_reader=reader, _last_running_count=2,
                                 _on_running_count=lambda count, from_poll: refresh.append((count, from_poll)))
        ui.AccountManagerUIQt._tick_pc_usage(window)
        self.assertEqual(window._pc_usage, (1.0, 2.0, 3.0))
        self.assertEqual(refresh, [(2, False)])

if __name__ == "__main__":
    unittest.main()
