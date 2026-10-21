import os
import tempfile
import unittest
from unittest.mock import patch

from features import whats_new


class WhatsNewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        for version in ("2.9.9", "2.10.0", "2.10.1", "2.10.2", "2.10.3"):
            with open(os.path.join(self.folder, f"v{version}.md"), "w", encoding="utf-8") as handle:
                handle.write(f"**v{version}**\n- change")
        self.settings = {}
        self.enterContext(patch.object(whats_new.settings_store, "load", side_effect=lambda: dict(self.settings)))
        self.enterContext(patch.object(whats_new.settings_store, "save",
                                       side_effect=lambda k, v: self.settings.__setitem__(k, v)))

    def versions(self, notes):
        return [version for version, _text in notes]

    def test_fresh_install_shows_nothing(self):
        self.assertEqual(whats_new.pending("2.10.3", used_before=False, folder=self.folder), [])
        self.assertEqual(self.settings[whats_new.LAST_SEEN_SETTING], "2.10.3")

    def test_update_from_before_this_feature_shows_this_version(self):
        notes = whats_new.pending("2.10.3", used_before=True, folder=self.folder)
        self.assertEqual(self.versions(notes), ["2.10.3"])

    def test_skipped_versions_newest_first_and_only_once(self):
        self.settings[whats_new.LAST_SEEN_SETTING] = "2.10.0"
        notes = whats_new.pending("2.10.3", used_before=True, folder=self.folder)
        self.assertEqual(self.versions(notes), ["2.10.3", "2.10.2", "2.10.1"])
        self.assertEqual(whats_new.pending("2.10.3", used_before=True, folder=self.folder), [])

    def test_version_numbers_compare_as_numbers(self):
        self.settings[whats_new.LAST_SEEN_SETTING] = "2.9.9"
        notes = whats_new.pending("2.10.0", used_before=True, folder=self.folder)
        self.assertEqual(self.versions(notes), ["2.10.0"])

    def test_real_notes_for_this_version_exist(self):
        from utils.version import APP_VERSION
        self.assertIn(APP_VERSION, whats_new.available_notes())


if __name__ == "__main__":
    unittest.main()
