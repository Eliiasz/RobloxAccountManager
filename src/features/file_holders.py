"""
features/file_holders.py
Which programs have a file open, using the Windows Restart Manager (the same
API installers use to say "close these programs first"). Safe to call from
the UI: it doesn't open handles of other processes.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

_CCH_RM_SESSION_KEY = 33
_CCH_RM_MAX_APP_NAME = 255
_CCH_RM_MAX_SVC_NAME = 63
_ERROR_MORE_DATA = 234


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", _FileTime)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("Process", _UniqueProcess),
        ("strAppName", wintypes.WCHAR * (_CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (_CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def who_has(path: str) -> list[tuple[int, str]]:
    """[(pid, program name)] of processes holding `path` open; [] if none or unknown."""
    try:
        rm = ctypes.WinDLL("rstrtmgr")
    except (OSError, AttributeError):
        return []
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(os.path.abspath(path))
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        result = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if result not in (0, _ERROR_MORE_DATA) or not needed.value:
            return []
        infos = (_ProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)) != 0:
            return []
        holders = []
        for info in infos[: count.value]:
            pid = int(info.Process.dwProcessId)
            name = _process_name(pid) or info.strAppName or f"PID {pid}"
            holders.append((pid, name))
        return holders
    except Exception:
        return []
    finally:
        rm.RmEndSession(session)


def _process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def is_this_app(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(("ananeram", "evanovarram"))
