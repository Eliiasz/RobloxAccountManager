"""
features/roblox_window_size.py
Make every Roblox window a chosen size (Settings > Roblox > Window Size).

Modes:
  off   Roblox decides (default, nothing is touched)
  size  a fixed window size, e.g. 320 x 240; the window keeps its position
  fit   a layout such as 5 x 4: the size is worked out from the screen and
        each new window drops into the next free spot, top-left first

Sizes are the whole window, title bar included, like Window Grid. Roblox may
refuse to go below some size; the real size is read back after resizing so
the app can say so instead of pretending.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

import features.settings_store as settings_store

MODE_SETTING = "roblox_window_mode"
SIZE_SETTING = "roblox_window_size"
LAYOUT_SETTING = "roblox_window_layout"
REFUSED_SETTING = "roblox_window_refused"   # smallest size Roblox actually kept

MODE_OFF, MODE_SIZE, MODE_FIT = "off", "size", "fit"
PRESET_SIZES = (
    (800, 600), (720, 540), (640, 480), (560, 420), (480, 360), (400, 300), (320, 240),   # 4:3
    (854, 480), (640, 360), (480, 270), (426, 240), (384, 270),                          # wide
)
LAYOUTS = ((3, 2), (3, 3), (4, 3), (4, 4), (5, 4), (6, 4), (6, 5), (8, 5))
DEFAULT_SIZE = (640, 480)
DEFAULT_LAYOUT = (5, 4)
MIN_SIDE = 160
MAX_SIDE = 3840
_GAP = 2
_WATCH_INTERVAL = 1.5
_SETTLE_SECONDS = 1.0     # let a new window finish appearing before resizing it
_TOLERANCE = 4


@dataclass(frozen=True)
class Choice:
    mode: str
    size: tuple[int, int]
    layout: tuple[int, int]


def _pair(value, default: tuple[int, int], low: int, high: int) -> tuple[int, int]:
    try:
        a, b = int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return default
    return max(low, min(high, a)), max(low, min(high, b))


def load_choice() -> Choice:
    settings = settings_store.load()
    mode = settings.get(MODE_SETTING, MODE_OFF)
    if mode not in (MODE_OFF, MODE_SIZE, MODE_FIT):
        mode = MODE_OFF
    return Choice(
        mode,
        _pair(settings.get(SIZE_SETTING), DEFAULT_SIZE, MIN_SIDE, MAX_SIDE),
        _pair(settings.get(LAYOUT_SETTING), DEFAULT_LAYOUT, 1, 12),
    )


def save_choice(choice: Choice) -> None:
    settings_store.save(MODE_SETTING, choice.mode)
    settings_store.save(SIZE_SETTING, list(choice.size))
    settings_store.save(LAYOUT_SETTING, list(choice.layout))


def fit_size(work: tuple[int, int, int, int], layout: tuple[int, int]) -> tuple[int, int]:
    left, top, right, bottom = work
    cols, rows = layout
    return max(1, (right - left) // cols), max(1, (bottom - top) // rows)


def slot_rect(work, layout, index: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of grid slot `index`, left to right, row by row."""
    left, top, _right, _bottom = work
    cols, rows = layout
    width, height = fit_size(work, layout)
    index %= max(1, cols * rows)
    col, row = index % cols, index // cols
    return left + col * width, top + row * height, width - _GAP, height - _GAP


