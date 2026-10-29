import os
import tempfile
import time
import unittest

from features import temp_cleanup


class TempCleanupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.temp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, name, ours=True, age=3600):
        path = os.path.join(self.temp, name)
        os.makedirs(os.path.join(path, "assets"))
        if ours:
            with open(os.path.join(path, temp_cleanup.MARKER), "wb") as f:
                f.write(b"x" * 1000)
        old = time.time() - age
        os.utime(path, (old, old))
        return path

    def test_old_folders_of_this_app_are_removed(self):
        stale = self.make("_MEI1001")
        removed, freed = temp_cleanup.clean(self.temp, own="")
        self.assertEqual(removed, 1)
        self.assertEqual(freed, 1000)
        self.assertFalse(os.path.exists(stale))

    def test_the_running_copys_own_folder_is_kept(self):
        own = self.make("_MEI1002")
        temp_cleanup.clean(self.temp, own=os.path.normcase(os.path.abspath(own)))
        self.assertTrue(os.path.isdir(own))

    def test_other_apps_and_other_folders_are_kept(self):
        other_app = self.make("_MEI1003", ours=False)
        unrelated = self.make("SomethingElse")
        temp_cleanup.clean(self.temp, own="")
        self.assertTrue(os.path.isdir(other_app))
        self.assertTrue(os.path.isdir(unrelated))

    def test_a_folder_that_is_still_unpacking_is_kept(self):
        fresh = self.make("_MEI1004", age=5)
        temp_cleanup.clean(self.temp, own="")
        self.assertTrue(os.path.isdir(fresh))

    @unittest.skipUnless(os.name == "nt", "Windows locks folders with open files")
    def test_a_folder_in_use_by_a_running_copy_is_not_touched(self):
        busy = self.make("_MEI1005")
        with open(os.path.join(busy, temp_cleanup.MARKER), "rb"):
            removed, _ = temp_cleanup.clean(self.temp, own="")
            self.assertEqual(removed, 0)
            self.assertTrue(os.path.isfile(os.path.join(busy, temp_cleanup.MARKER)))

    def test_an_interrupted_cleanup_is_finished(self):
        half = self.make("_MEI1006-removing")
        temp_cleanup.clean(self.temp, own="")
        self.assertFalse(os.path.exists(half))

    def test_source_runs_do_nothing(self):
        temp_cleanup.start_in_background()  # not frozen: returns without a thread


if __name__ == "__main__":
    unittest.main()
