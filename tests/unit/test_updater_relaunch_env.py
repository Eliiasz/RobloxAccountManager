import os
import sys
import unittest
from unittest.mock import patch

from features import updater


class RelaunchEnvironmentTests(unittest.TestCase):
    def test_bundle_variables_are_dropped_so_the_new_version_starts_clean(self):
        bundle = os.path.join("C:\\", "Temp", "_MEI12345")
        env = {
            "_PYI_APPLICATION_HOME_DIR": bundle,
            "_PYI_ARCHIVE_FILE": "x.exe",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_MEIPASS2": bundle,
            "QT_PLUGIN_PATH": os.path.join(bundle, "PySide6", "plugins"),
            "PATH": os.pathsep.join([bundle, os.path.join("C:\\", "Windows")]),
            "PSModulePath": "x",
            "USERPROFILE": os.path.join("C:\\", "Users", "me"),
        }
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "_MEIPASS", bundle, create=True):
            out = updater._powershell_environment()
        for key in ("_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL",
                    "_MEIPASS2", "QT_PLUGIN_PATH", "PSModulePath"):
            self.assertNotIn(key, out)
        self.assertEqual(out["PATH"], os.path.join("C:\\", "Windows"))
        self.assertEqual(out["USERPROFILE"], env["USERPROFILE"])
        self.assertEqual(out["PYINSTALLER_RESET_ENVIRONMENT"], "1")


if __name__ == "__main__":
    unittest.main()
