import os
import tempfile
import unittest
from unittest.mock import patch

from features import browsers


class BraveDetectionTests(unittest.TestCase):
    def test_brave_resolves_to_chrome_driver(self):
        with tempfile.TemporaryDirectory() as root:
            exe = os.path.join(root, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")
            os.makedirs(os.path.dirname(exe))
            open(exe, "w").close()
            env = {"ProgramFiles": root, "ProgramFiles(x86)": "", "LOCALAPPDATA": ""}
            with patch.dict(os.environ, env), patch.object(browsers.shutil, "which", return_value=None):
                result = browsers.resolve_browser("brave")
        self.assertTrue(result)
        browser = result.data["browser"]
        self.assertEqual(browser["key"], "brave")
        self.assertEqual(browser["driver_type"], "chrome")
        self.assertEqual(browser["executable_path"], exe)

    def test_missing_brave_reports_not_installed(self):
        env = {"ProgramFiles": "", "ProgramFiles(x86)": "", "LOCALAPPDATA": ""}
        with patch.dict(os.environ, env), patch.object(browsers.shutil, "which", return_value=None):
            result = browsers.resolve_browser("brave")
        self.assertFalse(result)
        self.assertEqual(result.code, "BRAVE_NOT_INSTALLED")


if __name__ == "__main__":
    unittest.main()
