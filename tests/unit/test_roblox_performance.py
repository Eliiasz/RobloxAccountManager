import unittest
from unittest.mock import patch

from classes.operation_result import OperationResult
from features import roblox_settings as rs


def loaded(fps="60", quality="5"):
    return OperationResult.success(data={
        "file_hash": "h",
        "settings": [
            {"key": "FramerateCap", "value": fps},
            {"key": "SavedQualityLevel", "value": quality},
        ],
    })


class PerformanceValueTests(unittest.TestCase):
    def test_nothing_ticked_means_nothing_to_write(self):
        self.assertEqual(rs.performance_values({}), {})

    def test_lowest_values_and_clamping(self):
        self.assertEqual(
            rs.performance_values({"perf_fps_enabled": True, "perf_fps": 5,
                                   "perf_quality_enabled": True, "perf_quality": 1}),
            {"FramerateCap": "5", "SavedQualityLevel": "1"},
        )
        self.assertEqual(rs.performance_values({"perf_fps_enabled": True, "perf_fps": 1})["FramerateCap"], "5")
        self.assertEqual(rs.performance_values({"perf_fps_enabled": True, "perf_fps": 999})["FramerateCap"], "240")
        self.assertEqual(rs.performance_values({"perf_quality_enabled": True, "perf_quality": 0})["SavedQualityLevel"], "1")


class ApplyPerformanceTests(unittest.TestCase):
    def test_writes_only_changed_keys(self):
        with patch.object(rs, "load_settings", return_value=loaded(fps="60", quality="1")), \
                patch.object(rs, "apply_settings", return_value=OperationResult.success()) as apply:
            result = rs.apply_performance({"perf_fps_enabled": True, "perf_fps": 5,
                                           "perf_quality_enabled": True, "perf_quality": 1})
        self.assertTrue(result)
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0], {"FramerateCap": "5"})

    def test_no_write_when_already_set(self):
        with patch.object(rs, "load_settings", return_value=loaded(fps="5")), \
                patch.object(rs, "apply_settings") as apply:
            result = rs.apply_performance({"perf_fps_enabled": True, "perf_fps": 5})
        self.assertTrue(result)
        apply.assert_not_called()

    def test_missing_settings_file_is_reported(self):
        failure = OperationResult.failure("ROBLOX_SETTINGS_NOT_FOUND", "x", "not found")
        with patch.object(rs, "load_settings", return_value=failure):
            self.assertFalse(rs.apply_performance({"perf_fps_enabled": True, "perf_fps": 5}))


if __name__ == "__main__":
    unittest.main()
