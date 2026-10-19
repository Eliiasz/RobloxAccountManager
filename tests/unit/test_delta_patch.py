import hashlib
import os
import random
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from features import delta_patch, updater


def _exe(seed: int, size: int = 400_000) -> bytes:
    rng = random.Random(seed)
    return b"MZ" + bytes(rng.getrandbits(8) for _ in range(size))


class DeltaPatchTests(unittest.TestCase):
    def test_patch_rebuilds_the_new_file_and_is_small(self):
        old = _exe(1)
        new = old[:200_000] + b"changed code" * 50 + old[200_000:]
        patch_bytes = delta_patch.make_patch(old, new, level=3)
        self.assertEqual(delta_patch.apply_patch(old, patch_bytes), new)
        self.assertLess(len(patch_bytes), len(new) // 10)

    def test_patch_from_the_wrong_file_never_rebuilds_the_new_file(self):
        old, other = _exe(1), _exe(2)
        new = old + b"tail"
        patch_bytes = delta_patch.make_patch(old, new, level=3)
        try:
            rebuilt = delta_patch.apply_patch(other, patch_bytes)
        except Exception:
            return
        self.assertNotEqual(rebuilt, new)

    def test_asset_name(self):
        self.assertEqual(delta_patch.patch_asset_name("v2.9.7", "2.9.6"), "ANaneRAM-v2.9.7-from-v2.9.6.patch")


class UpdaterPatchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.old = _exe(3)
        self.new = self.old[:100_000] + b"new feature" * 20 + self.old[100_000:]
        self.target = os.path.join(folder.name, "ANaneRAM.exe")
        with open(self.target, "wb") as handle:
            handle.write(self.old)

    def run_update(self, patch_body: bytes, digest: str):
        asset = {
            "url": "https://github.com/o/r/releases/download/v9/ANaneRAM-v9.0.0.exe",
            "name": "ANaneRAM-v9.0.0.exe", "size": len(self.new), "digest": digest,
            "patch": {"url": "https://github.com/o/r/releases/download/v9/p.patch",
                      "name": "p.patch", "size": len(patch_body)},
        }
        requested = []

        def fake_get(url, **_kwargs):
            requested.append(url)
            body = patch_body if url.endswith(".patch") else self.new
            reply = MagicMock(headers={"content-length": str(len(body))})
            reply.iter_content.return_value = [body]
            return reply

        installed = {}

        def fake_install(source_path, *_args, **_kwargs):
            with open(source_path, "rb") as handle:
                installed["data"] = handle.read()

        finished = threading.Event()
        result = {}
        with patch.object(updater, "get_update_target", return_value=self.target), \
                patch.object(updater, "get_exe_asset", return_value=asset), \
                patch.object(updater.requests, "get", side_effect=fake_get), \
                patch.object(updater, "_launch_installer", side_effect=fake_install):
            updater.download_update(lambda _: None, lambda ok, msg: (result.update(ok=ok), finished.set()))
            self.assertTrue(finished.wait(20))
        return result["ok"], requested, installed.get("data")

    def digest(self, data):
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def test_a_good_patch_is_used_and_the_full_exe_is_not_downloaded(self):
        body = delta_patch.make_patch(self.old, self.new, level=3)
        ok, requested, data = self.run_update(body, self.digest(self.new))
        self.assertTrue(ok)
        self.assertEqual(requested, ["https://github.com/o/r/releases/download/v9/p.patch"])
        self.assertEqual(data, self.new)

    def test_a_bad_patch_falls_back_to_the_full_download(self):
        ok, requested, data = self.run_update(b"garbage", self.digest(self.new))
        self.assertTrue(ok)
        self.assertEqual(len(requested), 2)
        self.assertEqual(data, self.new)

    def test_without_a_release_checksum_the_patch_is_not_trusted(self):
        body = delta_patch.make_patch(self.old, self.new, level=3)
        ok, requested, _ = self.run_update(body, "")
        self.assertTrue(ok)
        self.assertEqual(requested, ["https://github.com/o/r/releases/download/v9/ANaneRAM-v9.0.0.exe"])

    def test_select_patch_matches_this_version_only(self):
        release = {"tag_name": "v9.0.0", "assets": [
            {"name": "ANaneRAM-v9.0.0-from-v1.0.0.patch", "browser_download_url": "https://github.com/a"},
            {"name": "ANaneRAM-v9.0.0-from-v2.0.0.patch", "browser_download_url": "https://github.com/b"},
        ]}
        self.assertEqual(updater._select_patch(release, "2.0.0")["url"], "https://github.com/b")
        self.assertIsNone(updater._select_patch(release, "3.0.0"))


if __name__ == "__main__":
    unittest.main()