def how_many_fit(work, size: tuple[int, int]) -> tuple[int, int]:
    left, top, right, bottom = work
    return max(0, (right - left) // max(1, size[0])), max(0, (bottom - top) // max(1, size[1]))


def target_size(choice: Choice, work) -> tuple[int, int]:
    if choice.mode == MODE_FIT:
        width, height = fit_size(work, choice.layout)
        return width - _GAP, height - _GAP
    return choice.size


def free_slot(work, layout, taken_rects) -> int:
    """First grid slot whose top-left corner no window is sitting on."""
    cols, rows = layout
    for index in range(cols * rows):
        x, y, _w, _h = slot_rect(work, layout, index)
        if not any(abs(r[0] - x) <= 12 and abs(r[1] - y) <= 12 for r in taken_rects):
            return index
    return len(list(taken_rects)) % max(1, cols * rows)


# Windows side ----------------------------------------------------------------

def primary_work_area() -> tuple[int, int, int, int]:
    import win32api
    import win32con
    monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
    return tuple(win32api.GetMonitorInfo(monitor)["Work"])


def roblox_windows() -> list[int]:
    """Visible Roblox game windows, the first opened first."""
    import features.presence as presence_mod
    import features.window_grid as window_grid_mod
    windows = window_grid_mod._get_roblox_windows()
    try:
        created = {pid: data[0] for pid, data in presence_mod.get_roblox_processes().items()}
        import win32process

        def _created(hwnd):
            try:
                return created.get(win32process.GetWindowThreadProcessId(hwnd)[1], 0.0)
            except Exception:
                return 0.0
        windows.sort(key=_created)
    except Exception:
        pass
    return windows


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right - left, bottom - top


def resize_window(hwnd: int, width: int, height: int, x: int | None = None, y: int | None = None) -> tuple[int, int]:
    """Resize (and optionally move) one window; returns the size Roblox actually kept."""
    import win32con
    import win32gui
    if win32gui.IsIconic(hwnd) or _is_maximized(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    if x is None or y is None:
        flags |= win32con.SWP_NOMOVE
        x = y = 0
    win32gui.SetWindowPos(hwnd, 0, int(x), int(y), int(width), int(height), flags)
    _l, _t, actual_w, actual_h = window_rect(hwnd)
    if actual_w > width + _TOLERANCE or actual_h > height + _TOLERANCE:
        settings_store.save(REFUSED_SETTING, [actual_w, actual_h])
        print(f"[Window Size] Roblox kept {actual_w} x {actual_h} (asked for {width} x {height}); "
              "it won't go smaller here.")
    return actual_w, actual_h


def _is_maximized(hwnd: int) -> bool:
    try:
        import win32con
        import win32gui
        return win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED
    except Exception:
        return False


def apply(choice: Choice, windows: list[int] | None = None, *, layout_all: bool = True) -> int:
    """Apply the choice. Fit mode lays the windows out in order, top-left first."""
    if choice.mode == MODE_OFF:
        return 0
    windows = roblox_windows() if windows is None else windows
    work = primary_work_area()
    done = 0
    for index, hwnd in enumerate(windows):
        try:
            if choice.mode == MODE_FIT and layout_all:
                x, y, w, h = slot_rect(work, choice.layout, index)
                resize_window(hwnd, w, h, x, y)
            else:
                w, h = target_size(choice, work)
                resize_window(hwnd, w, h)
            done += 1
        except Exception as exc:
            print(f"[Window Size] Could not resize a Roblox window: {exc}")
    return done


def place_new(choice: Choice, hwnd: int, others: list[int]) -> None:
    """A newly opened window: resize it, and in fit mode put it in the next free spot."""
    work = primary_work_area()
    if choice.mode == MODE_FIT:
        taken = []
        for other in others:
            try:
                taken.append(window_rect(other))
            except Exception:
                continue
        x, y, w, h = slot_rect(work, choice.layout, free_slot(work, choice.layout, taken))
        resize_window(hwnd, w, h, x, y)
    elif choice.mode == MODE_SIZE:
        resize_window(hwnd, *choice.size)


class WindowSizeWatcher:
    """Resizes each Roblox window once, shortly after it opens."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handled: set[int] = set()
        self._first_seen: dict[int, float] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            self._handled = set(roblox_windows())  # leave windows that were already open alone
        except Exception:
            self._handled = set()
        self._thread = threading.Thread(target=self._run, daemon=True, name="RobloxWindowSize")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(_WATCH_INTERVAL):
            try:
                self.tick()
            except Exception as exc:
                print(f"[Window Size] {exc}")

    def tick(self, now: float | None = None) -> list[int]:
        choice = load_choice()
        if choice.mode == MODE_OFF:
            return []
        now = time.monotonic() if now is None else now
        windows = roblox_windows()
        live = set(windows)
        self._handled &= live
        self._first_seen = {h: t for h, t in self._first_seen.items() if h in live}
        placed = []
        for hwnd in windows:
            if hwnd in self._handled:
                continue
            first = self._first_seen.setdefault(hwnd, now)
            if now - first < _SETTLE_SECONDS:
                continue
            others = [h for h in windows if h in self._handled]
            place_new(choice, hwnd, others)
            self._handled.add(hwnd)
            placed.append(hwnd)
        return placed
