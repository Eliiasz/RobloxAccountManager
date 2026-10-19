"""
features/delta_patch.py
Small "patch" updates: the new .exe is stored as a zstd frame that uses the
old .exe as a reference, so only the parts that changed are downloaded.

Uses the zstd support built into Python (compression.zstd), so no extra
library is needed. Making a patch happens in the release workflow; applying
one happens in the updater, which always checks the result against the
release's SHA-256 before using it and falls back to the full download.
"""

from __future__ import annotations

import math
import os

PATCH_SUFFIX = ".patch"
MAX_WINDOW_LOG = 31


def patch_asset_name(new_version: str, old_version: str) -> str:
    new_tag = new_version if new_version.startswith("v") else f"v{new_version}"
    old_tag = old_version if old_version.startswith("v") else f"v{old_version}"
    return f"ANaneRAM-{new_tag}-from-{old_tag}{PATCH_SUFFIX}"


def _window_log(*sizes: int) -> int:
    # The window must reach back over the whole reference file.
    needed = max(1, sum(sizes))
    return min(MAX_WINDOW_LOG, max(20, math.ceil(math.log2(needed)) + 1))


def make_patch(old: bytes, new: bytes, level: int = 19) -> bytes:
    from compression import zstd

    reference = zstd.ZstdDict(old, is_raw=True)
    options = {
        zstd.CompressionParameter.compression_level: level,
        zstd.CompressionParameter.window_log: _window_log(len(old), len(new)),
        zstd.CompressionParameter.enable_long_distance_matching: 1,
    }
    return zstd.compress(new, options=options, zstd_dict=reference.as_prefix)


def apply_patch(old: bytes, patch: bytes) -> bytes:
    from compression import zstd

    reference = zstd.ZstdDict(old, is_raw=True)
    options = {zstd.DecompressionParameter.window_log_max: MAX_WINDOW_LOG}
    return zstd.decompress(patch, zstd_dict=reference.as_prefix, options=options)


def apply_patch_file(old_path: str, patch_path: str, output_path: str) -> None:
    with open(old_path, "rb") as handle:
        old = handle.read()
    with open(patch_path, "rb") as handle:
        patch = handle.read()
    new = apply_patch(old, patch)
    temporary = output_path + ".part"
    with open(temporary, "wb") as handle:
        handle.write(new)
    os.replace(temporary, output_path)
