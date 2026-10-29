"""
features/app_restart.py
Start a fresh copy of the application (used by "Restart now" after a theme change).
"""

from __future__ import annotations

import os
import subprocess
import sys

from utils.app_paths import clean_child_environment

_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def restart_command(argv: list[str] | None = None) -> list[str]:
    argv = list(sys.argv if argv is None else argv)
    if getattr(sys, "frozen", False):
        return [sys.executable, *argv[1:]]
    script = os.path.abspath(argv[0]) if argv else ""
    return [sys.executable, script, *argv[1:]]


def start_new_copy() -> bool:
    try:
        subprocess.Popen(
            restart_command(),
            env=clean_child_environment(),
            creationflags=_DETACHED,
            close_fds=True,
        )
        return True
    except OSError as exc:
        print(f"[WARNING] Could not restart the application: {exc}")
        return False
