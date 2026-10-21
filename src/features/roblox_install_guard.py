"""
features/roblox_install_guard.py
Keep Roblox's installer/updater from interrupting running accounts.

Every normal launch (the roblox-player: link) goes through Roblox's launcher,
which checks for an update and tries to install it. With other Roblox windows
open, or several launches at once, the files it needs are in use and it stops
with "Installer encountered a critical error". With this guard on (the
default, Settings > Roblox):

  * While a Roblox game is already open, new accounts are started with the
    game exe those windows are using, so the installer never runs. If that
    start fails right away, the normal launcher is used instead.
  * After any launch, Roblox error boxes (a standard Windows dialog, which a
    game window never is) are closed for a while so they do not sit there.
  * Before launching several accounts with no Roblox open, a pending Roblox
    update is detected and the first account is opened alone, so the update
    finishes before the rest start.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

SETTING = "prevent_installer_errors"
_VERSION_URL = "https://clientsettings.roblox.com/v2/client-version/WindowsPlayer"
_VERSION_CACHE_SECONDS = 600.0
_WATCH_SECONDS = 90.0
_WATCH_INTERVAL = 1.5
_QUICK_EXIT_SECONDS = 3.0

_lock = threading.Lock()
_watch_until = 0.0
_watch_running = False
_version_cache: tuple[float, str] = (0.0, "")


def enabled() -> bool:
    try:
        import features.settings_store as settings_store
        return bool(settings_store.get(SETTING, True))
    except Exception:
        return True


def _game_processes():
    import features.presence as presence_mod
    return presence_mod.get_roblox_processes(force=True)


def running_client_exe() -> str:
    """Path of the game exe used by the newest open Roblox window, or ""."""
    try:
        processes = _game_processes()
    except Exception:
        return ""
    for _create, process in sorted(processes.values(), key=lambda item: item[0], reverse=True):
        try:
            path = process.exe()
        except Exception:
            continue
        if path and os.path.isfile(path):
            return path
    return ""


def installed_version() -> str:
    """Folder name (version-xxxx) of the newest installed Roblox Player."""
    root = os.path.join(os.getenv("LOCALAPPDATA", ""), "Roblox", "Versions")
    try:
        folders = [
            entry for entry in os.scandir(root)
            if entry.is_dir() and entry.name.startswith("version-")
            and os.path.isfile(os.path.join(entry.path, "RobloxPlayerBeta.exe"))
        ]
    except OSError:
        return ""
    if not folders:
        return ""
    return max(folders, key=lambda entry: entry.stat().st_mtime).name


def latest_version() -> str:
    """Roblox's current Windows player version (cached for 10 minutes), or ""."""
    global _version_cache
    checked, version = _version_cache
    if version and time.monotonic() - checked < _VERSION_CACHE_SECONDS:
        return version
    try:
        import requests
        response = requests.get(_VERSION_URL, timeout=4)
        if response.status_code == 200:
            version = str(response.json().get("clientVersionUpload") or "")
    except Exception:
        version = ""
    if version:
        _version_cache = (time.monotonic(), version)
    return version


def update_pending() -> bool:
    """True only when Roblox has a newer player than the one installed."""
    installed = installed_version()
    if not installed:
        return False  # not installed here: the normal launcher installs it anyway
    latest = latest_version()
    return bool(latest and latest != installed)


def direct_args(ticket: str, launch_time_ms: int, tracker: str, place_launcher_url: str = "") -> list[str]:
    args = ["--app", "-t", ticket]
    if place_launcher_url:
        args += ["-j", place_launcher_url]
    args += ["-b", str(tracker), f"--launchtime={launch_time_ms}", "--rloc", "en_us", "--gloc", "en_us"]
    return args


def start_direct(args: list[str]) -> str:
    """Start the open windows' game exe directly. Returns its path, or "" to use the normal launcher."""
    exe = running_client_exe()
    if not exe:
        return ""
    try:
        process = subprocess.Popen(
            [exe, *args], cwd=os.path.dirname(exe),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        print(f"[WARNING] Direct Roblox start failed ({exc}); using the normal launcher")
        return ""
    try:
        code = process.wait(timeout=_QUICK_EXIT_SECONDS)
    except subprocess.TimeoutExpired:
        return exe
    print(f"[WARNING] Direct Roblox start closed at once (exit code {code}); using the normal launcher")
    return ""


def watch_error_windows(seconds: float = _WATCH_SECONDS) -> None:
    """Close Roblox error boxes for the next few seconds (one shared watcher)."""
    global _watch_until, _watch_running
    if not enabled():
        return
    with _lock:
        _watch_until = max(_watch_until, time.monotonic() + seconds)
        if _watch_running:
            return
        _watch_running = True
    threading.Thread(target=_watch_loop, daemon=True, name="RobloxErrorWatch").start()


def _watch_loop() -> None:
    global _watch_running
    try:
        import features.account_actions as actions
        while True:
            with _lock:
                if time.monotonic() >= _watch_until:
                    _watch_running = False
                    return
            try:
                for title in actions.close_roblox_error_windows():
                    print(f"[WARNING] Closed a Roblox error box ({title}). "
                          "That account didn't open; try it again (the Batch Queue retries it).")
            except Exception as exc:
                print(f"[WARNING] Roblox error box check failed: {exc}")
            time.sleep(_WATCH_INTERVAL)
    finally:
        with _lock:
            _watch_running = False
