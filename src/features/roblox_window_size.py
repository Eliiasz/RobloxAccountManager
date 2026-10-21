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
UNLOCK_SETTING = "roblox_window_allow_smaller"  # experimental: lift Roblox's minimum size
METHOD_SETTING = "roblox_window_method"   # resize method that worked in "Test resize"
SWP_NOSENDCHANGING = 0x0400   # skip WM_WINDOWPOSCHANGING, where minimum sizes are applied
METHODS = (
    ("normal", "Normal resize"),
    ("no_clamp", "Resize without the size check"),
    ("no_border", "Resize with the border removed"),
    ("no_border_no_clamp", "Border removed + no size check"),
    ("move_window", "Classic move-window call"),
)

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
_WATCH_INTERVAL = 0.5
_FIGHT_LIMIT = 10          # Roblox growing a window back this often per minute: give up on it
_FIGHT_WINDOW = 60.0
_SETTLE_SECONDS = 1.0     # let a new window finish appearing before resizing it
_TOLERANCE = 4


@dataclass(frozen=True)
class Choice:
    mode: str
    size: tuple[int, int]
    layout: tuple[int, int]
    allow_smaller: bool = False


@dataclass
class ApplyResult:
    asked: tuple[int, int] = (0, 0)
    resized: int = 0
    refused: tuple[int, int] | None = None   # what Roblox kept when it wouldn't shrink
    errors: list[str] | None = None

    def summary(self) -> str:
        if self.errors and not self.resized and not self.refused:
            return f"Couldn't resize: {self.errors[0]}"
        if self.refused:
            return (f"Roblox kept {self.refused[0]} × {self.refused[1]}: it won't go smaller than that "
                    "on this PC.")
        if not self.resized:
            return "No open Roblox windows to resize."
        return f"Resized {self.resized} window{'s' if self.resized != 1 else ''} to {self.asked[0]} × {self.asked[1]}."


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
        bool(settings.get(UNLOCK_SETTING, False)),
    )


def save_choice(choice: Choice) -> None:
    settings_store.save(MODE_SETTING, choice.mode)
    settings_store.save(SIZE_SETTING, list(choice.size))
    settings_store.save(LAYOUT_SETTING, list(choice.layout))
    settings_store.save(UNLOCK_SETTING, bool(choice.allow_smaller))


def refused_size() -> tuple[int, int] | None:
    value = settings_store.get(REFUSED_SETTING)
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError):
        return None


def below_minimum(size: tuple[int, int], minimum: tuple[int, int] | None) -> bool:
    return bool(minimum) and (size[0] < minimum[0] - _TOLERANCE or size[1] < minimum[1] - _TOLERANCE)


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


# Opening at the size ---------------------------------------------------------
# Roblox reopens at the window size saved in its settings file. Writing the
# chosen size there before each launch makes new windows open at that size
# themselves, instead of being resized afterwards.
START_SIZE_KEYS = ("StartScreenSize.X", "StartScreenSize.Y")
START_MAXIMIZED_KEY = "StartMaximized"


def launch_values(choice: Choice, work) -> dict[str, str]:
    if choice.mode == MODE_OFF:
        return {}
    width, height = target_size(choice, work)
    return {START_SIZE_KEYS[0]: str(width), START_SIZE_KEYS[1]: str(height), START_MAXIMIZED_KEY: "false"}


def write_launch_size(choice: Choice | None = None) -> str:
    """Before a launch: put the chosen size in Roblox's settings file. Returns a note."""
    choice = choice or load_choice()
    if choice.mode == MODE_OFF:
        return ""
    try:
        import features.roblox_settings as roblox_settings
        work = primary_work_area()
        result = roblox_settings.apply_values(launch_values(choice, work))
    except Exception as exc:
        return f"could not write the start size ({exc})"
    if not result:
        return f"could not write the start size ({result.message})"
    missing = (result.data or {}).get("missing") or []
    if set(START_SIZE_KEYS) & set(missing):
        return "Roblox's settings file has no start size yet (open and close Roblox once)"
    return ""


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


_unlocked: set[int] = set()   # windows whose resize border this app removed


def _set_resize_border(hwnd: int, on: bool) -> None:
    import win32con
    import win32gui
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    wanted = style | win32con.WS_THICKFRAME if on else style & ~win32con.WS_THICKFRAME
    if wanted != style:
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, wanted)
        win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                              | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)
    if on:
        _unlocked.discard(hwnd)
    else:
        _unlocked.add(hwnd)


def restore_borders() -> int:
    """Give back the resize border to every window the experimental option changed."""
    restored = 0
    for hwnd in list(_unlocked):
        try:
            _set_resize_border(hwnd, True)
            restored += 1
        except Exception:
            _unlocked.discard(hwnd)
    return restored


def _do_resize(hwnd: int, method: str, width: int, height: int, x: int | None, y: int | None) -> None:
    import win32con
    import win32gui
    if method in ("no_border", "no_border_no_clamp"):
        _set_resize_border(hwnd, False)
    elif hwnd in _unlocked:
        _set_resize_border(hwnd, True)
    if method == "move_window":
        if x is None or y is None:
            x, y, _w, _h = window_rect(hwnd)
        win32gui.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)
        return
    flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    if method in ("no_clamp", "no_border_no_clamp"):
        flags |= SWP_NOSENDCHANGING
    if x is None or y is None:
        flags |= win32con.SWP_NOMOVE
        x = y = 0
    win32gui.SetWindowPos(hwnd, 0, int(x), int(y), int(width), int(height), flags)


def current_method(allow_smaller: bool) -> str:
    if not allow_smaller:
        return "normal"
    saved = str(settings_store.get(METHOD_SETTING, "") or "")
    return saved if saved in dict(METHODS) else "no_border_no_clamp"


