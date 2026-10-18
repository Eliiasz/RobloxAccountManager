import os
import stat
import tempfile
import unittest
from pathlib import Path

from features import roblox_settings as rs


class SettingsBackupTests(unittest.TestCase):
    def test_backup_replaces_an_old_read_only_backup(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / "GlobalBasicSettings_13.xml"
            backup = Path(str(settings) + rs._BACKUP_SUFFIX)
            settings.write_text("<new/>", encoding="utf-8")
            backup.write_text("<old/>", encoding="utf-8")
            os.chmod(settings, stat.S_IREAD)   # Roblox file locked read-only
            os.chmod(backup, stat.S_IREAD)     # stale read-only backup
            try:
                rs._write_backup(settings, backup)
                self.assertEqual(backup.read_text(encoding="utf-8"), "<new/>")
                self.assertTrue(os.stat(backup).st_mode & stat.S_IWRITE, "new backup must be writable")
            finally:
                for path in (settings, backup):
                    if path.exists():
                        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)

    def test_backup_created_when_none_exists(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / "GlobalBasicSettings_13.xml"
            backup = Path(str(settings) + rs._BACKUP_SUFFIX)
            settings.write_text("<x/>", encoding="utf-8")
            rs._write_backup(settings, backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), "<x/>")


if __name__ == "__main__":
    unittest.main()
