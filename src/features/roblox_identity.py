"""
features/roblox_identity.py
Work out which saved account each running Roblox window belongs to, for
features that must never touch the wrong window (Kill All Roblox keeping
locked accounts open, the Batch Queue).

Evidence, strongest first:
  1. The launch ID (browser tracker ID) this app passed to Roblox, found in
     the process command line and matched to the app's own launch record.
  2. The same launch ID found in exactly one Roblox log file.
  3. The Roblox log file the process actually has open (checked by a small
     helper process, a few per call).
  4. Timing: only when exactly one window and exactly one log are left over.

Results from 1-3 are certain and remembered for the life of the process.
Anything that cannot be decided is reported as unknown (None), never guessed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
import threading
import time

import features.presence as presence_mod

_TRACKER_PATTERNS = (
    re.compile(r"browsertrackerid[^0-9]{0,32}(\d+)", re.IGNORECASE),
    re.compile(r"(?:^|\s)-b\s+(\d+)"),
)
_LOG_EARLY_TOLERANCE_SEC = 2.0
_LOG_STARTUP_WINDOW_SEC = 60.0
_LAUNCH_STARTUP_WINDOW_SEC = 300.0
DEFAULT_PROBES = 4

_CACHE_LOCK = threading.Lock()
# (pid, create_time) -> user_id, only for certain evidence.
_CERTAIN: dict[tuple[int, float], str] = {}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CERTAIN.clear()


def process_tracker(process) -> str:
    """The single launch ID in a Roblox command line, or ""."""
    try:
        command_line = " ".join(str(value) for value in process.cmdline())
    except Exception:
        return ""
    found: set[str] = set()
    for pattern in _TRACKER_PATTERNS:
        found.update(pattern.findall(command_line))
    return next(iter(found)) if len(found) == 1 else ""


def _utc(create_time: float) -> datetime:
    return datetime.fromtimestamp(create_time, tz=timezone.utc).replace(tzinfo=None)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _probe(key, stop_event):
    from features.window_log_probe import probe_open_log_paths
    return probe_open_log_paths(key, stop_event)


def identify_clients(manager, processes=None, *, max_probes: int = DEFAULT_PROBES,
                     stop_event: threading.Event | None = None) -> dict[int, str | None]:
    """Map each running Roblox game PID to a saved username.

    Returns {pid: username}; the value is "" for a window that belongs to an
    account not saved in this app, and None when it cannot be told for sure.
    """
    if processes is None:
        processes = presence_mod.get_roblox_processes(force=True)
    stop_event = stop_event or threading.Event()
    uid_to_name = presence_mod._build_uid_map(manager)
    get_records = getattr(manager, "get_launch_records", None)
    try:
        launch_records = get_records() if callable(get_records) else {}
    except Exception:
        launch_records = {}
    if not isinstance(launch_records, dict):
        launch_records = {}

    keys = {pid: (pid, float(create)) for pid, (create, _process) in processes.items()}
    with _CACHE_LOCK:
        live = set(keys.values())
        for stale in [key for key in _CERTAIN if key not in live]:
            _CERTAIN.pop(stale, None)
        user_ids: dict[int, str] = {
            pid: _CERTAIN[key] for pid, key in keys.items() if key in _CERTAIN
        }
    certain: dict[int, str] = {}

    entries = []
    if len(user_ids) < len(keys):
        try:
            oldest = min(create for create, _p in processes.values())
            entries = presence_mod.get_roblox_log_entries(
                earliest_time=_utc(oldest) - timedelta(seconds=_LOG_EARLY_TOLERANCE_SEC),
            )
        except Exception:
            entries = []

    # 1 + 2: launch IDs.
    trackers = {pid: process_tracker(proc) for pid, (_c, proc) in processes.items()
                if pid not in user_ids}
    tracker_count: dict[str, int] = {}
    for tracker in trackers.values():
        if tracker:
            tracker_count[tracker] = tracker_count.get(tracker, 0) + 1
    by_tracker: dict[str, list] = {}
    for entry in entries:
        if entry.browser_tracker_id:
            by_tracker.setdefault(entry.browser_tracker_id, []).append(entry)
    claimed_logs: set[str] = set()
    for pid, tracker in trackers.items():
        if not tracker or tracker_count.get(tracker) != 1:
            continue
        record = launch_records.get(tracker)
        if record:
            user_id, launch_time = record
            if -_LOG_EARLY_TOLERANCE_SEC <= keys[pid][1] - float(launch_time) <= _LAUNCH_STARTUP_WINDOW_SEC:
                certain[pid] = str(user_id)
                continue
        matches = by_tracker.get(tracker, [])
        if len(matches) == 1:
            certain[pid] = matches[0].user_id
            claimed_logs.add(_norm(matches[0].path))

    # 3: the log file the process has open.
    path_entries = {_norm(entry.path): entry for entry in entries}
    probed: set[int] = set()
    probes = 0
    for pid in sorted(keys, key=lambda p: keys[p][1]):
        if pid in user_ids or pid in certain or probes >= max_probes or stop_event.is_set():
            continue
        probes += 1
        try:
            result = _probe(keys[pid], stop_event)
        except Exception:
            continue
        if getattr(result, "status", "") != "ok":
            continue
        probed.add(pid)
        matches = [path_entries[_norm(path)] for path in result.paths if _norm(path) in path_entries]
        if len(matches) == 1:
            certain[pid] = matches[0].user_id
            claimed_logs.add(_norm(matches[0].path))

    if certain:
        with _CACHE_LOCK:
            for pid, user_id in certain.items():
                _CERTAIN[keys[pid]] = user_id
        user_ids.update(certain)

    # 4: timing, only when it cannot be wrong.
    unresolved = [pid for pid in keys if pid not in user_ids]
    if len(unresolved) == 1:
        pid = unresolved[0]
        create = _utc(keys[pid][1])
        known = set(user_ids.values())
        candidates = [
            entry for entry in entries
            if _norm(entry.path) not in claimed_logs
            and entry.user_id not in known
            and -_LOG_EARLY_TOLERANCE_SEC
            <= (entry.timestamp - create).total_seconds() <= _LOG_STARTUP_WINDOW_SEC
        ]
        if len(candidates) == 1:
            user_ids[pid] = candidates[0].user_id

    return {
        pid: (uid_to_name.get(str(user_ids[pid]), "") if pid in user_ids else None)
        for pid in keys
    }


def locked_and_unknown(manager, locked: set[str], *, max_probes: int = DEFAULT_PROBES,
                       processes=None, quiet: bool = False) -> tuple[set[int], set[int]]:
    """PIDs of locked accounts' windows, and of windows that could not be identified."""
    if not locked:
        return set(), set()
    started = time.monotonic()
    names = identify_clients(manager, processes, max_probes=max_probes)
    locked_pids = {pid for pid, name in names.items() if name and name in locked}
    unknown = {pid for pid, name in names.items() if name is None}
    if not quiet:
        print(f"[INFO] Identified {len(names) - len(unknown)}/{len(names)} Roblox window(s) "
              f"in {time.monotonic() - started:.1f}s ({len(locked_pids)} locked, {len(unknown)} unknown)")
    return locked_pids, unknown