def resize_window(hwnd: int, width: int, height: int, x: int | None = None, y: int | None = None,
                  allow_smaller: bool = False, method: str | None = None) -> tuple[int, int]:
    """Resize (and optionally move) one window; returns the size Roblox actually kept.

    Roblox sets a minimum size, which Windows applies while handling the
    resize. allow_smaller uses the method found by "Test resize" (or skips
    that check and removes the resize border) to go below it (experimental).
    """
    import win32con
    import win32gui
    if win32gui.IsIconic(hwnd) or _is_maximized(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    _do_resize(hwnd, method or current_method(allow_smaller), width, height, x, y)
    _l, _t, actual_w, actual_h = window_rect(hwnd)
    if actual_w > width + _TOLERANCE or actual_h > height + _TOLERANCE:
        settings_store.save(REFUSED_SETTING, [actual_w, actual_h])
        _kept.pop(hwnd, None)
    else:
        if allow_smaller:
            _kept[hwnd] = (int(width), int(height))  # Roblox may grow it back; keep_sizes() fixes that
        minimum = refused_size()
        if minimum and below_minimum((actual_w, actual_h), minimum):
            settings_store.save(REFUSED_SETTING, None)  # it went smaller after all
    return actual_w, actual_h


_kept: dict[int, tuple[int, int]] = {}         # windows shrunk below Roblox's minimum
_fights: dict[int, list[float]] = {}
_given_up: set[int] = set()


def keep_sizes(now: float | None = None) -> list[int]:
    """Roblox puts its window back to its minimum when it is clicked. Shrink
    those windows again; stop for a window Roblox keeps fighting over."""
    now = time.monotonic() if now is None else now
    fixed = []
    for hwnd, (width, height) in list(_kept.items()):
        try:
            _l, _t, w, h = window_rect(hwnd)
        except Exception:
            _kept.pop(hwnd, None)
            continue
        if w <= width + _TOLERANCE and h <= height + _TOLERANCE:
            continue
        recent = [t for t in _fights.get(hwnd, []) if now - t < _FIGHT_WINDOW] + [now]
        _fights[hwnd] = recent
        if len(recent) > _FIGHT_LIMIT:
            _kept.pop(hwnd, None)
            _given_up.add(hwnd)
            print(f"[Window Size] Roblox keeps resizing a window back to {w} x {h}; stopped re-shrinking it.")
            continue
        try:
            resize_window(hwnd, width, height, allow_smaller=True)
            fixed.append(hwnd)
        except Exception:
            _kept.pop(hwnd, None)
    return fixed


def test_methods(hwnd: int, width: int, height: int) -> list[tuple[str, tuple[int, int], bool]]:
    """Try every resize method on one window, putting it back after each.

    Returns [(method, size kept, worked)] and remembers the first that worked.
    """
    import win32gui
    placement = win32gui.GetWindowPlacement(hwnd)
    results = []
    try:
        for method, _label in METHODS:
            try:
                _do_resize(hwnd, method, width, height, None, None)
                actual = window_rect(hwnd)[2:]
            except Exception as exc:
                print(f"[Window Size] Test {method}: {exc}")
                actual = (0, 0)
            worked = bool(actual[0]) and actual[0] <= width + _TOLERANCE and actual[1] <= height + _TOLERANCE
            results.append((method, tuple(actual), worked))
            try:
                _set_resize_border(hwnd, True)
                win32gui.SetWindowPlacement(hwnd, placement)
            except Exception:
                pass
    finally:
        try:
            win32gui.SetWindowPlacement(hwnd, placement)
        except Exception:
            pass
    best = next((method for method, _size, ok in results if ok), "")
    if best:
        settings_store.save(METHOD_SETTING, best)
    print("[Window Size] Test resize: " + ", ".join(
        f"{m} {'OK' if ok else 'kept'} {w}x{h}" for m, (w, h), ok in results))
    return results


def _is_maximized(hwnd: int) -> bool:
    try:
        import win32con
        import win32gui
        return win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED
    except Exception:
        return False


def apply(choice: Choice, windows: list[int] | None = None, *, layout_all: bool = True) -> ApplyResult:
    """Apply the choice. Fit mode lays the windows out in order, top-left first."""
    result = ApplyResult(errors=[])
    if choice.mode == MODE_OFF:
        restore_borders()
        return result
    windows = roblox_windows() if windows is None else windows
    work = primary_work_area()
    result.asked = target_size(choice, work)
    for index, hwnd in enumerate(windows):
        try:
            if choice.mode == MODE_FIT and layout_all:
                x, y, w, h = slot_rect(work, choice.layout, index)
                actual = resize_window(hwnd, w, h, x, y, choice.allow_smaller)
            else:
                w, h = target_size(choice, work)
                actual = resize_window(hwnd, w, h, allow_smaller=choice.allow_smaller)
            if actual[0] > w + _TOLERANCE or actual[1] > h + _TOLERANCE:
                result.refused = actual
            else:
                result.resized += 1
        except Exception as exc:
            result.errors.append(_explain(exc))
    print(f"[Window Size] {result.summary()}")
    return result


def _explain(exc: Exception) -> str:
    code = getattr(exc, "winerror", None) or (exc.args[0] if getattr(exc, "args", None) else None)
    if code == 5:
        return "Windows blocked it (is Roblox running as administrator?)"
    return str(exc) or type(exc).__name__


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
        resize_window(hwnd, w, h, x, y, choice.allow_smaller)
    elif choice.mode == MODE_SIZE:
        resize_window(hwnd, *choice.size, allow_smaller=choice.allow_smaller)


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
            _kept.clear()
            return []
        if choice.allow_smaller:
            keep_sizes(now)
        else:
            _kept.clear()
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
