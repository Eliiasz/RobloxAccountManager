"""
Remove unpack folders left behind by earlier runs of the one-file build.

Each start of the .exe unpacks itself into a new _MEI... folder and deletes it
on a normal exit. When the app is ended from Task Manager, crashes, or the PC
shuts down while it runs (even halfway through unpacking), the folder (up to
about 150 MB) stays behind.

The build unpacks into its own folder (runtime_tmpdir in the spec), so every
_MEI folder there is ours. Older builds unpacked into Temp, where only folders
that look like ours (MARKER, or the libraries of a half-unpacked copy) are
removed. A folder is renamed before it is deleted: Windows refuses to rename a
folder while a running copy has its files loaded, so a folder in use is never
touched.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

PREFIX = "_MEI"
MARKER = os.path.join("assets", "discordlogo.png")
# A copy stopped while unpacking has no assets yet, but already has these.
PARTIAL_MARKERS = ("shiboken6", "selenium")
# A copy that is still unpacking has nothing loaded yet; leave young folders.
MIN_AGE_SECONDS = 10 * 60
# Shared PCs keep the app open for days; leftovers of other copies that crashed
# or were killed meanwhile are removed while it runs, not only at the next start.
REPEAT_SECONDS = 30 * 60


def _own_folder() -> str:
    own = getattr(sys, "_MEIPASS", "") or ""
    return os.path.normcase(os.path.abspath(own)) if own else ""


def _looks_like_ours(path: str) -> bool:
    if os.path.isfile(os.path.join(path, MARKER)):
        return True
    return all(os.path.isdir(os.path.join(path, name)) for name in PARTIAL_MARKERS)


def stale_folders(temp_dir: str, own: str = "", now: float | None = None,
                  all_ours: bool = False) -> list[str]:
    now = time.time() if now is None else now
    found = []
    try:
        entries = list(os.scandir(temp_dir))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.startswith(PREFIX) or entry.name.endswith("-removing"):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if os.path.normcase(os.path.abspath(entry.path)) == own:
                continue
            if not all_ours and not _looks_like_ours(entry.path):
                continue
            if now - entry.stat(follow_symlinks=False).st_mtime < MIN_AGE_SECONDS:
                continue
        except OSError:
            continue
        found.append(entry.path)
    return found


def _remove(path: str) -> int:
    """Delete one folder if nothing uses it. Returns the bytes freed."""
    claimed = f"{path}-removing"
    try:
        os.rename(path, claimed)
    except OSError:
        return 0  # in use by a running copy, or already gone
    size = 0
    for root, _dirs, files in os.walk(claimed):
        for name in files:
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    shutil.rmtree(claimed, ignore_errors=True)
    return size


def clean(temp_dir: str | None = None, own: str | None = None,
          all_ours: bool = False) -> tuple[int, int]:
    """Remove stale folders. Returns (folders removed, bytes freed)."""
    temp_dir = temp_dir or tempfile.gettempdir()
    own = _own_folder() if own is None else own
    removed = freed = 0
    for path in stale_folders(temp_dir, own, all_ours=all_ours):
        size = _remove(path)
        if not os.path.exists(path):
            removed += 1
            freed += size
    # Finish folders an earlier cleanup was interrupted in.
    try:
        leftovers = [e.path for e in os.scandir(temp_dir)
                     if e.name.startswith(PREFIX) and e.name.endswith("-removing")]
    except OSError:
        leftovers = []
    for path in leftovers:
        shutil.rmtree(path, ignore_errors=True)
    if removed:
        print(f"[INFO] Removed {removed} old temporary app folder(s) from {temp_dir} ({freed / (1024 * 1024):.0f} MB freed)")
    return removed, freed


def clean_all() -> None:
    """Clean this build's own unpack folder, then what older builds left in Temp."""
    own = _own_folder()
    temp_dir = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    runtime_dir = os.path.dirname(own) if own else ""
    if runtime_dir and runtime_dir != temp_dir:
        clean(runtime_dir, own, all_ours=True)
    clean(temp_dir, own)


def run_repeatedly(stop: threading.Event, interval: float = REPEAT_SECONDS) -> None:
    """Clean now, then again every interval until stop is set."""
    while True:
        try:
            clean_all()
        except Exception as exc:
            print(f"[WARNING] Could not remove old temporary app folders: {exc}")
        if stop.wait(interval):
            return


def start_in_background() -> None:
    """Clean up without slowing the start. Only the built .exe makes these folders."""
    if not getattr(sys, "frozen", False):
        return
    threading.Thread(target=run_repeatedly, args=(threading.Event(),),
                     name="temp-cleanup", daemon=True).start()
