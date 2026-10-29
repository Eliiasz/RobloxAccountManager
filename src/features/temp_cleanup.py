"""
Remove unpack folders left in Temp by earlier runs of the one-file build.

Each start of the .exe unpacks itself into a new _MEI... folder and deletes it
on a normal exit. When the app is ended from Task Manager, crashes, or the PC
shuts down while it runs, the folder (about 150 MB) stays behind.

Only folders that belong to this app (they contain MARKER) and that no running
copy uses are removed. A folder is renamed before it is deleted: Windows
refuses to rename a folder while a running copy has its files loaded, so a
folder in use is never touched.
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
# A copy that is still unpacking has nothing loaded yet; leave young folders.
MIN_AGE_SECONDS = 10 * 60


def _own_folder() -> str:
    own = getattr(sys, "_MEIPASS", "") or ""
    return os.path.normcase(os.path.abspath(own)) if own else ""


def stale_folders(temp_dir: str, own: str = "", now: float | None = None) -> list[str]:
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
            if not os.path.isfile(os.path.join(entry.path, MARKER)):
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


def clean(temp_dir: str | None = None, own: str | None = None) -> tuple[int, int]:
    """Remove stale folders. Returns (folders removed, bytes freed)."""
    temp_dir = temp_dir or tempfile.gettempdir()
    own = _own_folder() if own is None else own
    removed = freed = 0
    for path in stale_folders(temp_dir, own):
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
        print(f"[INFO] Removed {removed} old temporary app folder(s) from Temp ({freed / (1024 * 1024):.0f} MB freed)")
    return removed, freed


def start_in_background() -> None:
    """Clean up without slowing the start. Only the built .exe makes these folders."""
    if not getattr(sys, "frozen", False):
        return

    def run():
        try:
            clean()
        except Exception as exc:
            print(f"[WARNING] Could not remove old temporary app folders: {exc}")

    threading.Thread(target=run, name="temp-cleanup", daemon=True).start()
