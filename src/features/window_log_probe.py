"""Inspect external file handles in a disposable process, never in the UI app.

The Windows psutil scan may retain the GIL or hang in a native API. A thread
timeout cannot contain that failure. This module is also the frozen app's early
helper entry point; it must not initialize diagnostics, settings, or Qt.

One helper process serves every scan of a session. Starting the one-file build
unpacks about 150 MB into Temp, so a new copy per scan was slow (scans timed out)
and every copy stopped on a timeout left its unpack folder behind. Requests and
results are small files in a private folder: a windowed build has no stdout.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from utils.app_paths import clean_child_environment


HELPER_OPTION = "--window-log-probe"
PROBE_TIMEOUT_SECONDS = 3.0
# Covers unpacking the one-file build when the helper starts.
START_TIMEOUT_SECONDS = 30.0
# Time the one-file starter gets to delete its unpack folder after the helper ends.
STOP_TIMEOUT_SECONDS = 5.0
_POLL_SECONDS = 0.05
_MAX_RESULT_BYTES = 64 * 1024
_MAX_LOG_PATHS = 256
_READY = "ready"
_STOP = "stop"


@dataclass(frozen=True)
class ProbeResult:
    status: str
    paths: frozenset[str] = frozenset()


def _scan(pid: int, expected_create_time: float) -> dict:
    # Keep all psutil calls, including identity checks, inside the helper.
    import psutil

    try:
        process = psutil.Process(pid)
        if (
            abs(process.create_time() - expected_create_time) >= 0.01
            or process.name().casefold() != "robloxplayerbeta.exe"
        ):
            return {"status": "process_changed"}
        paths = sorted({
            os.path.normcase(os.path.abspath(opened.path))
            for opened in process.open_files()
            if opened.path.lower().endswith("_last.log")
        })
        # A PID may exit and be reused while the native scan is running.
        current = psutil.Process(pid)
        if abs(current.create_time() - expected_create_time) >= 0.01:
            return {"status": "process_changed"}
        if len(paths) > _MAX_LOG_PATHS:
            return {"status": "error"}
        return {"status": "ok", "pid": pid,
                "create_time": expected_create_time, "paths": paths}
    except psutil.NoSuchProcess:
        return {"status": "process_changed"}
    except (OSError, psutil.AccessDenied, psutil.ZombieProcess):
        return {"status": "error"}


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(json.dumps(payload).encode("utf-8"))
    os.replace(temporary, path)  # the reader never sees half a file


def _parent_alive(psutil, pid: int, create_time: float) -> bool:
    try:
        return abs(psutil.Process(pid).create_time() - create_time) < 0.01
    except psutil.Error:
        return False


def _serve(request: Path) -> None:
    request_id = request.stem[len("req-"):]
    try:
        raw = json.loads(request.read_bytes())
        pid, create_time = int(raw["pid"]), float(raw["create_time"])
        payload = _scan(pid, create_time) if pid > 0 and math.isfinite(create_time) \
            else {"status": "error"}
    except Exception:
        payload = {"status": "error"}
    try:
        request.unlink()
    except OSError:
        pass
    encoded = json.dumps(payload).encode("utf-8")
    if len(encoded) > _MAX_RESULT_BYTES:
        payload = {"status": "error"}
    _write_json(request.with_name(f"res-{request_id}.json"), payload)


def helper_main(arguments: list[str]) -> int:
    """Answer scan requests until the app closes (or asks the helper to stop)."""
    if len(arguments) != 3:
        return 2
    try:
        folder = Path(arguments[0])
        parent_pid = int(arguments[1])
        parent_created = float(arguments[2])
        if parent_pid <= 0 or not math.isfinite(parent_created) or not folder.is_dir():
            return 2
    except (ValueError, OSError):
        return 2
    try:
        import psutil

        _write_json(folder / _READY, {"status": "ready"})
        while True:
            if (folder / _STOP).exists() or not _parent_alive(psutil, parent_pid, parent_created):
                return 0
            requests = sorted(folder.glob("req-*.json"))
            for request in requests:
                _serve(request)
            if not requests:
                time.sleep(_POLL_SECONDS)
    except Exception:
        # The app treats a missing result as unknown evidence.
        return 1


def _read_result(path: str, key: tuple[int, float]) -> ProbeResult:
    with open(path, "rb") as handle:
        raw = handle.read(_MAX_RESULT_BYTES + 1)
    if len(raw) > _MAX_RESULT_BYTES:
        return ProbeResult("error")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return ProbeResult("error")
    if payload.get("status") == "process_changed":
        return ProbeResult("process_changed")
    if (
        payload.get("status") != "ok"
        or payload.get("pid") != key[0]
        or payload.get("create_time") != key[1]
    ):
        return ProbeResult("error")
    paths = payload.get("paths")
    if not isinstance(paths, list) or len(paths) > _MAX_LOG_PATHS:
        return ProbeResult("error")
    if any(not isinstance(path, str) or not os.path.isabs(path)
           or not path.lower().endswith("_last.log") for path in paths):
        return ProbeResult("error")
    return ProbeResult("ok", frozenset(os.path.normcase(path) for path in paths))


def _helper_command(folder: str) -> list[str]:
    import psutil

    arguments = [folder, str(os.getpid()), repr(psutil.Process().create_time())]
    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_OPTION, *arguments]
    source_root = Path(__file__).resolve().parents[1]
    return [sys.executable, str(source_root / "main.py"), HELPER_OPTION,
            *arguments]


def _end_process(process: subprocess.Popen) -> None:
    """Stop a helper so the one-file build can still delete its unpack folder.

    The .exe started here is only a starter that unpacks the app and runs it as
    its own child. Killing the starter would leave the folder in Temp; ending
    the child lets the starter delete the folder and exit by itself.
    """
    if process.poll() is not None:
        return
    try:
        import psutil

        children = psutil.Process(process.pid).children(recursive=True)
    except Exception:
        children = []
    for child in children:
        try:
            child.kill()
        except Exception:
            pass
    if not children:
        process.kill()
    try:
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


class _Helper:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._folder: Path | None = None
        self._next_id = 0

    def _running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _start(self, stop_event: threading.Event) -> str:
        self._reset()
        self._folder = Path(tempfile.mkdtemp(prefix="ram-window-probe-"))
        self._process = subprocess.Popen(
            _helper_command(str(self._folder)),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            # A clean environment: the helper is a fresh copy of the app and
            # must not reuse this copy's unpack folder (it would fail to start).
            env=clean_child_environment(),
        )
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while not (self._folder / _READY).exists():
            if stop_event.is_set():
                self._reset()
                return "cancelled"
            if self._process.poll() is not None:
                print(f"[WARNING] Window log helper failed to start (exit code {self._process.returncode}).")
                self._reset()
                return "error"
            if time.monotonic() >= deadline:
                print(f"[WARNING] Window log helper did not start within {START_TIMEOUT_SECONDS:g}s; it was stopped.")
                self._reset()
                return "timeout"
            stop_event.wait(_POLL_SECONDS)
        return "ok"

    def _reset(self) -> None:
        if self._process is not None:
            _end_process(self._process)
        self._process = None
        if self._folder is not None:
            shutil.rmtree(self._folder, ignore_errors=True)
        self._folder = None

    def probe(self, key: tuple[int, float], stop_event: threading.Event,
              timeout: float) -> ProbeResult:
        with self._lock:
            if stop_event.is_set():
                return ProbeResult("cancelled")
            if not self._running():
                status = self._start(stop_event)
                if status != "ok":
                    return ProbeResult(status)
            folder = self._folder
            for stale in folder.glob("res-*.json"):  # answers to cancelled scans
                try:
                    stale.unlink()
                except OSError:
                    pass
            self._next_id += 1
            request = folder / f"req-{self._next_id}.json"
            result = folder / f"res-{self._next_id}.json"
            _write_json(request, {"pid": key[0], "create_time": key[1]})
            deadline = time.monotonic() + max(0.0, timeout)
            while not result.exists():
                if stop_event.is_set():
                    try:
                        request.unlink()
                    except OSError:
                        pass
                    return ProbeResult("cancelled")
                if not self._running():
                    print("[WARNING] Window log helper stopped unexpectedly; it will be started again.")
                    self._reset()
                    return ProbeResult("error")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print(f"[WARNING] Window log helper did not finish within {timeout:g}s; it was stopped.")
                    self._reset()  # a hung scan: the next one starts a fresh helper
                    return ProbeResult("timeout")
                stop_event.wait(min(_POLL_SECONDS, remaining))
            try:
                return _read_result(str(result), key)
            except (OSError, ValueError):
                return ProbeResult("error")
            finally:
                try:
                    result.unlink()
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._running() and self._folder is not None:
                try:
                    (self._folder / _STOP).write_bytes(b"")
                    self._process.wait(timeout=STOP_TIMEOUT_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._reset()


_helper = _Helper()
atexit.register(_helper.close)


def probe_open_log_paths(
    key: tuple[int, float],
    stop_event: threading.Event,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Scan one Roblox process for its open log files, with a bounded wait."""
    try:
        return _helper.probe(key, stop_event, timeout)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return ProbeResult("error")
