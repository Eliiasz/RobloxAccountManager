"""
utils/ui.py
The v2.5.0 rewrite is finally complete.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import uuid
from dataclasses import replace

from utils import motion, icons, splash
from utils.app_paths import get_app_dir, get_data_dir, get_resource_path
from utils.version import APP_VERSION

_ROOT_DIR = get_app_dir()
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

import psutil

from PySide6.QtCore import (
    QEasingCurve, QEvent, QItemSelectionModel, QObject, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen,
    QIntValidator, QKeySequence, QPalette, QPixmap, QPolygon, QRegion, QShortcut, QTextCharFormat,
)
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
    QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSizeGrip, QSizePolicy, QDoubleSpinBox, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon,
    QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem,
)
from shiboken6 import isValid

from classes import (
    AccountDataError,
    AccountPasswordError,
    HardwareAccountDecryptionError,
    PasswordRequiredError,
    RobloxAccountManager,
)
from classes.encryption import EncryptionConfig, PasswordEncryption
from classes.operation_result import OperationResult, ensure_result
from classes.roblox_api import RobloxAPI

import features.account_actions as actions
import features.account_backup as account_backup
import features.auto_backup as auto_backup_mod
import features.account_creator as account_creator_mod
import features.auto_rejoin as ar
import features.account_filter as account_filter
import features.account_order as account_order
import features.avatars as avatars
import features.color_themes as color_themes
import features.cookie_validator as cookie_validator_mod
import features.roblox_install_guard as install_guard_mod
import features.pc_usage as pc_usage_mod
import features.roblox_window_size as window_size_mod
import features.file_holders as file_holders_mod
import features.chromium as chromium_mod
import features.diagnostics as diagnostics
import features.favorites as favorites_mod
import features.groups as groups
import features.game_selector as game_selector_mod
import features.headless_manager as headless_manager_mod
import features.presence as presence_mod
import features.private_servers as private_servers_mod
import features.proxy_manager as proxy_mod
import features.account_health as health_mod
import features.roblox_downloader as roblox_downloader_mod
import features.roblox_settings as roblox_settings_mod
import features.updater as updater_mod
import features.websocket_server as ws_mod
import features.window_grid as window_grid_mod
import features.window_renamer as window_renamer_mod
import features.windows_startup as windows_startup_mod
import features.app_restart as app_restart


class _DragDropFilter(QObject):
    reorder_requested = Signal(int, int) # (from_row, insert_before_row)
    HOLD_MS = 800 # hold duration before drag starts (ms)
    CANCEL_PX = 8 # pixel slop before hold is cancelled

    def __init__(self, list_widget: "QListWidget", get_avatar=None, parent=None):
        super().__init__(parent)
        self._list = list_widget
        self._get_avatar = get_avatar # Callable[[str], QPixmap|None] | None

        self._press_pos = QPoint()
        self._press_row = -1
        self._username = ""

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_confirmed)

        self._dragging = False
        self._drag_row = -1
        self._drop_row = -1 # insert-before index (0 = top)

        self._float_win: QFrame | None = None # floating window with username + avatar
        self._viewport = list_widget.viewport() # avoid calling viewport() on a deleted C++ object at teardown

        self._indicator = QFrame(self._viewport)
        self._indicator.setFixedHeight(2)
        self._indicator.setStyleSheet(f"background: {FG_ACCENT}; border: none;")
        self._indicator.hide()

    def eventFilter(self, obj, event):
        if obj is not self._viewport:
            return False
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            return self._on_press(event)
        if t == QEvent.Type.MouseMove:
            return self._on_move(event)
        if t == QEvent.Type.MouseButtonRelease:
            return self._on_release(event)
        return False

    def _on_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        local_pos = event.position().toPoint()
        item = self._list.itemAt(local_pos)
        if item is None:
            return False
        username = item.data(Qt.ItemDataRole.UserRole)
        if not username:
            return False
        self._press_pos = event.globalPosition().toPoint()
        self._press_row = self._list.row(item)
        self._username = username
        self._hold_timer.start(self.HOLD_MS)
        return False

    def _on_hold_confirmed(self):
        self._dragging = True
        self._drag_row = self._press_row
        self._drop_row = self._press_row

        cursor_pos = QCursor.pos()
        self._create_float(self._username, cursor_pos)

        if self._get_avatar:
            pix = self._get_avatar(self._username)
            if pix and not pix.isNull():
                self.update_float_avatar(pix)

        self._list.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _on_move(self, event) -> bool:
        gpos = event.globalPosition().toPoint()

        if not self._dragging:
            if self._hold_timer.isActive() and not self._press_pos.isNull():
                if (gpos - self._press_pos).manhattanLength() > self.CANCEL_PX:
                    self._hold_timer.stop()
            return False

        if self._float_win: # Move floating window
            self._float_win.move(gpos.x() + 4, gpos.y() + 4)

        self._drop_row = self._compute_drop_row(event.position().toPoint()) # compute drop-before row from local pos
        self._show_indicator(self._drop_row)
        return True # consume during drag

    def _on_release(self, event) -> bool:
        self._hold_timer.stop()

        if not self._dragging:
            return False

        self._dragging = False
        self._list.unsetCursor()

        if self._float_win:
            self._float_win.hide()
        self._indicator.hide()

        from_row = self._drag_row
        to_row = self._drop_row

        self._drag_row = -1
        self._drop_row = -1

        if to_row >= 0 and from_row >= 0 and from_row != to_row:
            self.reorder_requested.emit(from_row, to_row)

        return True

    def _create_float(self, username: str, initial_pos: "QPoint | None" = None):
        if self._float_win:
            self._float_win.hide()
            self._float_win.deleteLater()

        win = QFrame(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        win.setStyleSheet(f"""
            QFrame {{
                background: {PRESSED};
                border: 1px solid {HOVER};
                border-radius: 6px;
            }}
            QLabel {{ background: transparent; color: {TEXT}; }}
        """)

        h = QHBoxLayout(win)
        h.setContentsMargins(8, 6, 12, 6)
        h.setSpacing(8)

        # Avatar slot
        av_lbl = QLabel()
        av_lbl.setFixedSize(22, 22)
        av_lbl.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter
        )
        av_lbl.setStyleSheet(
            f"background: {SELECT}; border-radius: 11px;"
        )
        h.addWidget(av_lbl)
        self._float_av = av_lbl

        name = QLabel(username)
        name.setStyleSheet("font-size: 12px; font-weight: 600;")
        h.addWidget(name)

        win.adjustSize()
        if initial_pos is not None:
            win.move(initial_pos.x() + 4, initial_pos.y() + 4)
        self._float_win = win
        win.show()

    def update_float_avatar(self, pixmap: "QPixmap"):
        if self._float_win and self._float_av:
            scaled = pixmap.scaled(
                22, 22,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._float_av.setPixmap(scaled)
            self._float_av.setStyleSheet("background: transparent;")


    def _compute_drop_row(self, local_pos) -> int:
        count = self._list.count()
        if count == 0:
            return 0

        for row in range(count):
            item = self._list.item(row)
            if item is None:
                continue
            rect = self._list.visualItemRect(item)
            mid = rect.top() + rect.height() // 2
            if local_pos.y() < mid:
                return row

        return count # below last item

    def _show_indicator(self, insert_before: int):
        count = self._list.count()
        if count == 0:
            self._indicator.hide()
            return

        if insert_before <= 0:
            rect = self._list.visualItemRect(self._list.item(0))
            y = rect.top()
        elif insert_before >= count:
            rect = self._list.visualItemRect(self._list.item(count - 1))
            y = rect.bottom()
        else:
            rect = self._list.visualItemRect(self._list.item(insert_before))
            y = rect.top()

        w = self._list.viewport().width()
        self._indicator.setGeometry(0, y - 1, w, 2)
        self._indicator.raise_()
        self._indicator.show()

    def abort(self):
        self._hold_timer.stop()
        self._dragging = False
        self._drag_row = -1
        self._drop_row = -1
        self._list.unsetCursor()
        if self._float_win:
            self._float_win.hide()
            self._float_win.deleteLater()
            self._float_win = None
        self._indicator.hide()

    def cleanup(self):
        self.abort()


class _ComboRightClickFilter(QObject):
    # Right-clicking a QComboBox popup normally closes it before a context
    # menu can show. Consuming the press here keeps the popup open underneath.
    right_clicked = Signal(QPoint)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(event.pos())
            return True
        return False


class _ActionComboBox(QComboBox):
    action_requested = Signal()

    def __init__(self, action_text, parent=None):
        super().__init__(parent)
        self._action_menu = QMenu(self)
        self._action_menu.addAction(action_text, self.action_requested.emit)
        self._action_menu.aboutToHide.connect(self._menu_hidden)
        self._menu_hidden_at = 0.0

    def _menu_hidden(self):
        self._menu_hidden_at = time.monotonic()

    def showPopup(self):
        if self._action_menu.isVisible():
            self._action_menu.hide()
            return
        if time.monotonic() - self._menu_hidden_at < 0.2:
            return
        menu_width = self._action_menu.sizeHint().width()
        menu_x = max(0, self.width() - menu_width)
        self._action_menu.popup(self.mapToGlobal(QPoint(menu_x, self.height())))


# Thread to Qt signal bridge
class _Bridge(QObject):
    account_added = Signal(object) # OperationResult from add-account worker
    window_size_done = Signal(str) # result line for Settings > Roblox > Window Size
    window_size_test_done = Signal(str, bool) # (report, a method worked)
    account_creator_done = Signal(bool, str, object) # (success, summary, report dict or None)
    account_creator_progress = Signal(int, int, int) # (created, finished, requested)
    backup_done = Signal(object) # OperationResult from "Backup now"
    game_name_ready = Signal(str) # display text for current-place label
    launch_done = Signal(object) # OperationResult from any join/launch worker
    avatar_ready = Signal(str, object) # (username, image_bytes) from avatar worker
    rejoin_status = Signal(str, str) # (account, status_str) from rejoin worker
    afk_tooltip = Signal(str, int, int) # (message, x, y) pass None to hide
    mr_download_done = Signal(bool) # (success) from download_handle64 worker
    shortcut_toggle_done = Signal(str, bool, object) # (kind, enabled, OperationResult) from a shortcut worker
    chromium_progress = Signal(int, str) # (percent 0-100, label text) from chromium download
    chromium_done = Signal(object) # OperationResult from Chromium download
    chromium_status = Signal(object) # OperationResult from latest build check
    roblox_download_progress = Signal(int, str) # (percent 0-100, current operation)
    roblox_download_done = Signal(bool, str, str) # (success, result_type, message)
    presence_update = Signal(object) # set[str] of online usernames
    cookie_validated = Signal(str, str) # (username, status) from validator worker
    update_available = Signal(str) # (latest_version) from update check worker
    update_check_finished = Signal(object) # OperationResult from a manual update check
    update_progress = Signal(int) # (pct 0-100) from auto download worker
    update_done = Signal(bool, str) # (success, error_msg) from auto download worker
    join_place_resolved = Signal(object) # dict payload from Place ID resolution worker
    recent_game_saved = Signal() # a recent-game entry was written and needs a list refresh
    favorite_place_resolved = Signal(object) # dict payload from Save Current Game resolution
    headless_update = Signal(object) # list[dict] of running Roblox processes from Headless Manager scan
    headless_avatar_ready = Signal(int, object) # (pid, image_bytes) from Headless Manager avatar worker
    roblox_settings_loaded = Signal(object) # OperationResult from Roblox settings load
    roblox_settings_applied = Signal(object) # OperationResult from Roblox settings apply
    roblox_settings_auto_applied = Signal(object) # OperationResult from Roblox settings Auto Apply
    roblox_settings_path_changed = Signal(object)
    console_wakeup = Signal()
    proxy_test_done = Signal(bool, str) # (success, message) from proxy test worker
    proxy_ping_done = Signal(bool, str) # (success, "123 ms") from proxy ping worker
    ban_check_result = Signal(object, int, int) # (HealthResult, index, total) from ban check worker
    ban_check_done = Signal(object) # summary dict from ban check worker
    kill_roblox_done = Signal(object) # OperationResult from Kill All Roblox worker
    queue_poll_done = Signal(int) # running Roblox client count for the batch queue
    perf_applied = Signal(object) # OperationResult from Performance "Apply now"
    roblox_count_ready = Signal(int) # number of Roblox windows running, for the Account List header
    place_icon_ready = Signal(str, object) # (place_id, image bytes) for the Current game card


SKIPPED_UPDATE_SETTING = "skipped_update_version"
_THEME_NAME = color_themes.theme_name(actions.load_ui_settings())
_THEME = color_themes.resolve_colors(actions.load_ui_settings())
BG = _THEME["BG"]
PANEL = _THEME["PANEL"]
INPUT = _THEME["INPUT"]
TEXT = _THEME["TEXT"]
MUTED = _THEME["MUTED"]
LINE = _THEME["LINE"]
SELECT = _THEME["SELECT"]
NOTE = _THEME["NOTE"]
FG_ACCENT = _THEME["ACCENT"]
HOVER = _THEME["HOVER"]
PRESSED = _THEME["PRESSED"]
RAISED = _THEME["RAISED"]
ACCENT_HOVER = _THEME["ACCENT_HOVER"]
ACCENT_PRESSED = _THEME["ACCENT_PRESSED"]
ACCENT_BRIGHT = _THEME["ACCENT_BRIGHT"]
ACCENT_SOFT = _THEME["ACCENT_SOFT"]
ACCENT_TEXT = _THEME["ACCENT_TEXT"]
ACCENT_DEEP = _THEME["ACCENT_DEEP"]
ON_ACCENT = _THEME["ON_ACCENT"]

# Redesign surfaces (2.9.0): rounded cards, a darker list well and a soft
# accent tint for the selected sidebar item and highlighted rows.
LIST_BG = color_themes.mix(PANEL, BG, 0.5)
ACCENT_TINT = color_themes.mix(PANEL, FG_ACCENT, 0.16)
ACCENT_TINT_LINE = color_themes.mix(PANEL, FG_ACCENT, 0.42)
ACCENT_TINT_HOVER = color_themes.mix(PANEL, FG_ACCENT, 0.24)
PRIMARY_TOP = color_themes.mix(FG_ACCENT, "#FFFFFF", 0.12)
PRIMARY_BOTTOM = color_themes.mix(FG_ACCENT, "#000000", 0.14)
PRIMARY_HOVER_TOP = color_themes.mix(FG_ACCENT, "#FFFFFF", 0.24)
PRIMARY_HOVER_BOTTOM = color_themes.mix(FG_ACCENT, "#FFFFFF", 0.04)
PRIMARY_PRESSED = color_themes.mix(FG_ACCENT, "#000000", 0.22)
# White text on the orange buttons like the design; fall back to the theme's
# readable color when a custom accent is too light for white.
PRIMARY_TEXT = "#FFFFFF" if color_themes.contrast_ratio("#FFFFFF", PRIMARY_BOTTOM) >= 2.8 else ON_ACCENT
SCROLL_HANDLE = color_themes.mix(PANEL, TEXT, 0.16)
# 2.11.0 look: cards are lit slightly from the top, their border fades from a
# soft accent glow at the top into the plain outline, and fields glow on focus.
THEME_IS_LIGHT = color_themes.is_light(_THEME_NAME)
CARD_TOP = color_themes.mix(PANEL, TEXT, 0.012 if THEME_IS_LIGHT else 0.03)
CARD_GLOW = color_themes.mix(LINE, FG_ACCENT, 0.45)
FIELD_GLOW = color_themes.mix(LINE, FG_ACCENT, 0.65)
TITLE_ICON_ACTIVE = TEXT if THEME_IS_LIGHT else "#FFFFFF"
SCROLL_HANDLE_HOVER = color_themes.mix(PANEL, TEXT, 0.28)
ONLINE_GREEN = "#1E9E57" if THEME_IS_LIGHT else "#2ECC71"


def _status_color(color: str) -> str:
    """Bright status colours (green/blue/red) are darkened on a light theme so they stay readable."""
    return color_themes.mix(color, "#000000", 0.38) if THEME_IS_LIGHT else color


RAM_LIMIT_DEFAULT_MB = 300  # "Low RAM Limit"; the Boost switch itself stays off by default
PRIMARY_BUTTON_QSS = (
    f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {PRIMARY_TOP}, stop:1 {PRIMARY_BOTTOM});"
    f" border: 0; border-radius: 10px; min-height: 42px; padding: 0 14px; text-align: center;"
    f" color: {PRIMARY_TEXT}; font-size: 14px; font-weight: 700; }}"
    f"QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
    f" stop:0 {PRIMARY_HOVER_TOP}, stop:1 {PRIMARY_HOVER_BOTTOM}); }}"
    f"QPushButton:pressed {{ background: {PRIMARY_PRESSED}; }}"
    f"QPushButton:disabled {{ background: {RAISED}; color: {MUTED}; }}"
)

# Account Health statuses that get a "tick by status" button (never OK).
HEALTH_PICK_STATUSES = (
    health_mod.BANNED, health_mod.TERMINATED, health_mod.LOCKED,
    health_mod.NEEDS_VERIFY, health_mod.UNKNOWN,
)
SETTINGS_MISC_TAB = 3  # index of "Misc" in the Settings categories
ENCRYPTION_TIPS = {
    "Device Locked": "Your saved accounts are encrypted with a key tied to this PC.\n"
                     "They only open on this computer.",
    "Password Locked": "Your saved accounts are encrypted with your password.\n"
                       "They open on any PC, but only with that password.",
    "Unprotected": "Your saved accounts are stored without encryption.\n"
                   "Anyone with access to this PC's files could read them.",
}
WINDOW_SIZE_MODES = (("off", "Off (Roblox decides)"), ("size", "Fixed size"), ("fit", "Fit to screen"))
PC_USAGE_SETTING = "pc_usage_show"
PC_USAGE_INTERVAL_MS = 1000
PC_RAM_WARN = 85.0
PC_CPU_WARN = 90.0
PC_WARN_COLOR = "#F0A030"
KILL_ALL_TOOLTIP = (
    "Instantly close every running Roblox window (no confirmation). Locked accounts stay open.\n"
    "Right-click to turn the Kill All shortcut key on or off."
)
QUEUE_SELECT_FAILED_SETTING = "queue_select_failed"
TITLE_BAR_HEIGHT = 66
ROW_AVATAR_SIZE = 36
MIN_WINDOW_SIZE = QSize(1080, 720)
DEFAULT_WINDOW_SIZE = QSize(1240, 820)

_dropdown_arrow_cache: dict[str, str] = {}
_checkmark_cache: dict[str, str] = {}


def _checkmark_icon_path(color: str) -> str:
    cached = _checkmark_cache.get(color)
    if cached and os.path.exists(cached):
        return cached
    path = os.path.join(tempfile.gettempdir(), f"ram_checkmark_{color.strip('#')}.png")
    if not os.path.exists(path):
        pix = QPixmap(36, 36)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path_obj = QPainterPath()
        path_obj.moveTo(9, 19)
        path_obj.lineTo(15.5, 25.5)
        path_obj.lineTo(27, 11)
        painter.drawPath(path_obj)
        painter.end()
        pix.save(path, "PNG")
    _checkmark_cache[color] = path
    return path


def _dropdown_arrow_icon_path(color: str) -> str:
    cached = _dropdown_arrow_cache.get(color)
    if cached and os.path.exists(cached):
        return cached

    path = os.path.join(tempfile.gettempdir(), f"ram_dropdown_arrow_{color.strip('#')}.png")
    if not os.path.exists(path):
        pix = QPixmap(10, 10)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(1, 3), QPoint(9, 3), QPoint(5, 8)]))
        painter.end()
        pix.save(path, "PNG")

    _dropdown_arrow_cache[color] = path
    return path

class _FloatingTooltip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.ToolTip |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size_percent = 100
        self.set_size_percent(self._size_percent)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)

        self._update_timer = QTimer(self)
        self._update_timer.setInterval(150)
        self._update_timer.timeout.connect(self._follow_cursor)

        self.hide()

    def set_size_percent(self, size_percent: int):
        self._size_percent = max(50, min(200, int(size_percent)))
        scale = self._size_percent / 100
        font_size = max(8, round(11 * scale))
        vertical_padding = max(3, round(5 * scale))
        horizontal_padding = max(6, round(12 * scale))
        self._label.setStyleSheet(
            f"QLabel {{ color: {TEXT}; background-color: {PANEL};"
            f" border: 1px solid {LINE}; border-radius: 4px;"
            f" padding: {vertical_padding}px {horizontal_padding}px;"
            f" font-size: {font_size}px; font-weight: bold; }}"
        )
        self.adjustSize()

    def show_message(self, message: str, _x: int, _y: int):
        if not message:
            self._update_timer.stop()
            super().hide()
            return
        self._label.setText(message)
        self.adjustSize()
        cursor = QCursor.pos()
        self._place_at(cursor.x(), cursor.y())
        super().show()
        self._update_timer.start()

    def hide(self):
        self._update_timer.stop()
        super().hide()

    def _place_at(self, x: int, y: int):
        sx, sy = x + 5, y + 5
        screen = QApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            sx = min(sx, geo.right()  - self.width()  - 8)
            sy = min(sy, geo.bottom() - self.height() - 8)
            sx = max(sx, geo.left() + 8)
            sy = max(sy, geo.top()  + 8)
        self.move(sx, sy)

    def _follow_cursor(self):
        if not self.isVisible():
            self._update_timer.stop()
            return
        cursor = QCursor.pos()
        self._place_at(cursor.x(), cursor.y())

class _HotkeyCaptureButton(QPushButton):
    recording_started = Signal()
    recording_canceled = Signal()
    sequence_changed = Signal(str)

    _MODIFIER_KEYS = {
        Qt.Key.Key_Control,
        Qt.Key.Key_Shift,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Meta,
    }

    def __init__(self, sequence: str, parent=None):
        super().__init__(parent)
        self._sequence = sequence or window_grid_mod.DEFAULT_HOTKEY
        self._recording = False
        self._pending_sequence = ""
        self._pending_key = 0
        self.setText(self._sequence)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._begin_recording)

    def set_sequence(self, sequence: str) -> None:
        self._sequence = sequence or window_grid_mod.DEFAULT_HOTKEY
        if not self._recording:
            self.setText(self._sequence)

    def _begin_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._pending_sequence = ""
        self._pending_key = 0
        self.setText("Recording...")
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        self.recording_started.emit()

    def _finish_recording(self) -> None:
        sequence = self._pending_sequence
        self._recording = False
        self._pending_sequence = ""
        self._pending_key = 0
        self.releaseKeyboard()
        self._sequence = sequence
        self.setText(sequence)
        self.sequence_changed.emit(sequence)

    def _cancel_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._pending_sequence = ""
        self._pending_key = 0
        self.releaseKeyboard()
        self.setText(self._sequence)
        self.recording_canceled.emit()

    def keyPressEvent(self, event) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        if event.isAutoRepeat():
            event.accept()
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel_recording()
            event.accept()
            return
        if key in self._MODIFIER_KEYS:
            event.accept()
            return

        sequence = QKeySequence(event.keyCombination()).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        if sequence:
            self._pending_sequence = sequence
            self._pending_key = key
            self.setText("Release to assign")
        event.accept()

    def keyReleaseEvent(self, event) -> None:
        if not self._recording:
            super().keyReleaseEvent(event)
            return
        if (
            self._pending_sequence
            and event.key() == self._pending_key
        ):
            self._finish_recording()
        event.accept()

    def focusOutEvent(self, event) -> None:
        if self._recording:
            self._cancel_recording()
        super().focusOutEvent(event)


class _PrivateServerManagerDialog(QDialog):
    loaded = Signal(int, object)
    progress = Signal(int, object)
    link_ready = Signal(int, str, object)
    apply_server = Signal(str, str)

    def __init__(self, manager, selected, parent):
        super().__init__(parent)
        self.manager = manager
        self._cancel = None
        self._link_cancel = None
        self._pending_action = ''
        self._generation = 0
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Private Server Manager')
        self.setWindowIcon(parent.windowIcon())
        self.resize(680, 420)
        self.setMinimumSize(520, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        row.addWidget(QLabel('Account'))
        self.account = QComboBox()
        self.account.addItems(list(manager.accounts))
        if selected:
            self.account.setCurrentText(selected)
        row.addWidget(self.account, 1)
        row.addWidget(QLabel('Place ID'))
        self.place = QLineEdit()
        self.place.setPlaceholderText('Optional')
        row.addWidget(self.place, 1)
        self.refresh = QPushButton('Refresh')
        self.refresh.clicked.connect(self._load)
        row.addWidget(self.refresh)
        layout.addLayout(row)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(['Game', 'Server Name', 'Status'])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.list.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.list, 1)
        self.status = QLabel('')
        self.status.setStyleSheet(f'color: {MUTED}; background: transparent;')
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.copy = QPushButton('Copy Link')
        self.copy.clicked.connect(self._copy)
        self.apply = QPushButton('Apply to Field')
        self.apply.clicked.connect(self._apply)
        buttons.addWidget(self.copy)
        buttons.addWidget(self.apply)
        buttons.addStretch(1)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.loaded.connect(self._on_loaded)
        self.progress.connect(self._on_progress)
        self.link_ready.connect(self._on_link_ready)
        self.account.currentIndexChanged.connect(self._load)
        self.place.returnPressed.connect(self._load)
        self.place.textChanged.connect(self._invalidate)
        self._selection_changed()
        QTimer.singleShot(0, self._load)

    def _invalidate(self):
        self._generation += 1
        if self._cancel:
            self._cancel.set()
        if self._link_cancel:
            self._link_cancel.set()
            self._link_cancel = None
        self._pending_action = ''
        self.list.clear()
        self._row_ids = set()
        self.refresh.setEnabled(True)
        self.status.setText('Press Refresh to load servers.')
        self._selection_changed()

    def _load(self):
        self._invalidate()
        username = self.account.currentText()
        if not username:
            self.status.setText('No saved accounts.')
            return
        generation = self._generation
        self.refresh.setEnabled(False)
        self.status.setText('Loading private servers...')
        self._cancel = private_servers_mod.start_load(
            self.manager, username, self.place.text().strip(),
            lambda result: self.loaded.emit(generation, result),
            lambda rows: self.progress.emit(generation, rows),
        )

    def _on_progress(self, generation, rows):
        if generation != self._generation:
            return
        self._append_rows(rows)
        self.status.setText(f'{self.list.topLevelItemCount()} private server(s). Loading...')

    def _on_loaded(self, generation, result):
        if generation != self._generation:
            return
        self.refresh.setEnabled(True)
        if not result:
            self.status.setText('Could not load private servers.')
            self.parentWidget()._show_operation_error(result)
            return
        self._append_rows(result.data)
        self.status.setText(f'{len(result.data)} private server(s).' if result.data else 'No owned private servers found.')

    def _append_rows(self, rows):
        for row in rows:
            row_id = row.get('id') or row['link']
            if row_id in self._row_ids:
                continue
            self._row_ids.add(row_id)
            item = QTreeWidgetItem([row['game'], row['name'], row['status']])
            item.setData(0, Qt.ItemDataRole.UserRole, row['link'])
            item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, dict(row))
            if not row['link']:
                item.setToolTip(0, 'Roblox did not provide a join link for this server.')
            self.list.addTopLevelItem(item)
        if not self.list.currentItem() and self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        self._selection_changed()

    def _selected_link(self):
        item = self.list.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ''

    def _selected_row(self):
        item = self.list.currentItem()
        return item.data(0, int(Qt.ItemDataRole.UserRole) + 1) if item else None

    def _selection_changed(self):
        enabled = self.list.currentItem() is not None and not self._pending_action
        self.copy.setEnabled(enabled)
        self.apply.setEnabled(enabled)
        reason = ''
        if enabled and not self._selected_link():
            reason = 'This server needs a new join link.'
        self.copy.setToolTip(reason)
        self.apply.setToolTip(reason)

    def _copy(self):
        self._use_link('copy')

    def _apply(self):
        self._use_link('apply')

    def _use_link(self, action):
        link = self._selected_link()
        row = self._selected_row() or {}
        if link:
            self._finish_action(action, str(row.get('place_id', '')), link)
            return
        if not row or self._pending_action:
            return
        reply = QMessageBox.question(
            self,
            'Generate Private Server Link',
            'This server has no join link. Generate a new join code?\n\n'
            'This replaces the server\'s current join code on Roblox.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._pending_action = action
        self._selection_changed()
        self.status.setText('Generating private server link...')
        generation = self._generation
        server_id = str(row.get('id', ''))
        self._link_cancel = private_servers_mod.start_generate_link(
            self.manager,
            self.account.currentText(),
            server_id,
            str(row.get('place_id', '')),
            lambda result: self.link_ready.emit(generation, server_id, result),
        )

    def _on_link_ready(self, generation, server_id, result):
        if generation != self._generation:
            return
        action = self._pending_action
        self._pending_action = ''
        self._link_cancel = None
        if not result:
            self.status.setText('Could not generate private server link.')
            self._selection_changed()
            self.parentWidget()._show_operation_error(result)
            return
        link = str(result.data or '')
        place_id = ''
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            row = item.data(0, int(Qt.ItemDataRole.UserRole) + 1) or {}
            if str(row.get('id', '')) == server_id:
                place_id = str(row.get('place_id', ''))
                row['link'] = link
                item.setData(0, Qt.ItemDataRole.UserRole, link)
                item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, row)
                break
        self._selection_changed()
        self._finish_action(action, place_id, link)

    def _finish_action(self, action, place_id, link):
        if action == 'copy':
            QApplication.clipboard().setText(link)
            self.status.setText('Link copied.')
        elif action == 'apply':
            self.apply_server.emit(place_id, link)
            self.accept()

    def done(self, result):
        self._generation += 1
        if self._cancel:
            self._cancel.set()
        if self._link_cancel:
            self._link_cancel.set()
        super().done(result)


class _DetachablePageHost(QWidget):
    def __init__(self, page_index: int, page_name: str, parent=None):
        super().__init__(parent)
        self.page_index = page_index
        self.page_name = page_name
        self._page: QWidget | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        root.addWidget(self._content, 1)

        self._placeholder = QFrame()
        self._placeholder.setStyleSheet(f"background: {BG}; border: 0;")
        placeholder_layout = QVBoxLayout(self._placeholder)
        placeholder_layout.setContentsMargins(24, 24, 24, 24)
        placeholder_layout.setSpacing(10)
        placeholder_layout.addStretch(1)

        message = QLabel(f"{page_name} is open in another window.")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        placeholder_layout.addWidget(message)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.show_button = QPushButton("Show Window")
        self.show_button.setFixedHeight(26)
        self.show_button.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 2px 10px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
        )
        button_row.addWidget(self.show_button)
        self.reattach_button = QPushButton("Reattach")
        self.reattach_button.setFixedHeight(26)
        self.reattach_button.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 2px 10px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
        )
        button_row.addWidget(self.reattach_button)
        button_row.addStretch(1)
        placeholder_layout.addLayout(button_row)
        placeholder_layout.addStretch(1)
        self._content_layout.addWidget(self._placeholder)
        self._placeholder.hide()

    def set_page(self, page: QWidget) -> None:
        if self._page is page:
            return
        if self._page is not None:
            self._content_layout.removeWidget(self._page)
        self._page = page
        page.setParent(self._content)
        self._content_layout.addWidget(page)
        self._placeholder.hide()

    def take_page(self) -> QWidget | None:
        page = self._page
        if page is None:
            return None
        self._content_layout.removeWidget(page)
        page.setParent(None)
        self._page = None
        self._content_layout.addWidget(self._placeholder)
        self._placeholder.show()
        return page

    def restore_page(self, page: QWidget) -> None:
        self._content_layout.removeWidget(self._placeholder)
        self.set_page(page)


class _DetachedPageWindow(QMainWindow):
    reattach_requested = Signal(int)

    def __init__(
        self,
        page_index: int,
        page_name: str,
        page: QWidget,
        icon: QIcon,
        stylesheet: str,
        parent=None,
    ):
        super().__init__(parent)
        self.page_index = page_index
        self.page_name = page_name
        self._page = page
        self._allow_close = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.setWindowTitle(f"{page_name} - ANAne RAM")
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setStyleSheet(stylesheet)
        self.resize(640, 520)
        self.setMinimumSize(480, 360)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._page_container = QWidget()
        self._page_layout = QVBoxLayout(self._page_container)
        self._page_layout.setContentsMargins(0, 0, 0, 0)
        self._page_layout.setSpacing(0)
        page.setParent(self._page_container)
        self._page_layout.addWidget(page)
        root.addWidget(self._page_container, 1)

    def release_page(self) -> QWidget | None:
        page = self._page
        if page is None:
            return None
        self._page_layout.removeWidget(page)
        page.setParent(None)
        self._page = None
        return page

    def allow_close(self) -> None:
        self._allow_close = True

    def closeEvent(self, event) -> None:
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()
        self.reattach_requested.emit(self.page_index)

class _ThemeCard(QAbstractButton):
    """One theme in the Settings gallery: a small preview of its colours and its name."""

    def __init__(self, key: str, label: str, colors: dict, parent=None):
        super().__init__(parent)
        self.key = key
        self._label = label
        self._colors = colors
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(118)
        self.setMinimumWidth(150)
        self.setAccessibleName(f"{label} theme")
        self.setToolTip(label)

    def paintEvent(self, event):
        c = self._colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.isChecked():
            glow = QColor(FG_ACCENT)
            for width, alpha in ((5, 40), (3, 80)):
                glow.setAlpha(alpha)
                painter.setPen(QPen(glow, width))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(outer, 12, 12)
        border = QColor(FG_ACCENT if self.isChecked() else (TEXT if self.underMouse() else LINE))
        if not self.isChecked() and self.underMouse():
            border.setAlpha(110)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(QColor(PANEL))
        painter.drawRoundedRect(outer, 12, 12)

        preview = outer.adjusted(8, 8, -8, -30)
        path = QPainterPath()
        path.addRoundedRect(preview, 8, 8)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(preview, QColor(c["BG"]))
        side = QRectF(preview.left(), preview.top(), preview.width() * 0.28, preview.height())
        painter.fillRect(side, QColor(c["PANEL"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["ACCENT_SOFT"]))
        painter.drawRoundedRect(QRectF(side.left() + 5, side.top() + 8, side.width() - 10, 9), 3, 3)
        painter.setBrush(QColor(c["INPUT"]))
        for row in range(2):
            painter.drawRoundedRect(QRectF(side.left() + 5, side.top() + 23 + row * 13, side.width() - 10, 7), 3, 3)
        body_left = side.right() + 8
        body_width = preview.right() - body_left - 8
        painter.setBrush(QColor(c["INPUT"]))
        painter.setPen(QPen(QColor(c["LINE"]), 1))
        painter.drawRoundedRect(QRectF(body_left, preview.top() + 8, body_width, preview.height() - 16), 5, 5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["TEXT"]))
        painter.drawRoundedRect(QRectF(body_left + 6, preview.top() + 15, body_width * 0.55, 5), 2, 2)
        painter.setBrush(QColor(c["MUTED"]))
        painter.drawRoundedRect(QRectF(body_left + 6, preview.top() + 26, body_width * 0.38, 4), 2, 2)
        painter.setBrush(QColor(c["ACCENT"]))
        painter.drawRoundedRect(QRectF(body_left + body_width - 40, preview.bottom() - 22, 34, 12), 5, 5)
        painter.restore()

        painter.setPen(QColor(TEXT))
        font = painter.font()
        font.setPixelSize(12)
        font.setBold(self.isChecked())
        painter.setFont(font)
        text_rect = QRectF(outer.left() + 10, outer.bottom() - 26, outer.width() - 20, 22)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._label)
        if self.isChecked():
            painter.setPen(QColor(ACCENT_TEXT))
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), "Selected")
        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class _ResizeHandle(QSizeGrip):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("resizeHandle")
        self.setFixedSize(20, 20)
        self.setStyleSheet("background: transparent; border: 0;")
        self.setMask(QRegion(QPolygon([QPoint(20, 0), QPoint(20, 20), QPoint(0, 20)])))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Resize window")
        self.setToolTip("Drag to resize. Arrow keys resize when focused.")

    def paintEvent(self, event):
        painter = QPainter(self)
        color = TEXT if self.underMouse() or self.hasFocus() else MUTED
        painter.drawPixmap(2, 2, icons.pixmap("resize", color))
        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        steps = {
            Qt.Key.Key_Left: (-10, 0), Qt.Key.Key_Right: (10, 0),
            Qt.Key.Key_Up: (0, -10), Qt.Key.Key_Down: (0, 10),
        }
        if event.key() in steps:
            dx, dy = steps[event.key()]
            window = self.window()
            window.resize(window.width() + dx, window.height() + dy)
            event.accept()
            return
        super().keyPressEvent(event)


class _SettingsSheet(QDialog):
    """Rounded, frameless settings window used by the Hotkey and Performance buttons."""

    def __init__(self, owner, title: str, icon_name: str, subtitle: str, build):
        super().__init__(owner)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self._drag_offset: QPoint | None = None
        self._intro: QPropertyAnimation | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("sheetFrame")
        frame.setStyleSheet(
            f"QFrame#sheetFrame {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 18px; }}"
        )
        outer.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(20, 18, 16, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        badge = QLabel()
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icons.pixmap(icon_name, ACCENT_BRIGHT, 24))
        badge.setStyleSheet(
            f"background: {ACCENT_TINT}; border: 1px solid {ACCENT_TINT_LINE}; border-radius: 12px;"
        )
        head.addWidget(badge)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("cardTitle")
        titles.addWidget(title_lbl)
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
        titles.addWidget(sub_lbl)
        head.addLayout(titles, 1)
        close_btn = QPushButton()
        close_btn.setObjectName("closeButton")
        close_btn.setToolTip("Close")
        close_btn.setAccessibleName("Close")
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setIcon(icons.make_icon("close", TEXT, active="#FFFFFF", size=16))
        close_btn.setIconSize(QSize(16, 16))
        close_btn.clicked.connect(self.close)
        head.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self._header_height = 64

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 4, 6, 0)
        body.setSpacing(12)
        build(body)
        body.addStretch(1)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)

        self.setMinimumWidth(500)
        hint = content.sizeHint().height() + 120
        screen = owner.screen() if owner is not None else None
        limit = screen.availableGeometry().height() - 80 if screen else 700
        self.resize(540, max(320, min(hint, limit, 720)))

    def present(self, animate: bool = True) -> None:
        owner = self.parentWidget()
        if not self.isVisible() and owner is not None:
            center = owner.frameGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        end = self.pos()
        self.setWindowOpacity(0.0 if animate else 1.0)
        self.show()
        self.raise_()
        self.activateWindow()
        if not animate:
            return
        self.move(end.x(), end.y() + 16)
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(200)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        glide = QPropertyAnimation(self, b"pos", self)
        glide.setDuration(240)
        glide.setStartValue(self.pos())
        glide.setEndValue(end)
        glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro = (fade, glide)
        fade.start()
        glide.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= self._header_height:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def reject(self):
        self.close()

    def closeEvent(self, event):
        # Hidden, not destroyed: the controls stay wired to the main window.
        event.ignore()
        self.hide()


class _RecentGameDelegate(QStyledItemDelegate):
    """Recent games row with the game icon on the left and a chevron on the right."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        chevron = icons.pixmap("chevron_right", MUTED, 16)
        rect = option.rect
        painter.drawPixmap(rect.right() - 26, rect.center().y() - 8, chevron)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), 46))


class _InsetButtonFilter(QObject):
    """Keeps a small button inside the right end of a combo box's text field."""

    def __init__(self, combo: QComboBox, button: QWidget, right_gap: int = 30):
        super().__init__(combo)
        self._combo = combo
        self._button = button
        self._gap = right_gap
        combo.installEventFilter(self)

    def place(self) -> None:
        combo, button = self._combo, self._button
        button.move(combo.width() - self._gap - button.width(), (combo.height() - button.height()) // 2)
        button.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False


class _GameNameLabel(QLabel):
    """Holds the "Current: <game>" text other code sets, and mirrors it into the Current card."""

    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self.on_change = on_change

    def setText(self, text):
        super().setText(text)
        if callable(self.on_change):
            self.on_change(text)


def _app_icon(icon_path: str | None = None) -> QIcon:
    """The ANAne logo as a window/taskbar icon.

    Built from logo.png at every common size so Windows always has a size it
    can show, with the .ico file as a fallback.
    """
    icon = QIcon()
    logo = QPixmap(get_resource_path("assets", "logo.png"))
    if not logo.isNull():
        side = max(logo.width(), logo.height())
        square = QPixmap(side, side)
        square.fill(Qt.GlobalColor.transparent)
        painter = QPainter(square)
        painter.drawPixmap((side - logo.width()) // 2, (side - logo.height()) // 2, logo)
        painter.end()
        for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            icon.addPixmap(square.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
    if icon.isNull() and icon_path and os.path.exists(icon_path):
        icon = QIcon(icon_path)
    return icon


class _NavPanel(QFrame):
    """Sidebar frame that keeps its sliding highlight aligned when it resizes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.relayout = None

    def _sync(self) -> None:
        if callable(self.relayout):
            self.relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync)


def _window_size_label(width: int, height: int) -> str:
    ratio = "4:3" if width * 3 == height * 4 else "wide"
    note = "  (5 across on 1080p)" if (width, height) == (384, 270) else ""
    return f"{width} × {height}  {ratio}{note}"


class _WindowSizePreview(QWidget):
    """A small drawing of the screen filled with windows of the chosen size."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(150)
        self.setMinimumWidth(240)
        self._work = (0, 0, 1920, 1040)
        self._size = (640, 480)
        self._grid = (3, 2)
        self._active = False

    def set_layout(self, work, size, cols: int, rows: int, active: bool) -> None:
        self._work, self._size, self._grid, self._active = work, size, (cols, rows), active
        self.update()

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QPainter, QPen, QColor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, top, right, bottom = self._work
        sw, sh = max(1, right - left), max(1, bottom - top)
        scale = min((self.width() - 8) / sw, (self.height() - 8) / sh)
        ox = (self.width() - sw * scale) / 2
        oy = (self.height() - sh * scale) / 2
        painter.setPen(QPen(QColor(LINE), 1))
        painter.setBrush(QColor(INPUT))
        painter.drawRoundedRect(int(ox), int(oy), int(sw * scale), int(sh * scale), 4, 4)
        if not self._active:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Roblox decides")
            return
        cols, rows = self._grid
        w, h = self._size
        painter.setPen(QPen(QColor(ACCENT_TEXT), 1))
        painter.setBrush(QColor(ACCENT_TINT))
        for row in range(min(rows, 40)):
            for col in range(min(cols, 40)):
                painter.drawRect(int(ox + col * w * scale) + 1, int(oy + row * h * scale) + 1,
                                 max(2, int(w * scale) - 2), max(2, int(h * scale) - 2))


class _SizeOutline(QWidget):
    """A see-through box at the exact pixel size, for when no Roblox window is open."""

    @classmethod
    def show_for(cls, owner, width: int, height: int) -> None:
        old = getattr(owner, "_size_outline", None)
        if old is not None and isValid(old):
            old.close()
        outline = cls(width, height, owner.screen() if hasattr(owner, "screen") else None)
        owner._size_outline = outline
        outline.show()
        outline.raise_()
        outline.activateWindow()

    def __init__(self, width: int, height: int, screen=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._label = f"{width} × {height}"
        screen = screen or QApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1.0
        self.resize(max(1, round(width / ratio)), max(1, round(height / ratio)))
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)
        QTimer.singleShot(15000, self, self.close)

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QPainter, QPen, QColor
        painter = QPainter(self)
        fill = QColor(ACCENT_TEXT)
        fill.setAlpha(40)
        painter.fillRect(self.rect(), fill)
        pen = QPen(QColor(ACCENT_TEXT), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         f"{self._label}\nRoblox window size\n(click or Esc to close)")

    def mousePressEvent(self, _event) -> None:
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()


class _PreviewKeepBar(QDialog):
    """Keep / Undo for a window size preview. Undo happens by itself after 15 s."""

    @classmethod
    def ask(cls, owner, text: str, keep, undo) -> "_PreviewKeepBar":
        bar = cls(owner, text, keep, undo)
        bar.show()
        return bar

    def __init__(self, owner, text: str, keep, undo):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Window size preview")
        self._keep, self._undo, self._done = keep, undo, False
        self._left = 15
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self._lbl = QLabel(text)
        lay.addWidget(self._lbl)
        row = QHBoxLayout()
        keep_btn = QPushButton("Keep")
        keep_btn.setObjectName("primary")
        self._undo_btn = QPushButton(f"Undo ({self._left})")
        keep_btn.clicked.connect(lambda: self._finish(True))
        self._undo_btn.clicked.connect(lambda: self._finish(False))
        row.addWidget(keep_btn)
        row.addWidget(self._undo_btn)
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._left -= 1
        if self._left <= 0:
            self._finish(False)
        else:
            self._undo_btn.setText(f"Undo ({self._left})")

    def _finish(self, keep: bool) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        try:
            (self._keep if keep else self._undo)()
        finally:
            self.close()

    def reject(self) -> None:
        self._finish(False)

    def closeEvent(self, event) -> None:
        if not self._done:
            self._done = True
            self._timer.stop()
            self._undo()
        event.accept()


class _RequiredUpdateDialog(QDialog):
    """Update window that can't be dismissed: Esc is ignored and closing it
    (Alt+F4, taskbar) calls on_close_attempt, which quits the app."""

    on_close_attempt = None

    def reject(self) -> None:
        pass

    def closeEvent(self, event) -> None:
        if getattr(self, "_allow_close", False):
            event.accept()
            return
        event.ignore()
        if callable(self.on_close_attempt):
            self.on_close_attempt()


class AccountManagerUIQt(QMainWindow): # Main Window
    def __init__(self, manager, icon_path: str | None = None):
        super().__init__()
        self.manager = manager
        self.manager.set_pre_launch_hook(self._pre_launch_settings)
        self._last_operation_error = ("", 0.0)
        self._chromium_download_active = False
        self._chromium_status_result = None
        self._roblox_settings_records: dict[str, dict] = {}
        self._roblox_settings_pending: dict[str, str] = {}
        self._roblox_settings_file_hash = ""
        self._roblox_settings_editor_active = False
        self._roblox_settings_loading = False
        self._roblox_settings_applying = False
        self._roblox_settings_show_load_error = False
        self._roblox_settings_config: dict[str, object] = {}
        self._roblox_settings_pending_config: dict[str, object] = {}
        self._roblox_settings_auto_applying = False
        self._roblox_settings_path_changing = False
        self._roblox_settings_startup_reload = True
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_exit_requested = False
        self._tray_restore_maximized = False
        self._tray_detached_states: dict[int, str] = {}
        self._shutdown_cleanup_done = False
        self._admin_restart_pending = False
        self._page_hosts: dict[int, _DetachablePageHost] = {}
        self._detached_windows: dict[int, _DetachedPageWindow] = {}
        self._page_names = {
            0: "Accounts",
            1: "Auto-Rejoin",
            2: "Anti AFK",
            3: "Multi Roblox",
            4: "Settings",
            5: "Console",
            6: "Contact Us",
            8: "Account Health",
        }
        self._window_grid_hotkey_registered = False
        self._window_grid_hotkey_hwnd = 0
        self._diagnostics_heartbeat = QTimer(self)
        self._diagnostics_heartbeat.setInterval(1000)
        self._diagnostics_heartbeat.timeout.connect(diagnostics.pulse_ui)
        self._diagnostics_heartbeat.start()

        for candidate in [
            os.path.join(get_data_dir(), "icon.ico"),
            icon_path,
            get_resource_path("assets", "icon.ico"),
        ]:
            if candidate and os.path.exists(candidate):
                self._icon_path = candidate
                break
        else:
            self._icon_path = None

        self._drag_pos = QPoint()
        self._game_name_timer = QTimer(self)
        self._game_name_timer.setSingleShot(True)
        self._game_name_timer.timeout.connect(self._do_fetch_game_name)

        self._console_queue = diagnostics.console_queue()

        # Thread to Qt signal bridge
        self._bridge = _Bridge()
        self._bridge.account_added.connect(self._on_add_done_main)
        self._bridge.proxy_test_done.connect(self._on_proxy_test_done)
        self._bridge.proxy_ping_done.connect(self._on_proxy_ping_done)
        self._bridge.ban_check_result.connect(self._on_ban_check_result)
        self._bridge.ban_check_done.connect(self._on_ban_check_done)
        self._bridge.kill_roblox_done.connect(self._on_kill_roblox_done)
        self._bridge.perf_applied.connect(self._on_perf_applied)
        self._bridge.account_creator_done.connect(self._on_account_creator_done)
        self._bridge.account_creator_progress.connect(self._on_account_creator_progress)
        self._bridge.backup_done.connect(self._on_backup_done)
        self._bridge.launch_done.connect(self._on_launch_and_refresh)
        self._bridge.avatar_ready.connect(self._on_avatar_ready)
        self._bridge.rejoin_status.connect(self._on_rejoin_status)
        self._bridge.afk_tooltip.connect(self._on_afk_tooltip_signal)
        self._bridge.mr_download_done.connect(self._update_mr_h64_status)
        self._bridge.shortcut_toggle_done.connect(self._on_shortcut_toggle_done)
        self._bridge.chromium_progress.connect(self._on_chromium_progress)
        self._bridge.chromium_done.connect(self._on_chromium_done)
        self._bridge.chromium_status.connect(self._on_chromium_status)
        self._bridge.roblox_download_progress.connect(self._on_roblox_download_progress)
        self._bridge.roblox_download_done.connect(self._on_roblox_download_done)
        self._bridge.presence_update.connect(self._on_presence_update)
        self._bridge.cookie_validated.connect(self._on_cookie_validated)
        self._bridge.console_wakeup.connect(self._drain_console_queue)
        diagnostics.set_console_wakeup(self._bridge.console_wakeup.emit)
        self._bridge.update_available.connect(self._on_update_available)
        self._bridge.update_check_finished.connect(self._on_update_check_finished)
        self._bridge.join_place_resolved.connect(self._on_join_place_resolved)
        self._bridge.recent_game_saved.connect(self._refresh_recent_games)
        self._bridge.favorite_place_resolved.connect(self._on_favorite_place_resolved)
        self._bridge.headless_update.connect(self._on_headless_update)
        self._bridge.headless_avatar_ready.connect(self._on_headless_avatar_ready)
        self._bridge.roblox_settings_loaded.connect(self._on_roblox_settings_loaded)
        self._bridge.roblox_settings_applied.connect(self._on_roblox_settings_applied)
        self._bridge.roblox_settings_auto_applied.connect(
            self._on_roblox_settings_auto_applied
        )
        self._bridge.roblox_settings_path_changed.connect(
            self._on_roblox_settings_path_changed
        )

        # Account Activity Monitor
        self._presence_mod = presence_mod
        self._presence_scanner = None
        self._presence_dots: dict[str, QLabel] = {}
        self._status_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._lock_labels: dict[str, QLabel] = {}
        self._note_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._online_usernames: set[str] = set()
        self._activity_snapshot: dict[str, dict] = {}
        self._activity_widgets: dict[str, QWidget] = {}
        self._activity_labels: dict[str, tuple[QLabel, QLabel]] = {}

        # Roblox Window Renamer
        self._window_renamer: window_renamer_mod.RobloxWindowRenamer | None = None

        # Cookie Validator
        self._cv_mod = cookie_validator_mod
        self._animations_on = motion.animations_enabled(actions.load_ui_settings())
        self._cv_validator = None
        self._invalid_badges: dict[str, QLabel] = {}
        self._account_avatar_containers: dict[str, QWidget] = {}
        self._account_name_labels: dict[str, QLabel] = {}
        self._account_rows: dict[str, QWidget] = {}
        self._placeholder_pixmaps: dict[int, QPixmap] = {}

        self._avatar_labels: dict[str, QLabel] = {}
        self._current_group: str | None = None
        self._group_bar_lay: QHBoxLayout | None = None

        self._ar_configs: dict = ar.load_configs() # {username: config_dict}
        self._ar_workers: dict[str, ar.AutoRejoinWorker] = {} # {username: worker}
        self._ar_statuses: dict[str, str] = {}
        self._ar_list: QListWidget | None = None
        self._headless_manager: headless_manager_mod.HeadlessManager | None = None
        self._headless_latest_rows: list[dict] = []
        self._headless_avatar_labels: dict[int, QLabel] = {}
        self._headless_status_labels: dict[int, QLabel] = {}

        # WebSocket server
        self._ws_server: ws_mod.WebSocketServer | None = ws_mod.WebSocketServer(
            manager=self.manager,
            ar_workers=self._ar_workers,
            ar_configs=self._ar_configs,
            get_settings=actions.load_ui_settings,
            rejoin_status_callback=self._bridge.rejoin_status.emit,
            refresh_ui_callback=lambda: self._bridge.account_added.emit(
                OperationResult.success()
            ),
        )

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setWindowTitle("ANAne's Roblox Account Manager")
        self._init_window_size()
        try:
            app_icon = _app_icon(self._icon_path)
            if not app_icon.isNull():
                self.setWindowIcon(app_icon)
        except Exception:
            pass

        self._apply_stylesheet()
        self._build_ui()
        self._setup_system_tray()

        _data_folder = get_data_dir()
        _enc_cfg = EncryptionConfig(os.path.join(_data_folder, "encryption_config.json"))
        self._setup_needed = not _enc_cfg.is_setup_complete()
        if self._setup_needed:
            for b in self._normal_nav_btns:
                b.hide()
            self._setup_nav_btn.show()
            self._setup_nav_btn.setChecked(True)
            self._page_stack.setCurrentIndex(7)

        self._bridge.game_name_ready.connect(self._game_name_label.setText)
        self._bridge.place_icon_ready.connect(self._on_place_icon_ready)
        self._start_running_counter()
        self._start_pc_usage_timer()
        self._start_window_size_watcher()
        QTimer.singleShot(8000, self._start_auto_backup)
        QTimer.singleShot(1500, self._offer_secure_settings_reset)

        self._refresh_account_list()
        self._refresh_recent_games()
        self._update_encryption_badge()
        # Apply persisted settings that affect widgets built in _build_ui
        S = actions.load_ui_settings()
        self._place_id_edit.setCurrentText(S.get("last_place_id", ""))
        self._private_server_edit.setCurrentText(S.get("last_private_server", ""))

        if S.get("last_place_id"):
            self._schedule_game_name_fetch()
            
        if S.get("enable_multi_select", False):
            self._account_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        if S.get("enable_topmost", False):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()

        if S.get("optimize_roblox_ram", False):
            self._start_ram_boost()

        if S.get("rename_roblox_windows", False):
            self._start_rename_windows()

        if S.get("presence_indicator", False):
            self._start_presence_scanner()

        if S.get("roblox_installer_fix", False):
            try:
                RobloxAPI.quarantine_installers()
            except Exception as e:
                print(f"[ERROR] Failed to quarantine installers: {e}")

        if S.get("websocket_enabled") and S.get("developer_mode"):
            self._ws_server.start()

        if S.get("headless_manager_enabled", False):
            self._start_headless_manager()

        if S.get("window_grid_enabled", False):
            QTimer.singleShot(
                0,
                lambda: self._apply_window_grid_hotkey(show_error=False),
            )

        if S.get("kill_all_hotkey_enabled", False):
            QTimer.singleShot(0, lambda: self._apply_kill_hotkey(show_error=False))

        if S.get("window_keys_enabled", False):
            QTimer.singleShot(0, lambda: self._apply_window_keys(show_error=False))

        if roblox_settings_mod.has_startup_customizations():
            QTimer.singleShot(0, self._start_roblox_auto_apply)

        QTimer.singleShot(2500, self._start_cookie_validator)
        QTimer.singleShot(500, self._start_update_check)
        if S.get("browser_type", "chrome") == "chromium":
            QTimer.singleShot(1500, self._start_chromium_status_check)
        print("[INFO] UI ready")

    def _theme_changed_since_start(self) -> bool:
        return color_themes.theme_name(actions.load_ui_settings()) != _THEME_NAME

    def _on_theme_card_clicked(self, key: str) -> None:
        actions.save_ui_setting(color_themes.THEME_SETTING, key)
        for card_key, card in self._theme_cards.items():
            card.setChecked(card_key == key)
        self._refresh_theme_restart_banner()

    def _refresh_theme_restart_banner(self) -> None:
        banner = getattr(self, "_theme_restart_banner", None)
        if banner is not None and isValid(banner):
            banner.setVisible(self._theme_changed_since_start())

    def _restart_for_theme(self) -> None:
        self._theme_restart_btn.setEnabled(False)
        self._theme_restart_btn.setText("Restarting...")
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(app_restart.start_new_copy)
        QTimer.singleShot(150, self._quit_for_update)

    def _apply_stylesheet(self):
        check_path = _checkmark_icon_path(PRIMARY_TEXT).replace("\\", "/")
        arrow_path = _dropdown_arrow_icon_path(MUTED).replace("\\", "/")
        self.setStyleSheet(f"""
            QMainWindow {{ background: {BG}; }}
            QWidget {{ color: {TEXT}; font-family: 'Segoe UI'; }}

            QFrame#titleBar {{ background: {BG}; border: 0; }}
            QLabel#titleText {{ font-size: 18px; font-weight: 700; color: {TEXT}; }}
            QLabel#titleSub {{ font-size: 12px; color: {MUTED}; }}
            QFrame#windowButtons {{
                background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px;
            }}
            QPushButton#titleButton, QPushButton#closeButton {{
                background: transparent; border: 0; border-radius: 8px;
                min-height: 30px; max-height: 30px; min-width: 36px; max-width: 36px;
                padding: 0; text-align: center;
            }}
            QPushButton#titleButton:hover {{ background: {HOVER}; }}
            QPushButton#closeButton:hover {{ background: #C42B1C; }}

            QFrame#navPanel {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CARD_TOP}, stop:1 {PANEL});
                border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CARD_GLOW}, stop:0.35 {LINE});
                border-radius: 18px;
            }}
            QFrame#navIndicator {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT_TINT_HOVER}, stop:1 {ACCENT_TINT});
                border: 1px solid {ACCENT_TINT_LINE}; border-left: 3px solid {FG_ACCENT};
                border-radius: 12px;
            }}
            QPushButton#sideNav {{
                background: transparent; border: 0; border-radius: 12px;
                text-align: left; min-height: 48px; max-height: 48px;
                padding: 0 14px; color: {MUTED}; font-size: 15px;
            }}
            QPushButton#sideNav:hover {{ color: {TEXT}; }}
            QPushButton#sideNav:checked {{ color: {ACCENT_TEXT}; font-weight: 700; }}
            QPushButton#killAll {{
                background: {ACCENT_TINT}; border: 1px solid {ACCENT_TINT_LINE};
                border-radius: 12px; min-height: 48px; padding: 0 14px;
                text-align: left; color: {ACCENT_TEXT}; font-size: 14px; font-weight: 700;
            }}
            QPushButton#killAll:hover {{ background: {ACCENT_TINT_HOVER}; }}
            QPushButton#killAll:pressed {{ background: {ACCENT_TINT}; }}
            QLabel#versionText {{ color: {MUTED}; font-size: 12px; background: transparent; }}

            QPushButton#navTab {{
                background: transparent; border: 1px solid transparent;
                border-radius: 8px; text-align: left; min-height: 30px;
                padding: 2px 10px; color: {MUTED}; font-size: 12px;
            }}
            QPushButton#navTab:hover {{ color: {TEXT}; background: {RAISED}; }}
            QPushButton#navTab:checked {{
                background: {ACCENT_TINT}; border: 1px solid {ACCENT_TINT_LINE};
                color: {ACCENT_TEXT}; font-weight: 700;
            }}

            QFrame#centerPanel, QFrame#card {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CARD_TOP}, stop:1 {PANEL});
                border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CARD_GLOW}, stop:0.25 {LINE});
                border-radius: 18px;
            }}
            QFrame#rightPanel {{ background: transparent; border: 0; }}
            QFrame#currentGame {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 14px;
            }}

            QLabel#sectionTitle {{ font-size: 15px; font-weight: 600; background: transparent; }}
            QLabel#cardTitle {{ font-size: 19px; font-weight: 600; background: transparent; }}
            QLabel#fieldLabel {{ color: {MUTED}; font-size: 12px; background: transparent; }}
            QLabel#runningCount {{ color: {MUTED}; font-size: 12px; background: transparent; }}
            QLabel#titleText, QLabel#titleSub {{ background: transparent; }}

            QListWidget {{
                background: {LIST_BG}; border: 1px solid {LINE}; border-radius: 12px;
                outline: none; padding: 4px; font-size: 12px;
            }}
            QListWidget::item {{ border-radius: 10px; padding-left: 8px; min-height: 26px; }}
            QListWidget::item:hover {{ background: {RAISED}; }}
            QListWidget::item:selected {{
                background: {ACCENT_TINT}; border: 1px solid {FG_ACCENT}; color: {TEXT};
            }}
            QListWidget#recentList::item {{ min-height: 40px; font-size: 13px; }}

            QTreeWidget, QTreeView, QTableWidget, QTableView {{
                background: {LIST_BG}; alternate-background-color: {PANEL};
                border: 1px solid {LINE}; border-radius: 12px; outline: none;
                color: {TEXT}; font-size: 12px;
                selection-background-color: {ACCENT_TINT}; selection-color: {TEXT};
            }}
            QTreeView::item, QTableView::item {{ padding: 4px 2px; }}
            QTreeView::item:hover, QTableView::item:hover {{ background: {RAISED}; }}
            QTreeView::item:selected, QTableView::item:selected {{ background: {ACCENT_TINT}; color: {TEXT}; }}
            QHeaderView {{ background: transparent; border: 0; }}
            QHeaderView::section {{
                background: {PANEL}; color: {MUTED}; border: 0; border-bottom: 1px solid {LINE};
                padding: 6px 8px; font-size: 11px; font-weight: 700;
            }}
            QTableCornerButton::section {{ background: {PANEL}; border: 0; }}

            QLabel#accountName {{ color: {TEXT}; font-size: 13px; background: transparent; }}
            QLabel#accountStatus {{ color: {MUTED}; font-size: 11px; background: transparent; }}
            QLabel#noteSep {{ color: #6B7280; font-size: 12px; background: transparent; }}
            QLabel#noteText {{ color: {NOTE}; font-size: 12px; font-weight: 600; background: transparent; }}
            QLabel#performanceSep {{ color: #6B7280; font-size: 11px; background: transparent; }}
            QLabel#ramUsage {{ color: {ACCENT_TEXT}; font-size: 11px; background: transparent; }}
            QLabel#cpuUsage {{ color: {ONLINE_GREEN}; font-size: 11px; background: transparent; }}
            QToolButton#rowMenu {{
                background: transparent; border: 0; border-radius: 8px;
                min-width: 30px; max-width: 30px; min-height: 26px; max-height: 26px;
            }}
            QToolButton#rowMenu:hover {{ background: {HOVER}; }}

            QLineEdit {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px;
                padding: 5px 10px; min-height: 24px; color: {TEXT};
                selection-background-color: {FG_ACCENT};
            }}
            QLineEdit:hover {{ border: 1px solid {HOVER}; }}
            QLineEdit:focus {{ border: 1px solid {FIELD_GLOW}; background: {RAISED}; }}
            QLineEdit:disabled {{ color: {MUTED}; }}
            QLineEdit#searchBox {{ min-height: 30px; font-size: 13px; padding-left: 6px; }}
            QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{
                padding: 0; min-height: 0; border: 0; border-radius: 0; background: transparent;
            }}

            QComboBox {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px;
                padding: 5px 10px; min-height: 24px; color: {TEXT};
            }}
            QComboBox:hover {{ border: 1px solid {HOVER}; }}
            QComboBox:focus, QComboBox:on {{ border: 1px solid {FIELD_GLOW}; }}
            QComboBox::drop-down {{ border: 0; width: 26px; }}
            QComboBox::down-arrow {{ image: url({arrow_path}); width: 10px; height: 10px; }}
            QComboBox QAbstractItemView {{
                background: {PANEL}; border: 1px solid {LINE}; outline: 0;
                selection-background-color: {ACCENT_TINT}; selection-color: {TEXT};
                padding: 4px;
            }}
            QComboBox#bigField {{ min-height: 30px; font-size: 13px; }}

            QPushButton {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px;
                min-height: 28px; padding: 3px 12px;
                text-align: left; font-size: 12px; color: {TEXT};
            }}
            QPushButton:hover {{ background: {RAISED}; border: 1px solid {ACCENT_TINT_LINE}; }}
            QPushButton:pressed {{ background: {PRESSED}; border: 1px solid {ACCENT_TINT_LINE}; }}
            QPushButton:disabled {{ color: {MUTED}; }}

            QPushButton#primary {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {PRIMARY_TOP}, stop:1 {PRIMARY_BOTTOM});
                border: 0; border-radius: 10px; min-height: 42px; padding: 0 14px;
                text-align: center; color: {PRIMARY_TEXT}; font-size: 14px; font-weight: 700;
            }}
            QPushButton#primary:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {PRIMARY_HOVER_TOP}, stop:1 {PRIMARY_HOVER_BOTTOM});
            }}
            QPushButton#primary:pressed {{ background: {PRIMARY_PRESSED}; }}
            QPushButton#primary:disabled {{ background: {RAISED}; color: {MUTED}; }}

            QPushButton#tile {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 12px;
                min-height: 48px; padding: 0 16px; text-align: left; font-size: 13px;
            }}
            QPushButton#tile:hover {{ background: {RAISED}; border: 1px solid {ACCENT_TINT_LINE}; }}
            QPushButton#tileSmall {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 12px;
                min-height: 44px; padding: 0 10px; text-align: center; font-size: 12px; font-weight: 600;
            }}
            QPushButton#tileSmall:hover {{ background: {RAISED}; border: 1px solid {ACCENT_TINT_LINE}; }}
            QPushButton#iconButton {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px;
                min-width: 38px; max-width: 38px; min-height: 36px; max-height: 36px;
                padding: 0; text-align: center; font-size: 18px;
            }}
            QPushButton#iconButton:hover {{ background: {RAISED}; border: 1px solid {ACCENT_TINT_LINE}; }}
            QPushButton#discordRound {{
                background: transparent; border: 2px solid transparent; border-radius: 20px;
                min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; padding: 0;
            }}
            QPushButton#discordRound:hover {{ background: transparent; border: 2px solid #7983F5; }}
            QPushButton#discordRound:pressed {{ background: transparent; border: 2px solid #5865F2; }}

            QToolButton#splitArrow {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px;
                min-width: 42px; max-width: 42px; min-height: 42px;
                padding: 0; color: {TEXT}; font-size: 14px; font-weight: 700;
            }}
            QToolButton#splitArrow:hover {{ background: {RAISED}; border: 1px solid {ACCENT_TINT_LINE}; }}
            QToolButton#splitArrow:pressed {{ background: {PRESSED}; }}
            QToolButton#splitArrow::menu-indicator {{ image: none; }}

            QMenu {{
                background: {PANEL}; border: 1px solid {CARD_GLOW}; border-radius: 10px;
                color: {TEXT}; font-size: 12px; padding: 6px;
            }}
            QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {ACCENT_TINT}; color: {TEXT}; }}
            QMenu::item:disabled {{ color: {MUTED}; }}
            QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}
            QMenu::section {{ color: {MUTED}; font-size: 10px; font-weight: 700; padding: 6px 12px 2px 12px; }}

            QToolTip {{
                background: {PANEL}; border: 1px solid {CARD_GLOW}; border-radius: 6px;
                color: {TEXT}; padding: 6px 9px;
            }}

            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {SCROLL_HANDLE}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::handle:vertical:hover {{ background: {SCROLL_HANDLE_HOVER}; }}
            QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
            QScrollBar::handle:horizontal {{ background: {SCROLL_HANDLE}; border-radius: 4px; min-width: 30px; }}
            QScrollBar::handle:horizontal:hover {{ background: {SCROLL_HANDLE_HOVER}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: 0; background: none; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

            QScrollArea#groupScroll {{
                background: transparent; border: 0;
                max-height: 30px;
            }}
            QScrollArea#groupScroll > QWidget > QWidget {{
                background: transparent;
            }}
            QPushButton#groupTab {{
                background: transparent; border: 1px solid transparent;
                border-radius: 8px; min-height: 22px; max-height: 22px;
                padding: 0px 10px; font-size: 11px; color: {MUTED};
            }}
            QPushButton#groupTab:checked {{
                background: {ACCENT_TINT}; border: 1px solid {ACCENT_TINT_LINE}; color: {ACCENT_TEXT};
            }}
            QPushButton#groupTab:hover {{ background: {RAISED}; border-color: {HOVER}; }}

            QDialog {{ background: {BG}; }}
            QTextEdit {{
                background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px;
                color: {TEXT}; font-size: 11px; selection-background-color: {FG_ACCENT};
            }}
            QTextEdit:focus {{ border: 1px solid {FIELD_GLOW}; }}

            QCheckBox {{ color: {TEXT}; font-size: 12px; spacing: 9px; background: transparent; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px; border-radius: 5px;
                border: 2px solid {HOVER}; background: {INPUT};
            }}
            QCheckBox::indicator:hover {{ border: 2px solid {FG_ACCENT}; }}
            QCheckBox::indicator:checked {{
                background: {FG_ACCENT}; border: 2px solid {FG_ACCENT};
                image: url({check_path});
            }}
            QCheckBox::indicator:checked:hover {{ background: {ACCENT_HOVER}; border: 2px solid {ACCENT_HOVER}; }}
            QCheckBox::indicator:disabled {{ background: {SELECT}; border: 2px solid {LINE}; }}
            QCheckBox::indicator:checked:disabled {{ background: {ACCENT_SOFT}; border: 2px solid {ACCENT_SOFT}; }}
            QCheckBox:disabled {{ color: {MUTED}; }}

            QRadioButton {{ color: {TEXT}; font-size: 12px; spacing: 9px; background: transparent; }}
            QRadioButton::indicator {{
                width: 14px; height: 14px; border-radius: 9px;
                border: 2px solid {HOVER}; background: {INPUT};
            }}
            QRadioButton::indicator:hover {{ border: 2px solid {FG_ACCENT}; }}
            QRadioButton::indicator:checked {{
                width: 8px; height: 8px; border-radius: 9px;
                background: {PRIMARY_TEXT}; border: 5px solid {FG_ACCENT};
            }}
            QRadioButton:disabled {{ color: {MUTED}; }}

            QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {SELECT}; }}
            QSlider::sub-page:horizontal {{
                border-radius: 3px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PRIMARY_BOTTOM}, stop:1 {PRIMARY_TOP});
            }}
            QSlider::handle:horizontal {{
                width: 12px; height: 12px; margin: -6px 0; border-radius: 9px;
                background: #FFFFFF; border: 3px solid {FG_ACCENT};
            }}
            QSlider::handle:horizontal:hover {{ border: 3px solid {ACCENT_HOVER}; }}
            QSlider::sub-page:horizontal:disabled {{ background: {ACCENT_SOFT}; }}

            QGroupBox {{
                border: 1px solid {LINE}; border-radius: 14px;
                margin-top: 12px; padding-top: 6px;
                font-size: 10px; font-weight: 700; color: {MUTED};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; subcontrol-position: top left;
                padding: 0 6px 0 6px; left: 10px;
            }}
        """)

    def _build_ui(self): # build the main window UI structure
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_title_bar())

        self._page_stack = QStackedWidget()
        self._built_pages = {0, 1, 2, 3, 7, 8}
        self._lazy_page_builders = {
            4: self._build_settings_panel,
            5: self._build_console_panel,
            6: self._build_donations_panel,
        }

        # Stack index must equal page index; the Setup panel sits at 7, so
        # pages after it are added once Setup is in place.
        def _add_page_host(index: int, page_name: str) -> None:
            host = _DetachablePageHost(index, page_name)
            host.show_button.clicked.connect(
                lambda _=False, idx=index: self._show_detached_page(idx)
            )
            host.reattach_button.clicked.connect(
                lambda _=False, idx=index: self._reattach_page(idx)
            )
            self._page_hosts[index] = host
            self._page_stack.addWidget(host)

        for index, page_name in self._page_names.items():
            if index < 7:
                _add_page_host(index, page_name)

        _accounts_page = QWidget()
        _acc_lay = QHBoxLayout(_accounts_page)
        _acc_lay.setContentsMargins(0, 0, 0, 0)
        _acc_lay.setSpacing(14)
        _acc_lay.addWidget(self._build_center_panel(), 1)
        _acc_lay.addWidget(self._build_right_panel())

        self._page_hosts[0].set_page(_accounts_page)
        self._page_hosts[1].set_page(self._build_auto_rejoin_panel())
        self._page_hosts[2].set_page(self._build_anti_afk_panel())
        self._page_hosts[3].set_page(self._build_multi_roblox_panel())
        self._page_stack.addWidget(self._build_setup_panel()) # idx 7
        for index, page_name in self._page_names.items():
            if index > 7:
                _add_page_host(index, page_name)
        self._page_hosts[8].set_page(self._build_ban_check_panel())

        body = QHBoxLayout()
        body.setContentsMargins(14, 2, 14, 14)
        body.setSpacing(14)
        body.addWidget(self._build_nav_panel())
        body.addWidget(self._page_stack, 1)
        outer.addLayout(body, 1)

        self._resize_handle = _ResizeHandle(central)
        self._position_resize_handle()

    def _show_page(self, index: int) -> None:
        self._ensure_page_built(index)
        changed = self._page_stack.currentIndex() != index
        self._page_stack.setCurrentIndex(index)
        if changed:
            motion.slide_fade_in(self._page_stack.currentWidget(), enabled=self._animations_on)
        button = getattr(self, "_nav_buttons_by_page", {}).get(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        if index in self._detached_windows:
            self._show_detached_page(index)

    def _ensure_page_built(self, index: int) -> None:
        if index not in self._built_pages:
            builder = self._lazy_page_builders.get(index)
            if builder is not None:
                page = builder()
                self._page_hosts[index].set_page(page)
                self._built_pages.add(index)
                if index == 4:
                    if hasattr(self, "_headless_list"):
                        self._refresh_headless_list(self._headless_latest_rows)
                    self._start_chromium_status_check()
                    self._load_roblox_settings(show_error=False)
                elif index == 5:
                    self._drain_console_queue()

    def _show_page_context_menu(self, index: int, global_pos: QPoint) -> None:
        menu = QMenu(self)
        if index in self._detached_windows:
            show_action = menu.addAction("Show Window")
            reattach_action = menu.addAction("Reattach to Main Window")
            selected = menu.exec(global_pos)
            if selected == show_action:
                self._show_detached_page(index)
            elif selected == reattach_action:
                self._reattach_page(index)
            return

        open_action = menu.addAction("Open in New Window")
        if menu.exec(global_pos) == open_action:
            self._detach_page(index)

    def _detach_page(self, index: int) -> None:
        if index not in self._page_hosts:
            return
        if index in self._detached_windows:
            self._show_detached_page(index)
            return

        self._ensure_page_built(index)
        host = self._page_hosts[index]
        page = host.take_page()
        if page is None:
            return

        detached = _DetachedPageWindow(
            index,
            self._page_names[index],
            page,
            self.windowIcon(),
            self.styleSheet(),
        )
        detached.reattach_requested.connect(self._reattach_page)
        if self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            detached.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        detached.resize(max(640, self.width()), max(520, self.height()))
        self._detached_windows[index] = detached
        detached.show()
        detached.raise_()
        detached.activateWindow()
        print(f"[INFO] Opened {self._page_names[index]} in a separate window")

    def _show_detached_page(self, index: int) -> None:
        detached = self._detached_windows.get(index)
        if detached is None:
            return
        if detached.isMinimized():
            detached.showNormal()
        else:
            detached.show()
        detached.raise_()
        detached.activateWindow()

    def _reattach_page(self, index: int) -> None:
        detached = self._detached_windows.pop(index, None)
        host = self._page_hosts.get(index)
        if detached is None or host is None:
            return

        page = detached.release_page()
        if page is not None:
            host.restore_page(page)
        detached.allow_close()
        detached.hide()
        QTimer.singleShot(0, detached.close)
        self._tray_detached_states.pop(index, None)
        print(f"[INFO] Reattached {self._page_names[index]} to the main window")

    def _hide_detached_pages_for_tray(self) -> None:
        self._tray_detached_states = {}
        for index, detached in self._detached_windows.items():
            if not detached.isVisible():
                continue
            if detached.isMaximized():
                state = "maximized"
            elif detached.isMinimized():
                state = "minimized"
            else:
                state = "normal"
            self._tray_detached_states[index] = state
            detached.hide()

    def _restore_detached_pages_from_tray(self) -> None:
        states = dict(self._tray_detached_states)
        self._tray_detached_states.clear()
        for index, state in states.items():
            detached = self._detached_windows.get(index)
            if detached is None:
                continue
            if state == "maximized":
                detached.showMaximized()
            elif state == "minimized":
                detached.showMinimized()
            else:
                detached.showNormal()

    def _close_detached_pages(self) -> None:
        detached_windows = list(self._detached_windows.values())
        self._detached_windows.clear()
        self._tray_detached_states.clear()
        for detached in detached_windows:
            detached.allow_close()
            detached.close()

    # Title bar
    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(TITLE_BAR_HEIGHT)

        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 6, 14, 2)
        lay.setSpacing(14)

        logo = QLabel()
        logo.setObjectName("appLogo")
        logo_pix = QPixmap(get_resource_path("assets", "logo.png"))
        if logo_pix.isNull() and self._icon_path:
            logo_pix = QPixmap(self._icon_path)
        if not logo_pix.isNull():
            ratio = max(1.0, self.devicePixelRatioF())
            scaled = logo_pix.scaledToHeight(round(44 * ratio), Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            logo.setPixmap(scaled)
        logo.setStyleSheet("background: transparent;")
        lay.addWidget(logo)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = QLabel("ANAne's Roblox Account Manager")
        title.setObjectName("titleText")
        text_col.addWidget(title)

        crumbs = [("Manage", 0), ("Auto-Rejoin", 1), ("Anti AFK", 2), ("Multi Roblox", 3)]
        dot = f'<span style="color:{color_themes.mix(MUTED, BG, 0.45)};">&nbsp;&nbsp;&bull;&nbsp;&nbsp;</span>'
        links = dot.join(
            f'<a href="{index}" style="color:{MUTED}; text-decoration:none;">{label}</a>'
            for label, index in crumbs
        )
        subtitle = QLabel(links)
        subtitle.setObjectName("titleSub")
        subtitle.setTextFormat(Qt.TextFormat.RichText)
        subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        subtitle.setOpenExternalLinks(False)
        subtitle.linkActivated.connect(lambda target: self._show_page(int(target)))
        text_col.addWidget(subtitle)
        lay.addLayout(text_col)
        lay.addStretch(1)

        # How the saved accounts are protected (Device Locked / Password Locked
        # / Unprotected). Click to open Settings > Misc > Encryption.
        self._enc_badge = QPushButton()
        self._enc_badge.setObjectName("encBadge")
        self._enc_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._enc_badge.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._enc_badge.setIconSize(QSize(16, 16))
        self._enc_badge.clicked.connect(self._open_encryption_settings)
        self._enc_badge.hide()
        lay.addWidget(self._enc_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)

        buttons = QFrame()
        buttons.setObjectName("windowButtons")
        btn_lay = QHBoxLayout(buttons)
        btn_lay.setContentsMargins(4, 4, 4, 4)
        btn_lay.setSpacing(2)

        def _title_button(name: str, icon_name: str, tip: str) -> QPushButton:
            btn = QPushButton()
            btn.setObjectName(name)
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            active = "#FFFFFF" if name == "closeButton" else TITLE_ICON_ACTIVE
            btn.setIcon(icons.make_icon(icon_name, TEXT, active=active, size=16))
            btn.setIconSize(QSize(16, 16))
            btn_lay.addWidget(btn)
            return btn

        min_btn = _title_button("titleButton", "minimize", "Minimize")
        min_btn.clicked.connect(self.showMinimized)
        self._max_btn = _title_button("titleButton", "maximize", "Maximize")
        self._max_btn.clicked.connect(self._toggle_maximized)
        close_btn = _title_button("closeButton", "close", "Close")
        close_btn.clicked.connect(self.close)
        lay.addWidget(buttons, 0, Qt.AlignmentFlag.AlignVCenter)

        return bar

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            button = getattr(self, "_max_btn", None)
            if button is not None and isValid(button):
                maximized = self.isMaximized()
                button.setIcon(icons.make_icon("restore" if maximized else "maximize",
                                               TEXT, active=TITLE_ICON_ACTIVE, size=16))
                button.setToolTip("Restore" if maximized else "Maximize")
            handle = getattr(self, "_resize_handle", None)
            if handle is not None and isValid(handle):
                handle.setVisible(not self.isMaximized())

    def _init_window_size(self) -> None:
        self._window_size_save_timer = QTimer(self)
        self._window_size_save_timer.setSingleShot(True)
        self._window_size_save_timer.setInterval(400)
        self._window_size_save_timer.timeout.connect(self._save_window_size)
        self.setMinimumSize(MIN_WINDOW_SIZE)

        size = self._default_window_size()
        saved = actions.load_ui_settings().get("main_window_size")
        if (isinstance(saved, list) and len(saved) == 2
                and all(type(value) is int and value > 0 for value in saved)):
            screen = self.screen()
            available = screen.availableGeometry().size() if screen else MIN_WINDOW_SIZE
            size = QSize(min(saved[0], available.width()), min(saved[1], available.height()))
            size = size.expandedTo(self.minimumSize())
        self.resize(size)
        self._last_saved_window_size = [self.width(), self.height()]

    def _default_window_size(self) -> QSize:
        screen = self.screen()
        if screen is None:
            return QSize(DEFAULT_WINDOW_SIZE)
        available = screen.availableGeometry().size()
        size = QSize(
            min(DEFAULT_WINDOW_SIZE.width(), round(available.width() * 0.92)),
            min(DEFAULT_WINDOW_SIZE.height(), round(available.height() * 0.92)),
        )
        return size.expandedTo(MIN_WINDOW_SIZE)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_resize_handle()
        if (hasattr(self, "_window_size_save_timer") and self.isVisible()
                and not self.isMaximized() and not self.isFullScreen()
                and not self.isMinimized()):
            self._window_size_save_timer.start()

    def _position_resize_handle(self) -> None:
        handle = getattr(self, "_resize_handle", None)
        if handle is not None:
            parent = handle.parentWidget()
            handle.move(parent.width() - handle.width(), parent.height() - handle.height())
            handle.raise_()

    def _save_window_size(self) -> None:
        self._window_size_save_timer.stop()
        geometry_size = self.size()
        if self.isMaximized() or self.isFullScreen() or self.isMinimized():
            geometry_size = self.normalGeometry().size()
        if not geometry_size.isValid():
            return
        size = [geometry_size.width(), geometry_size.height()]
        if size == self._last_saved_window_size:
            return
        try:
            actions.save_ui_setting("main_window_size", size)
        except Exception as exc:
            print(f"[WARNING] Could not save window size: {exc}")
        else:
            self._last_saved_window_size = size

    # Drag window
    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and event.position().y() <= TITLE_BAR_HEIGHT
                and not self.isMaximized()):
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = QPoint()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= TITLE_BAR_HEIGHT:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # Left nav panel
    def _build_nav_panel(self) -> QFrame:
        panel = _NavPanel()
        panel.setObjectName("navPanel")
        panel.setFixedWidth(204)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 14, 10, 14)
        lay.setSpacing(8)

        # Highlight that glides behind the selected item when you switch pages.
        self._nav_indicator = QFrame(panel)
        self._nav_indicator.setObjectName("navIndicator")
        self._nav_indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._nav_indicator.hide()
        self._nav_indicator_anim = QPropertyAnimation(self._nav_indicator, b"geometry", self)
        self._nav_indicator_anim.setDuration(240)
        self._nav_indicator_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._nav_buttons_by_page: dict[int, QPushButton] = {}
        panel.relayout = lambda: self._place_nav_indicator(animate=False)

        _NAV_PAGES = {
            "Accounts":    0,
            "Auto-Rejoin": 1,
            "Anti AFK":    2,
            "Multi Roblox":3,
            "Settings":    4,
            "Console":     5,
            "Account Health": 8,
            "Contact Us":  6,
        }

        self._normal_nav_btns: list[QPushButton] = []
        _nav_icons = {
            "Accounts": "accounts", "Auto-Rejoin": "refresh", "Anti AFK": "shield_check",
            "Multi Roblox": "windows", "Proxy": "globe", "Settings": "sliders", "Console": "terminal",
            "Contact Us": "mail", "Account Health": "pulse",
        }

        for label, checked in [
            ("Accounts", True),
            ("Auto-Rejoin", False),
            ("Anti AFK", False),
            ("Multi Roblox", False),
            ("Settings", False),
            ("Console", False),
            ("Account Health", False),
            ("Contact Us", False),
        ]:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("sideNav")
            btn.setAccessibleName(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setChecked(checked)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(icons.make_icon(
                _nav_icons[label], MUTED, active=TEXT, checked=ACCENT_TEXT, disabled=LINE, size=22,
            ))
            btn.setIconSize(QSize(22, 22))
            btn.toggled.connect(
                lambda on, button=btn: on and self._place_nav_indicator(button)
            )
            if label in _NAV_PAGES:
                page_idx = _NAV_PAGES[label]
                self._nav_buttons_by_page[page_idx] = btn
                btn.clicked.connect(
                    lambda _=False, idx=page_idx: self._show_page(idx)
                )
                btn.setToolTip("Right-click for window options")
                btn.setContextMenuPolicy(
                    Qt.ContextMenuPolicy.CustomContextMenu
                )
                btn.customContextMenuRequested.connect(
                    lambda pos, button=btn, idx=page_idx:
                    self._show_page_context_menu(
                        idx,
                        button.mapToGlobal(pos),
                    )
                )
            lay.addWidget(btn)
            self._normal_nav_btns.append(btn)

        # Setup nav button
        self._setup_nav_btn = QPushButton("  Setup")
        self._setup_nav_btn.setObjectName("sideNav")
        self._setup_nav_btn.setIcon(icons.make_icon(
            "shield", MUTED, active=TEXT, checked=ACCENT_TEXT, disabled=LINE, size=22,
        ))
        self._setup_nav_btn.setIconSize(QSize(22, 22))
        self._setup_nav_btn.toggled.connect(
            lambda on: on and self._place_nav_indicator(self._setup_nav_btn)
        )
        self._setup_nav_btn.setCheckable(True)
        self._setup_nav_btn.setAutoExclusive(True)
        self._setup_nav_btn.setChecked(False)
        self._setup_nav_btn.clicked.connect(
            lambda: self._page_stack.setCurrentIndex(7)
        )
        self._setup_nav_btn.hide() # shown when setup needed (hidden by default)
        lay.addWidget(self._setup_nav_btn)

        lay.addStretch(1)

        self._nav_queue_lbl = QLabel("")
        self._nav_queue_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_queue_lbl.setStyleSheet(f"color: {ACCENT_BRIGHT}; font-size: 10px; font-weight: 700;")
        self._nav_queue_lbl.setToolTip("Batch queue in progress. Kill All Roblox opens the next batch.")
        self._nav_queue_lbl.hide()
        lay.addWidget(self._nav_queue_lbl)

        kill_roblox_button = QPushButton("  Kill All Roblox")
        kill_roblox_button.setObjectName("killAll")
        kill_roblox_button.setCursor(Qt.CursorShape.PointingHandCursor)
        kill_roblox_button.setToolTip(KILL_ALL_TOOLTIP)
        self._kill_roblox_btn = kill_roblox_button
        kill_roblox_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        kill_roblox_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        kill_roblox_button.customContextMenuRequested.connect(self._show_kill_button_menu)
        icons.set_accent_icon(kill_roblox_button, "power", _THEME, size=22)
        kill_roblox_button.clicked.connect(self._on_kill_all_roblox)
        lay.addWidget(kill_roblox_button)
        lay.addSpacing(8)

        ver_lbl = QLabel(f"Version : {APP_VERSION}")
        ver_lbl.setObjectName("versionText")
        ver_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        ver_lbl.setContentsMargins(10, 0, 0, 0)
        lay.addWidget(ver_lbl)
        QTimer.singleShot(0, lambda: self._place_nav_indicator(animate=False))
        return panel

    def _checked_nav_button(self) -> QPushButton | None:
        for button in list(getattr(self, "_normal_nav_btns", [])) + [getattr(self, "_setup_nav_btn", None)]:
            if button is not None and isValid(button) and button.isChecked() and button.isVisible():
                return button
        return None

    def _place_nav_indicator(self, button: QPushButton | None = None, animate: bool = True) -> None:
        indicator = getattr(self, "_nav_indicator", None)
        if indicator is None or not isValid(indicator):
            return
        button = button or self._checked_nav_button()
        if button is None or not button.isVisible():
            indicator.hide()
            return
        target = button.geometry()
        animation = self._nav_indicator_anim
        animation.stop()
        if animate and self._animations_on and indicator.isVisible():
            animation.setStartValue(indicator.geometry())
            animation.setEndValue(target)
            animation.start()
        else:
            indicator.setGeometry(target)
        indicator.show()
        indicator.lower()

    def _build_setup_panel(self) -> QWidget: # Encryption setup panel
        panel = QWidget()
        panel.setObjectName("centerPanel")
        root = QVBoxLayout(panel)
        root.setContentsMargins(28, 22, 28, 20)
        root.setSpacing(14)

        hdr = QLabel("Choose your encryption method")
        hdr.setStyleSheet("font-size: 14px; font-weight: 700;")
        sub = QLabel(
            "Your account cookies will be stored locally. "
            "Choose how they are protected."
        )
        sub.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        sub.setWordWrap(True)
        root.addWidget(hdr)
        root.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {LINE};")
        root.addWidget(sep)

        self._setup_stack = QStackedWidget()
        root.addWidget(self._setup_stack, 1)

        choice_w = QWidget()
        choice_lay = QVBoxLayout(choice_w)
        choice_lay.setContentsMargins(0, 0, 0, 0)
        choice_lay.setSpacing(10)

        _btn_style = (
            f"QPushButton {{ background: {INPUT}; color: {TEXT};"
            f"  border: 1px solid {LINE}; border-radius: 0;"
            f"  padding: 10px 16px; font-size: 12px; text-align: left; }}"
            f"QPushButton:hover {{ background: {SELECT}; border-color: {HOVER}; }}"
            f"QPushButton:checked {{ background: {ACCENT_DEEP}; border-color: {FG_ACCENT}; color: {TEXT}; }}"
        )

        btn_group = QButtonGroup(self)
        btn_group.setExclusive(True)

        self._setup_hw_btn = QPushButton(
            "Hardware Encryption\n"
            "   Tied to this PC. No password needed. Not portable."
        )
        self._setup_hw_btn.setCheckable(True)
        self._setup_hw_btn.setStyleSheet(_btn_style)

        self._setup_pw_btn = QPushButton(
            "Password Encryption\n"
            "   Portable across PCs. No recovery if password is lost."
        )
        self._setup_pw_btn.setCheckable(True)
        self._setup_pw_btn.setChecked(True)
        self._setup_pw_btn.setStyleSheet(_btn_style)

        self._setup_none_btn = QPushButton(
            "No Encryption\n"
            "   Cookies stored in plain text. Not secure."
        )
        self._setup_none_btn.setCheckable(True)
        self._setup_none_btn.setStyleSheet(_btn_style)

        for b in (self._setup_hw_btn, self._setup_pw_btn, self._setup_none_btn):
            btn_group.addButton(b)
            choice_lay.addWidget(b)

        choice_lay.addStretch()

        cont_row = QHBoxLayout()
        cont_row.addStretch()
        self._setup_continue_btn = QPushButton("Continue")
        self._setup_continue_btn.setStyleSheet(
            f"QPushButton {{ background: {SELECT}; border: 1px solid {LINE};"
            f"  min-height: 30px; min-width: 120px; font-weight: 700;"
            f"  text-align: center; color: {TEXT}; border-radius: 0; }}"
            f"QPushButton:hover   {{ background: {HOVER}; }}"
            f"QPushButton:pressed {{ background: {PRESSED}; }}"
        )
        cont_row.addWidget(self._setup_continue_btn)
        choice_lay.addLayout(cont_row)

        self._setup_stack.addWidget(choice_w) # idx 0

        pw_w = QWidget()
        pw_lay = QVBoxLayout(pw_w)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        pw_lay.setSpacing(10)

        warn = QLabel(
            "IMPORTANT: There is NO password recovery.\n"
            "A lost password means permanent data loss."
        )
        warn.setStyleSheet(f"color: {NOTE}; font-size: 11px;")
        pw_lay.addWidget(warn)

        pw_lay.addWidget(QLabel("Enter your password (min. 8 characters):"))
        self._setup_pw_entry1 = QLineEdit()
        self._setup_pw_entry1.setEchoMode(QLineEdit.EchoMode.Password)
        self._setup_pw_entry1.setPlaceholderText("Password")
        pw_lay.addWidget(self._setup_pw_entry1)

        pw_lay.addWidget(QLabel("Confirm password:"))
        self._setup_pw_entry2 = QLineEdit()
        self._setup_pw_entry2.setEchoMode(QLineEdit.EchoMode.Password)
        self._setup_pw_entry2.setPlaceholderText("Confirm password")
        pw_lay.addWidget(self._setup_pw_entry2)

        self._setup_pw_err = QLabel("")
        self._setup_pw_err.setStyleSheet("color: #C0392B; font-size: 11px;")
        pw_lay.addWidget(self._setup_pw_err)

        pw_lay.addStretch()

        pw_btn_row = QHBoxLayout()
        pw_btn_row.setSpacing(8)
        pw_back = QPushButton("Back")
        self._setup_pw_confirm_btn = QPushButton("Confirm")
        self._setup_pw_confirm_btn.setStyleSheet(
            f"QPushButton {{ background: {SELECT}; border: 1px solid {LINE};"
            f"  min-height: 30px; min-width: 120px; font-weight: 700;"
            f"  text-align: center; color: {TEXT}; border-radius: 0; }}"
            f"QPushButton:hover   {{ background: {HOVER}; }}"
            f"QPushButton:pressed {{ background: {PRESSED}; }}"
        )
        pw_btn_row.addWidget(pw_back)
        pw_btn_row.addStretch()
        pw_btn_row.addWidget(self._setup_pw_confirm_btn)
        pw_lay.addLayout(pw_btn_row)

        self._setup_stack.addWidget(pw_w)       # idx 1

        def _on_continue():
            if self._setup_hw_btn.isChecked():
                _do_hardware()
            elif self._setup_none_btn.isChecked():
                _do_none()
            else:
                self._setup_pw_entry1.clear()
                self._setup_pw_entry2.clear()
                self._setup_pw_err.setText("")
                self._setup_stack.setCurrentIndex(1)

        def _do_hardware():
            data_folder = get_data_dir()
            enc = EncryptionConfig(os.path.join(data_folder, "encryption_config.json"))
            enc.enable_hardware_encryption()
            _show_info(self, "Hardware Encryption Enabled",
                       "Hardware-based encryption is now active.\n"
                       "Your accounts will be encrypted automatically.")
            self._on_setup_complete()

        def _do_none():
            res = QMessageBox.warning(
                self, "No Encryption",
                "Your account data will be stored in plain text.\n"
                "Anyone with access to your files can read your cookies.\n\n"
                "Are you sure you want to continue without encryption?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res == QMessageBox.StandardButton.Yes:
                data_folder = get_data_dir()
                enc = EncryptionConfig(os.path.join(data_folder, "encryption_config.json"))
                enc.disable_encryption()
                self._on_setup_complete()

        def _on_confirm_pw():
            pw1 = self._setup_pw_entry1.text()
            pw2 = self._setup_pw_entry2.text()
            if len(pw1) < 8:
                self._setup_pw_err.setText("Password must be at least 8 characters.")
                return
            if pw1 != pw2:
                self._setup_pw_err.setText("Passwords do not match.")
                return
            data_folder = get_data_dir()
            enc = EncryptionConfig(os.path.join(data_folder, "encryption_config.json"))
            temp = PasswordEncryption(pw1)
            enc.enable_password_encryption(temp.get_salt_b64())
            _show_info(self, "Password Encryption Enabled",
                       "Password encryption is now active.\n"
                       "Keep your password safe, there is no recovery method.")
            self._on_setup_complete()

        self._setup_continue_btn.clicked.connect(_on_continue)
        pw_back.clicked.connect(lambda: self._setup_stack.setCurrentIndex(0))
        self._setup_pw_confirm_btn.clicked.connect(_on_confirm_pw)

        return panel

    def _on_setup_complete(self):
        self._setup_nav_btn.hide()
        for b in self._normal_nav_btns:
            b.show()
        self._normal_nav_btns[0].setChecked(True) # Accounts
        self._page_stack.setCurrentIndex(0)
        self._setup_needed = False
        QTimer.singleShot(0, lambda: self._place_nav_indicator(animate=False))

    def _build_center_panel(self) -> QFrame: # Main account list
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        header_icon = QLabel()
        header_icon.setPixmap(icons.pixmap("accounts", ACCENT_BRIGHT, 26))
        header_icon.setStyleSheet("background: transparent;")
        header_row.addWidget(header_icon)

        section_title = QLabel("Account List")
        section_title.setObjectName("cardTitle")
        header_row.addWidget(section_title)
        header_row.addSpacing(10)

        # Number of Roblox windows open right now (every instance, not just
        # the ones started from this manager).
        self._running_lbl = QLabel("0 running")
        self._running_lbl.setObjectName("runningCount")
        self._running_lbl.setToolTip("Roblox windows running on this PC")
        self._ram_total_lbl = self._running_lbl
        header_row.addWidget(self._running_lbl)

        header_row.addStretch(1)

        # (The encryption badge lives in the title bar.)

        lay.addLayout(header_row)
        lay.addSpacing(2)

        # account search
        self._account_query = ""
        self._account_search = QLineEdit()
        self._account_search.setObjectName("searchBox")
        self._account_search.setPlaceholderText("Search accounts (Ctrl+F)")
        self._account_search.setClearButtonEnabled(True)
        self._account_search.setAccessibleName("Search accounts")
        self._account_search.addAction(
            icons.make_icon("search", MUTED, size=18), QLineEdit.ActionPosition.LeadingPosition,
        )
        self._account_search_timer = QTimer(self)
        self._account_search_timer.setSingleShot(True)
        self._account_search_timer.setInterval(150)
        self._account_search_timer.timeout.connect(self._apply_account_search)
        self._account_search.textChanged.connect(
            lambda _text: self._account_search_timer.start()
        )
        QShortcut(
            QKeySequence(QKeySequence.StandardKey.Find), self,
            activated=self._focus_account_search,
        )
        clear_search = QShortcut(
            QKeySequence("Escape"), self._account_search,
            activated=self._account_search.clear,
        )
        clear_search.setContext(Qt.ShortcutContext.WidgetShortcut)
        lay.addWidget(self._account_search)

        # group filter: dropdown + new group button
        group_row = QHBoxLayout()
        group_row.setContentsMargins(0, 0, 0, 0)
        group_row.setSpacing(10)
        self._group_combo = QComboBox()
        self._group_combo.setObjectName("groupCombo")
        self._group_combo.setMinimumWidth(130)
        self._group_combo.setMaximumWidth(240)
        self._group_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._group_combo.setToolTip("Show one group of accounts.\nRight-click to rename or delete the selected group.")
        self._group_combo.setAccessibleName("Account group")
        self._group_combo.currentIndexChanged.connect(self._on_group_combo_changed)
        self._group_combo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._group_combo.customContextMenuRequested.connect(self._on_group_combo_context)
        group_row.addWidget(self._group_combo)
        add_group_btn = QPushButton("+")
        add_group_btn.setObjectName("iconButton")
        add_group_btn.setToolTip("Create new group")
        add_group_btn.setAccessibleName("Create new group")
        add_group_btn.clicked.connect(self._on_add_group)
        group_row.addWidget(add_group_btn)
        group_row.addStretch(1)
        lay.addLayout(group_row)
        self._group_scroll = None

        # account list widget
        self._account_list = QListWidget()
        self._account_list.setObjectName("accountList")
        self._account_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._account_list.customContextMenuRequested.connect(self._on_account_context_menu)
        self._account_list.itemDoubleClicked.connect(self._on_account_double_clicked)
        self._account_list.setToolTip("Double-click an account to join the Place ID")
        self._account_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        self._drag_filter = _DragDropFilter(
            self._account_list,
            get_avatar=lambda u: self._avatar_labels[u].pixmap() if u in self._avatar_labels else None,
            parent=self,
        )
        self._account_list.viewport().installEventFilter(self._drag_filter)
        self._drag_filter.reorder_requested.connect(self._on_account_reorder)

        lay.addWidget(self._account_list, 1)

        # Quick Sign-in gets the full width on its own row.
        quick_btn = QPushButton("Quick Sign-in")
        quick_btn.setObjectName("primary")
        quick_btn.setIcon(icons.make_icon("enter", PRIMARY_TEXT, size=18))
        quick_btn.setIconSize(QSize(18, 18))
        quick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quick_btn.setToolTip(
            "Opens Roblox login and starts Quick Sign-in for you.\n"
            "Type the code it shows on a phone or PC where you're already signed in."
        )
        quick_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        quick_btn.clicked.connect(self._on_add_account_quick)
        self._quick_signin_btn = quick_btn
        lay.addSpacing(2)
        lay.addWidget(quick_btn)

        # Add and Remove buttons row
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        self._add_btn = QToolButton()
        self._add_btn.setText("  Add Account")
        self._add_btn.setIcon(icons.make_icon("plus", TEXT, size=18))
        self._add_btn.setIconSize(QSize(18, 18))
        self._add_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._add_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._add_btn.setFixedHeight(40)
        _arrow = _dropdown_arrow_icon_path(TEXT).replace("\\", "/")
        self._add_btn.setStyleSheet(
            f"QToolButton {{"
            f"  background: {INPUT}; border: 1px solid {LINE}; border-radius: 10px; font-size: 12px;"
            f"  padding: 2px 36px 2px 14px; color: {TEXT};"
            f"}}"
            f"QToolButton:hover {{ background: {RAISED}; border: 1px solid {ACCENT_TINT_LINE}; }}"
            f"QToolButton:pressed {{ background: {PRESSED}; }}"
            f"QToolButton::menu-button {{ width: 32px; border: 0; border-left: 1px solid {LINE};"
            f"  border-top-right-radius: 10px; border-bottom-right-radius: 10px; }}"
            f"QToolButton::menu-arrow {{ image: url({_arrow}); width: 10px; height: 10px; }}"
        )

        # Dropdown menu for add button
        add_menu = QMenu(self._add_btn)
        add_menu.addSection("Sign In")
        act_browser = add_menu.addAction("Browser Login")
        act_browser.setToolTip("Open the login page and sign in normally")
        act_quick = add_menu.addAction("Quick Sign-in (code)")
        act_quick.setToolTip(
            "Opens the login page and starts Quick Sign-in for you.\n"
            "Enter the code shown in the browser on a device where you are already signed in."
        )
        act_browser.triggered.connect(self._on_add_account_browser)
        act_quick.triggered.connect(self._on_add_account_quick)
        add_menu.setToolTipsVisible(True)
        add_menu.addSection("Import / Create")
        act_cookie = add_menu.addAction("Import Cookie")
        act_userpass = add_menu.addAction("Import User:Pass")
        act_creator = add_menu.addAction("Account Creator")
        act_js = add_menu.addAction("Javascript")
        act_cookie.triggered.connect(self._on_import_cookie)
        act_userpass.triggered.connect(self._on_import_userpass)
        act_creator.triggered.connect(self._on_account_creator)
        act_js.triggered.connect(self._on_add_javascript)
        self._add_btn.setMenu(add_menu)
        self._add_btn.clicked.connect(self._on_add_account_browser)

        remove_btn = QPushButton("  Remove")
        remove_btn.setObjectName("tileSmall")
        icons.set_accent_icon(remove_btn, "trash", _THEME, size=18)
        remove_btn.setMinimumHeight(40)
        remove_btn.setMaximumHeight(40)
        remove_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        remove_btn.clicked.connect(self._on_remove_account)

        bottom.addWidget(self._add_btn, 3)
        bottom.addWidget(remove_btn, 2)
        bottom.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(bottom)

        return panel

    def _on_account_double_clicked(self, item: QListWidgetItem) -> None:
        username = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not username:
            return
        # Join with just the account that was double-clicked.
        self._account_list.clearSelection()
        self._account_list.setCurrentItem(item)
        item.setSelected(True)
        self._on_join_place()

    def _open_account_row_menu(self, username: str, button: QWidget) -> None:
        viewport = self._account_list.viewport()
        pos = viewport.mapFromGlobal(button.mapToGlobal(QPoint(button.width() // 2, button.height() // 2)))
        item = self._account_list.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) != username:
            return
        self._on_account_context_menu(pos)


    def _build_auto_rejoin_panel(self) -> QFrame: # Auto Rejoin
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        ttl = QLabel("Auto-Rejoin")
        ttl.setObjectName("sectionTitle")
        hdr.addWidget(ttl)
        hdr.addStretch(1)
        hint = QLabel("Right-click for actions")
        hint.setStyleSheet(f"color: {MUTED}; font-size: 9px;")
        hdr.addWidget(hint)
        lay.addLayout(hdr)

        # Account list for auto rejoin
        self._ar_list = QListWidget()
        self._ar_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._ar_list.customContextMenuRequested.connect(self._ar_on_context_menu)
        lay.addWidget(self._ar_list, 1)
        QTimer.singleShot(0, self._ar_refresh_list) # defer

        bottom = QHBoxLayout() # Add / Start All / Stop All buttons
        bottom.setSpacing(6)
        _BTN_SS = f"QPushButton {{ text-align: center; color: {TEXT}; }}"
        add_btn = QPushButton("Add Account")
        add_btn.setStyleSheet(_BTN_SS)
        add_btn.clicked.connect(self._ar_on_add)
        start_all_btn = QPushButton("Start All")
        start_all_btn.setStyleSheet(_BTN_SS)
        start_all_btn.clicked.connect(self._ar_on_start_all)
        stop_all_btn = QPushButton("Stop All")
        stop_all_btn.setStyleSheet(_BTN_SS)
        stop_all_btn.clicked.connect(self._ar_on_stop_all)
        bottom.addWidget(add_btn, 1)
        bottom.addWidget(start_all_btn)
        bottom.addWidget(stop_all_btn)
        lay.addLayout(bottom)
        return panel

    def _build_anti_afk_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        ttl = QLabel("Anti-AFK")
        ttl.setObjectName("sectionTitle")
        hdr.addWidget(ttl)
        hdr.addStretch(1)
        self._afk_status_lbl = QLabel("Status: Stopped")
        self._afk_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        hdr.addWidget(self._afk_status_lbl)
        lay.addLayout(hdr)

        desc = QLabel(
            "Automatically sends key presses to all Roblox windows at set intervals to prevent AFK kicks."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(desc)

        # Enable checkbox
        self._afk_enabled_chk = QCheckBox("Enable Anti-AFK")
        self._afk_enabled_chk.setStyleSheet(f"QCheckBox {{ color: {TEXT}; font-size: 12px; font-weight: 700; }}")
        self._afk_enabled_chk.stateChanged.connect(self._on_afk_enabled_changed)
        lay.addWidget(self._afk_enabled_chk)

        debug_row = QHBoxLayout()
        debug_row.setContentsMargins(0, 0, 0, 0)
        self._afk_debug_btn = QPushButton("Trigger Anti-AFK Now")
        self._afk_debug_btn.setFixedHeight(26)
        self._afk_debug_btn.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 12px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:disabled {{ color: {MUTED}; }}"
        )
        self._afk_debug_btn.clicked.connect(self._on_afk_debug_trigger)
        debug_row.addWidget(self._afk_debug_btn)
        debug_row.addStretch(1)
        lay.addLayout(debug_row)

        # Settings form
        form = QVBoxLayout()
        form.setContentsMargins(0, 8, 0, 0)
        form.setSpacing(10)

        # Action Key row
        row_key = QHBoxLayout()
        row_key.setSpacing(8)
        lbl_key = QLabel("Action Key:")
        lbl_key.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        row_key.addWidget(lbl_key)
        
        self._afk_key_btn = QPushButton("W")
        self._afk_key_btn.setFixedWidth(100)
        self._afk_key_btn.setFixedHeight(24)
        self._afk_key_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._afk_key_btn.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; font-size: 11px; font-weight: 700; min-height: 24px; border-radius: 3px; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:focus {{ border: 1px solid {FG_ACCENT}; }}"
        )
        self._afk_key_btn.clicked.connect(self._on_afk_record_key)
        row_key.addWidget(self._afk_key_btn)
        
        self._afk_key_hint = QLabel("Click to record")
        self._afk_key_hint.setStyleSheet(f"color: {NOTE}; font-size: 10px;")
        self._afk_key_hint.hide()
        row_key.addWidget(self._afk_key_hint)
        row_key.addStretch(1)
        form.addLayout(row_key)

        # Press Count row
        row_press = QHBoxLayout()
        row_press.setSpacing(8)
        lbl_press = QLabel("Press Count:")
        lbl_press.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        row_press.addWidget(lbl_press)
        self._afk_press_spin = QSpinBox()
        self._afk_press_spin.setRange(1, 10)
        self._afk_press_spin.setValue(1)
        self._afk_press_spin.setFixedWidth(60)
        self._afk_press_spin.setStyleSheet(
            f"QSpinBox {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px; border-radius: 3px; }}"
            "QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: 0; }"
        )
        self._afk_press_spin.valueChanged.connect(self._on_afk_setting_changed)
        row_press.addWidget(self._afk_press_spin)
        row_press.addStretch(1)
        form.addLayout(row_press)

        # Interval row
        row_interval = QHBoxLayout()
        row_interval.setSpacing(8)
        lbl_interval = QLabel("Interval (min):")
        lbl_interval.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        row_interval.addWidget(lbl_interval)
        self._afk_interval_spin = QSpinBox()
        self._afk_interval_spin.setRange(1, 120)
        self._afk_interval_spin.setValue(10)
        self._afk_interval_spin.setFixedWidth(60)
        self._afk_interval_spin.setStyleSheet(
            f"QSpinBox {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px; border-radius: 3px; }}"
            "QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: 0; }"
        )
        self._afk_interval_spin.valueChanged.connect(self._on_afk_setting_changed)
        row_interval.addWidget(self._afk_interval_spin)
        row_interval.addStretch(1)
        form.addLayout(row_interval)

        # Delay between windows: each window is done on its own, then this wait.
        row_delay = QHBoxLayout()
        row_delay.setSpacing(8)
        lbl_delay = QLabel("Delay between windows (s):")
        lbl_delay.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        row_delay.addWidget(lbl_delay)
        self._afk_delay_spin = QDoubleSpinBox()
        self._afk_delay_spin.setRange(0.0, actions.AFK_DELAY_MAX)
        self._afk_delay_spin.setSingleStep(0.5)
        self._afk_delay_spin.setDecimals(1)
        self._afk_delay_spin.setValue(actions.afk_window_delay())
        self._afk_delay_spin.setFixedWidth(60)
        self._afk_delay_spin.setToolTip(
            "Anti AFK opens one Roblox window at a time (at its own size, starting with the\n"
            "top-left one), presses the key, puts it back, then waits this long before the next.\n"
            "A longer delay is easier on the PC with many windows."
        )
        self._afk_delay_spin.setStyleSheet(self._afk_interval_spin.styleSheet().replace("QSpinBox", "QDoubleSpinBox"))
        self._afk_delay_spin.valueChanged.connect(
            lambda value: actions.save_ui_setting(actions.AFK_DELAY_SETTING, round(float(value), 1)))
        row_delay.addWidget(self._afk_delay_spin)
        row_delay.addStretch(1)
        form.addLayout(row_delay)

        # Tooltip checkbox
        self._afk_tooltip_chk = QCheckBox("Show countdown tooltip")
        self._afk_tooltip_chk.setChecked(True)
        self._afk_tooltip_chk.setStyleSheet(f"QCheckBox {{ color: {MUTED}; font-size: 11px; }}")
        self._afk_tooltip_chk.stateChanged.connect(self._on_afk_setting_changed)
        form.addWidget(self._afk_tooltip_chk)

        tooltip_preview_row = QHBoxLayout()
        tooltip_preview_row.setContentsMargins(0, 0, 0, 0)
        self._afk_tooltip_preview_btn = QPushButton("Show Tooltip")
        self._afk_tooltip_preview_btn.setFixedHeight(26)
        self._afk_tooltip_preview_btn.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 12px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
        )
        self._afk_tooltip_preview_btn.clicked.connect(
            self._on_afk_tooltip_preview
        )
        tooltip_preview_row.addWidget(self._afk_tooltip_preview_btn)
        tooltip_preview_row.addStretch(1)
        form.addLayout(tooltip_preview_row)

        tooltip_size_row = QHBoxLayout()
        tooltip_size_row.setSpacing(8)
        tooltip_size_label = QLabel("Tooltip Size (%):")
        tooltip_size_label.setStyleSheet(
            f"color: {MUTED}; font-size: 11px; min-width: 80px;"
        )
        tooltip_size_row.addWidget(tooltip_size_label)
        self._afk_tooltip_size_spin = QSpinBox()
        self._afk_tooltip_size_spin.setRange(50, 200)
        self._afk_tooltip_size_spin.setSingleStep(10)
        self._afk_tooltip_size_spin.setValue(100)
        self._afk_tooltip_size_spin.setFixedWidth(60)
        self._afk_tooltip_size_spin.setStyleSheet(
            f"QSpinBox {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px; border-radius: 0; }}"
            "QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: 0; }"
        )
        self._afk_tooltip_size_spin.valueChanged.connect(
            self._on_afk_tooltip_size_changed
        )
        tooltip_size_row.addWidget(self._afk_tooltip_size_spin)
        tooltip_size_row.addStretch(1)
        form.addLayout(tooltip_size_row)

        lay.addLayout(form)
        lay.addStretch(1)

        self._load_afk_settings()
        
        # Install event filter for key recording
        self._key_grab_active = False
        self._afk_key_btn.installEventFilter(self)
        
        self._afk_tooltip = _FloatingTooltip()
        self._afk_tooltip.set_size_percent(self._afk_tooltip_size)
        self._afk_tooltip.hide()
        self._afk_tooltip_preview_visible = False
        
        actions.set_afk_tooltip_callback(self._on_afk_tooltip_emit)
        
        return panel

    def _load_afk_settings(self):
        saved = actions.load_ui_settings()
        self._afk_key = saved.get("anti_afk_key", "w")
        self._afk_press_count = saved.get("anti_afk_press_count", 1)
        self._afk_interval = saved.get("anti_afk_interval", 10)
        self._afk_tooltip_enabled = saved.get("anti_afk_tooltip_enabled", True)
        self._afk_tooltip_size = saved.get("anti_afk_tooltip_size", 100)
        self._afk_enabled = saved.get("anti_afk_enabled", False)

        self._afk_key_btn.setText(self._afk_key.upper())
        self._afk_press_spin.setValue(int(self._afk_press_count))
        self._afk_interval_spin.setValue(int(self._afk_interval))
        self._afk_tooltip_chk.setChecked(bool(self._afk_tooltip_enabled))
        self._afk_tooltip_size_spin.setValue(
            max(50, min(200, int(self._afk_tooltip_size)))
        )
        self._afk_enabled_chk.setChecked(bool(self._afk_enabled))
        self._update_afk_status()

    def _on_afk_setting_changed(self):
        self._afk_key = self._afk_key_btn.text().lower()
        self._afk_press_count = self._afk_press_spin.value()
        self._afk_interval = self._afk_interval_spin.value()
        self._afk_tooltip_enabled = self._afk_tooltip_chk.isChecked()
        self._save_afk_settings()
        if (
            getattr(self, "_afk_enabled", False)
            and self._afk_enabled_chk.isChecked()
        ):
            actions.start_anti_afk(
                self._afk_key,
                self._afk_press_count,
                self._afk_interval,
                self._afk_tooltip_enabled,
            )

    def _on_afk_record_key(self):
        self._afk_key_btn.setText("...")
        self._afk_key_hint.show()
        self._afk_key_btn.setFocus()
        self._key_grab_active = True

    def _on_afk_debug_trigger(self):
        actions.trigger_anti_afk()

    def _show_afk_tooltip_preview(self):
        cursor = QCursor.pos()
        self._afk_tooltip.show_message(
            "Anti-AFK Maintenance in 30s",
            cursor.x(),
            cursor.y(),
        )

    def _on_afk_tooltip_preview(self):
        self._afk_tooltip_preview_visible = not self._afk_tooltip_preview_visible
        if self._afk_tooltip_preview_visible:
            self._afk_tooltip_preview_btn.setText("Hide Tooltip")
            self._show_afk_tooltip_preview()
        else:
            self._afk_tooltip_preview_btn.setText("Show Tooltip")
            self._afk_tooltip.hide()

    def _on_afk_tooltip_size_changed(self, value):
        self._afk_tooltip_size = int(value)
        actions.save_ui_setting("anti_afk_tooltip_size", self._afk_tooltip_size)
        if hasattr(self, "_afk_tooltip"):
            self._afk_tooltip.set_size_percent(self._afk_tooltip_size)
            if getattr(self, "_afk_tooltip_preview_visible", False):
                self._show_afk_tooltip_preview()
    
    def _on_afk_tooltip_emit(self, message, x, y):
        self._bridge.afk_tooltip.emit(message, x, y)
    
    def _on_afk_tooltip_signal(self, message, x, y):
        if message is None:
            if self._afk_tooltip_preview_visible:
                self._show_afk_tooltip_preview()
            else:
                self._afk_tooltip.hide()
        else:
            self._afk_tooltip.show_message(message, x, y)

    def eventFilter(self, obj, event): # Handle key recording via event filter on the key button
        if obj == self._afk_key_btn and self._key_grab_active:
            if event.type() == event.Type.KeyPress:
                key_code = event.key()
                modifiers = event.modifiers()
                
                if key_code == Qt.Key_Escape:
                    self._key_grab_active = False
                    self._afk_key_hint.hide()
                    self._afk_key_btn.setText(self._afk_key.upper())
                    return True
                
                key_text = ""
                
                char_text = event.text().upper()
                if char_text and len(char_text) == 1 and char_text.isalpha():
                    key_text = char_text
                elif char_text and len(char_text) == 1 and char_text.isdigit():
                    key_text = char_text
                
                if not key_text:
                    key_map = {
                        Qt.Key_Space: "SPACE",
                        Qt.Key_Tab: "TAB",
                        Qt.Key_Backspace: "BACKSPACE",
                        Qt.Key_Return: "ENTER",
                        Qt.Key_Enter: "NUMPADENTER",
                        Qt.Key_Delete: "DELETE",
                        Qt.Key_Insert: "INSERT",
                        Qt.Key_Home: "HOME",
                        Qt.Key_End: "END",
                        Qt.Key_PageUp: "PGUP",
                        Qt.Key_PageDown: "PGDOWN",
                        Qt.Key_Down: "DOWN",
                        Qt.Key_Left: "LEFT",
                        Qt.Key_Right: "RIGHT",
                        Qt.Key_Up: "UP",

                        Qt.Key_F1: "F1", Qt.Key_F2: "F2", Qt.Key_F3: "F3",
                        Qt.Key_F4: "F4", Qt.Key_F5: "F5", Qt.Key_F6: "F6",
                        Qt.Key_F7: "F7", Qt.Key_F8: "F8", Qt.Key_F9: "F9",
                        Qt.Key_F10: "F10", Qt.Key_F11: "F11", Qt.Key_F12: "F12",

                        Qt.Key_Shift: "SHIFT",
                        Qt.Key_Control: "CTRL",
                        Qt.Key_Alt: "ALT",
                        Qt.Key_Meta: "WIN",
                        Qt.Key_AltGr: "ALTGR",

                        Qt.Key_CapsLock: "CAPSLOCK",
                        Qt.Key_NumLock: "NUMLOCK",
                        Qt.Key_ScrollLock: "SCROLLLOCK",

                        Qt.Key_Minus: "-",
                        Qt.Key_Equal: "=",
                        Qt.Key_BracketLeft: "[",
                        Qt.Key_BracketRight: "]",
                        Qt.Key_Backslash: "\\",
                        Qt.Key_Semicolon: ";",
                        Qt.Key_QuoteLeft: "'",
                        Qt.Key_Comma: ",",
                        Qt.Key_Period: ".",
                        Qt.Key_Slash: "/",
                        Qt.Key_QuoteLeft: "`",

                        Qt.Key_A: "A", Qt.Key_B: "B", Qt.Key_C: "C",
                        Qt.Key_D: "D", Qt.Key_E: "E", Qt.Key_F: "F",
                        Qt.Key_G: "G", Qt.Key_H: "H", Qt.Key_I: "I",
                        Qt.Key_J: "J", Qt.Key_K: "K", Qt.Key_L: "L",
                        Qt.Key_M: "M", Qt.Key_N: "N", Qt.Key_O: "O",
                        Qt.Key_P: "P", Qt.Key_Q: "Q", Qt.Key_R: "R",
                        Qt.Key_S: "S", Qt.Key_T: "T", Qt.Key_U: "U",
                        Qt.Key_V: "V", Qt.Key_W: "W", Qt.Key_X: "X",
                        Qt.Key_Y: "Y", Qt.Key_Z: "Z",
                        Qt.Key_0: "0", Qt.Key_1: "1", Qt.Key_2: "2",
                        Qt.Key_3: "3", Qt.Key_4: "4", Qt.Key_5: "5",
                        Qt.Key_6: "6", Qt.Key_7: "7", Qt.Key_8: "8",
                        Qt.Key_9: "9",
                    }
                    key_text = key_map.get(key_code, "")
                    
                    if not key_text and Qt.Key_0 <= key_code <= Qt.Key_9:
                        if modifiers & Qt.KeyboardModifier.KeypadModifier:
                            key_text = f"NUMPAD{chr(key_code)}"
                
                if key_text:
                    self._afk_key = key_text.lower()
                    self._afk_key_btn.setText(key_text)
                    self._afk_key_hint.hide()
                    self._key_grab_active = False
                    self._on_afk_setting_changed()
                else:
                    self._key_grab_active = False
                    self._afk_key_hint.hide()
                    self._afk_key_btn.setText(self._afk_key.upper())
                return True

            elif event.type() == QEvent.MouseButtonPress:
                button = event.button()

                mouse_map = {
                    Qt.LeftButton: "LMB",
                    Qt.RightButton: "RMB",
                    Qt.MiddleButton: "MMB",
                    Qt.BackButton: "MBACK",
                    Qt.ForwardButton: "MFWD",
                }

                key_text = mouse_map.get(button)

                if key_text:
                    self._afk_key = key_text.lower()
                    self._afk_key_btn.setText(key_text)
                    self._afk_key_hint.hide()
                    self._key_grab_active = False
                    self._on_afk_setting_changed()
                return True
        return super().eventFilter(obj, event)

    def _on_afk_enabled_changed(self, state):
        self._afk_enabled = (state == Qt.CheckState.Checked.value)
        self._save_afk_settings()
        self._update_afk_status()
        if self._afk_enabled:
            actions.start_anti_afk(
                self._afk_key,
                self._afk_press_count,
                self._afk_interval,
                self._afk_tooltip_enabled,
            )
        else:
            actions.stop_anti_afk()

    def _update_afk_status(self):
        status = "Running" if self._afk_enabled else "Stopped"
        color = self._AR_ACTIVE_COLOR if self._afk_enabled else self._AR_INACTIVE_COLOR
        self._afk_status_lbl.setText(f"Status: {status}")
        self._afk_status_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._afk_debug_btn.setEnabled(self._afk_enabled)

    def _save_afk_settings(self):
        actions.save_ui_setting("anti_afk_key", self._afk_key)
        actions.save_ui_setting("anti_afk_press_count", self._afk_press_count)
        actions.save_ui_setting("anti_afk_interval", self._afk_interval)
        actions.save_ui_setting("anti_afk_tooltip_enabled", self._afk_tooltip_enabled)
        actions.save_ui_setting("anti_afk_tooltip_size", self._afk_tooltip_size)
        actions.save_ui_setting("anti_afk_enabled", self._afk_enabled)

    # Account Health (formerly Ban Check) -------------------------------------------------------------
    def _build_ban_check_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        ttl = QLabel("Account Health")
        ttl.setObjectName("sectionTitle")
        hdr.addWidget(ttl)
        hdr.addStretch(1)
        self._ban_status_lbl = QLabel("Not scanned yet")
        self._ban_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        hdr.addWidget(self._ban_status_lbl)
        lay.addLayout(hdr)

        desc = QLabel(
            "Scans every saved account and lists the ones that are banned, terminated, whose login "
            "no longer works (locked / signed out), or that got Roblox's \"not a bot\" security check "
            "when launching (Needs verification). Tick the ones you want gone (one by one, or a whole "
            "status with Tick by status) and delete them. OK accounts can't be ticked."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(desc)

        btn_style = (
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 12px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:disabled {{ color: {MUTED}; }}"
        )
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._ban_scan_btn = QPushButton("Scan All Accounts")
        self._ban_scan_btn.clicked.connect(self._on_ban_scan)
        self._ban_stop_btn = QPushButton("Stop")
        self._ban_stop_btn.setEnabled(False)
        self._ban_stop_btn.clicked.connect(self._on_ban_stop)
        for b in (self._ban_scan_btn, self._ban_stop_btn):
            b.setFixedHeight(26)
            b.setStyleSheet(btn_style)
            top_row.addWidget(b)
        top_row.addSpacing(8)
        self._ban_show_ok_chk = QCheckBox("Show OK accounts")
        self._ban_show_ok_chk.setStyleSheet(f"QCheckBox {{ color: {MUTED}; font-size: 11px; }}")
        self._ban_show_ok_chk.toggled.connect(self._apply_ban_filter)
        top_row.addWidget(self._ban_show_ok_chk)
        top_row.addStretch(1)
        lay.addLayout(top_row)

        # Pick by status: tick (or untick) every account with that status.
        # There is deliberately no OK button; OK accounts can't be ticked.
        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)
        pick_lbl = QLabel("Tick by status:")
        pick_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        pick_row.addWidget(pick_lbl)
        pick_style = (
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 3px 10px; border-radius: 0; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:checked {{ background: {ACCENT_TINT}; border-color: {ACCENT_TINT_LINE}; color: {ACCENT_TEXT}; }}"
            f"QPushButton:disabled {{ color: {MUTED}; }}"
        )
        self._ban_status_btns: dict[str, QPushButton] = {}
        for status in HEALTH_PICK_STATUSES:
            btn = QPushButton(f"{health_mod.LABELS[status]} (0)")
            btn.setCheckable(True)
            btn.setEnabled(False)
            btn.setFixedHeight(24)
            btn.setStyleSheet(pick_style)
            btn.setToolTip(f"Tick every {health_mod.LABELS[status]} account. Click again to untick them.")
            btn.clicked.connect(lambda checked, st=status: self._tick_ban_status(st, checked))
            self._ban_status_btns[status] = btn
            pick_row.addWidget(btn)
        pick_row.addStretch(1)
        lay.addLayout(pick_row)

        self._ban_tree = QTreeWidget()
        self._ban_tree.setColumnCount(3)
        self._ban_tree.setHeaderLabels(["Account", "Status", "Details"])
        self._ban_tree.setRootIsDecorated(False)
        self._ban_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._ban_tree.header().setStretchLastSection(True)
        self._ban_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._ban_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._ban_tree.itemChanged.connect(lambda *_: self._update_ban_delete_buttons())
        lay.addWidget(self._ban_tree, 1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        self._ban_tick_all_btn = QPushButton("Tick All Problems")
        self._ban_tick_all_btn.setToolTip("Ticks Banned, Terminated, Locked and Needs verification accounts")
        self._ban_tick_all_btn.clicked.connect(lambda: self._set_ban_ticks(True))
        self._ban_untick_btn = QPushButton("Untick All")
        self._ban_untick_btn.clicked.connect(lambda: self._set_ban_ticks(False))
        self._ban_delete_btn = QPushButton("Delete Ticked (0)")
        self._ban_delete_btn.setEnabled(False)
        self._ban_delete_btn.clicked.connect(self._on_ban_delete)
        for b in (self._ban_tick_all_btn, self._ban_untick_btn):
            b.setFixedHeight(26)
            b.setStyleSheet(btn_style)
            bottom_row.addWidget(b)
        bottom_row.addStretch(1)
        self._ban_delete_btn.setFixedHeight(26)
        self._ban_delete_btn.setStyleSheet(
            "QPushButton { background: #5a1f1f; border: 1px solid #7a2a2a;"
            " color: #ffffff; padding: 4px 12px; border-radius: 0; font-weight: 700; }"
            "QPushButton:hover { background: #742828; }"
            f"QPushButton:disabled {{ background: {INPUT}; border: 1px solid {LINE}; color: {MUTED}; }}"
        )
        bottom_row.addWidget(self._ban_delete_btn)
        lay.addLayout(bottom_row)

        self._ban_checker = None
        self._ban_items: dict[str, QTreeWidgetItem] = {}
        return panel

    _BAN_COLORS = {
        health_mod.NEEDS_VERIFY: "#d4b44a",
        health_mod.BANNED: "#e06c6c",
        health_mod.TERMINATED: "#e06c6c",
        health_mod.LOCKED: "#e0a85c",
        health_mod.UNKNOWN: "#9a9a9a",
        health_mod.OK: "#6cc08a",
    }

    def _on_ban_scan(self) -> None:
        if self._ban_checker is not None and self._ban_checker.running:
            return
        if not self.manager.accounts:
            self._ban_status_lbl.setText("No accounts saved")
            return
        self._ban_tree.clear()
        self._ban_items.clear()
        self._refresh_ban_status_buttons()
        self._ban_scan_btn.setEnabled(False)
        self._ban_stop_btn.setEnabled(True)
        self._ban_delete_btn.setEnabled(False)
        self._ban_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._ban_status_lbl.setText("Scanning...")
        settings = actions.load_ui_settings()
        delay = cookie_validator_mod.get_validation_delay(settings)
        self._ban_checker = health_mod.AccountHealthChecker(
            self.manager,
            on_result=lambda r, i, t: self._bridge.ban_check_result.emit(r, i, t),
            on_done=lambda summary: self._bridge.ban_check_done.emit(summary),
            delay_sec=delay,
        )
        self._ban_checker.start()

    def _on_ban_stop(self) -> None:
        if self._ban_checker is not None:
            self._ban_checker.stop()
        self._ban_stop_btn.setEnabled(False)
        self._ban_status_lbl.setText("Stopping...")

    def _on_ban_check_result(self, result, index: int, total: int) -> None:
        if not isValid(self._ban_tree):
            return
        item = QTreeWidgetItem([result.username, result.label, result.detail])
        item.setData(0, Qt.ItemDataRole.UserRole, result.status)
        problem = result.status in health_mod.PROBLEM_STATUSES
        if problem or result.status in (health_mod.UNKNOWN, *health_mod.ATTENTION_STATUSES):
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if problem else Qt.CheckState.Unchecked)
        else:
            # OK accounts can never be ticked (tree items are tickable by default).
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        item.setForeground(1, QColor(self._BAN_COLORS.get(result.status, TEXT)))
        item.setToolTip(2, result.detail)
        self._ban_tree.blockSignals(True)
        self._ban_tree.addTopLevelItem(item)
        self._ban_tree.blockSignals(False)
        self._ban_items[result.username] = item
        item.setHidden(result.status == health_mod.OK and not self._ban_show_ok_chk.isChecked())
        self._ban_status_lbl.setText(f"Scanning {index}/{total}...")
        self._update_ban_delete_buttons()

    def _on_ban_check_done(self, summary: dict) -> None:
        if not isValid(self._ban_status_lbl):
            return
        self._ban_scan_btn.setEnabled(True)
        self._ban_stop_btn.setEnabled(False)
        counts = summary.get("counts", {})
        problems = sum(counts.get(s, 0) for s in health_mod.PROBLEM_STATUSES)
        parts = [f"{health_mod.LABELS[k]}: {v}" for k, v in counts.items() if v]
        text = f"{problems} problem account{'s' if problems != 1 else ''}  ·  " + ", ".join(parts)
        if summary.get("stopped"):
            text = summary["stopped"] + "  " + text
        self._ban_status_lbl.setText(text)
        color = "#e06c6c" if problems else MUTED
        self._ban_status_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._update_ban_delete_buttons()

    def _apply_ban_filter(self, *_args) -> None:
        show_ok = self._ban_show_ok_chk.isChecked()
        for item in self._ban_items.values():
            status = item.data(0, Qt.ItemDataRole.UserRole)
            item.setHidden(status == health_mod.OK and not show_ok)

    def _set_ban_ticks(self, tick: bool) -> None:
        self._ban_tree.blockSignals(True)
        for item in self._ban_items.values():
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            status = item.data(0, Qt.ItemDataRole.UserRole)
            if tick:
                if status in health_mod.PROBLEM_STATUSES or status in health_mod.ATTENTION_STATUSES:
                    item.setCheckState(0, Qt.CheckState.Checked)
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)
        self._ban_tree.blockSignals(False)
        self._update_ban_delete_buttons()

    def _tick_ban_status(self, status: str, tick: bool) -> None:
        if status == health_mod.OK:
            return
        state = Qt.CheckState.Checked if tick else Qt.CheckState.Unchecked
        self._ban_tree.blockSignals(True)
        for item in self._ban_items.values():
            if (item.data(0, Qt.ItemDataRole.UserRole) == status
                    and item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                item.setCheckState(0, state)
        self._ban_tree.blockSignals(False)
        self._update_ban_delete_buttons()

    def _refresh_ban_status_buttons(self) -> None:
        buttons = getattr(self, "_ban_status_btns", None) or {}
        for status, btn in buttons.items():
            if not isValid(btn):
                continue
            items = [
                item for item in self._ban_items.values()
                if item.data(0, Qt.ItemDataRole.UserRole) == status
                and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
            ]
            btn.setText(f"{health_mod.LABELS[status]} ({len(items)})")
            btn.setEnabled(bool(items))
            btn.blockSignals(True)
            btn.setChecked(bool(items) and all(i.checkState(0) == Qt.CheckState.Checked for i in items))
            btn.blockSignals(False)

    def _ticked_ban_usernames(self) -> list[str]:
        return [
            name for name, item in self._ban_items.items()
            if (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            and item.checkState(0) == Qt.CheckState.Checked
        ]

    def _update_ban_delete_buttons(self) -> None:
        if not isValid(self._ban_delete_btn):
            return
        self._refresh_ban_status_buttons()
        count = len(self._ticked_ban_usernames())
        running = self._ban_checker is not None and self._ban_checker.running
        self._ban_delete_btn.setText(f"Delete Ticked ({count})")
        self._ban_delete_btn.setEnabled(count > 0 and not running)

    def _on_ban_delete(self) -> None:
        usernames = self._ticked_ban_usernames()
        if not usernames:
            return
        preview = "\n".join(
            f"  • {u}  ({self._ban_items[u].text(1)})" for u in usernames[:15]
        )
        if len(usernames) > 15:
            preview += f"\n  ... and {len(usernames) - 15} more"
        reply = QMessageBox.question(
            self, "Delete Accounts",
            f"Remove {len(usernames)} account{'s' if len(usernames) != 1 else ''} from the manager?\n\n"
            f"{preview}\n\nThis only removes them from this app, not from Roblox. It cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed, failed = 0, []
        for username in usernames:
            ok, msg = actions.remove_account(self.manager, username)
            if ok:
                removed += 1
                item = self._ban_items.pop(username, None)
                if item is not None:
                    idx = self._ban_tree.indexOfTopLevelItem(item)
                    if idx >= 0:
                        self._ban_tree.takeTopLevelItem(idx)
            else:
                failed.append(f"{username}: {msg}")
        self._refresh_account_list()
        self._update_ban_delete_buttons()
        self._ban_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._ban_status_lbl.setText(f"Removed {removed} account{'s' if removed != 1 else ''}")
        print(f"[INFO] Ban check: removed {removed} account(s)")
        if failed:
            _show_error(self, "Some accounts were not removed", "\n".join(failed[:20]))

    # Proxy ---------------------------------------------------------------
    def _build_proxy_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        ttl = QLabel("Proxy")
        ttl.setObjectName("sectionTitle")
        hdr.addWidget(ttl)
        hdr.addStretch(1)
        self._proxy_status_lbl = QLabel("Status: Off")
        self._proxy_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        hdr.addWidget(self._proxy_status_lbl)
        lay.addLayout(hdr)

        desc = QLabel(
            "Routes the manager's Roblox web requests (login, launching, cookie checks, "
            "server lists) through a proxy. The game client's own in-game connection is not proxied."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(desc)

        # The checkbox holds the state; the big button is what people click.
        self._proxy_enabled_chk = QCheckBox("Enable Proxy")
        self._proxy_enabled_chk.setStyleSheet(
            f"QCheckBox {{ color: {TEXT}; font-size: 12px; font-weight: 700; }}"
        )
        self._proxy_enabled_chk.toggled.connect(self._on_proxy_enabled_toggled)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(10)
        self._proxy_toggle_btn = QPushButton("OFF")
        self._proxy_toggle_btn.setCheckable(True)
        self._proxy_toggle_btn.setFixedSize(84, 30)
        self._proxy_toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._proxy_toggle_btn.setToolTip("Turn the proxy on or off")
        self._proxy_toggle_btn.clicked.connect(
            lambda checked: self._proxy_enabled_chk.setChecked(bool(checked))
        )
        self._proxy_enabled_chk.toggled.connect(self._sync_proxy_toggle_btn)
        self._proxy_toggle_btn.hide()  # replaced by the checkbox
        toggle_row.addWidget(self._proxy_enabled_chk)
        self._proxy_live_lbl = QLabel("Status: Off")
        self._proxy_live_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 700;")
        toggle_row.addWidget(self._proxy_live_lbl)
        toggle_row.addStretch(1)
        lay.addLayout(toggle_row)
        self._proxy_ping_text = ""

        label_style = f"color: {MUTED}; font-size: 11px; min-width: 90px;"

        # Mode
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        mode_lbl = QLabel("Mode:")
        mode_lbl.setStyleSheet(label_style)
        mode_row.addWidget(mode_lbl)
        self._proxy_mode_gateway = QRadioButton("Rotating gateway")
        self._proxy_mode_gateway.setToolTip(
            "One address from your provider. The provider changes the IP "
            "(per request, or every few minutes with a sticky session)."
        )
        self._proxy_mode_list = QRadioButton("Proxy list (app rotates)")
        self._proxy_mode_list.setToolTip("Several proxies. The app switches to the next one on a timer.")
        self._proxy_mode_group = QButtonGroup(panel)
        self._proxy_mode_group.addButton(self._proxy_mode_gateway)
        self._proxy_mode_group.addButton(self._proxy_mode_list)
        self._proxy_mode_gateway.toggled.connect(self._update_proxy_mode_visibility)
        mode_row.addWidget(self._proxy_mode_gateway)
        mode_row.addWidget(self._proxy_mode_list)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)

        # Default type
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_lbl = QLabel("Default type:")
        type_lbl.setStyleSheet(label_style)
        type_row.addWidget(type_lbl)
        self._proxy_scheme_combo = QComboBox()
        self._proxy_scheme_combo.addItem("SOCKS5 (remote DNS)", "socks5h")
        self._proxy_scheme_combo.addItem("SOCKS5 (local DNS)", "socks5")
        self._proxy_scheme_combo.addItem("SOCKS4", "socks4")
        self._proxy_scheme_combo.addItem("HTTP", "http")
        self._proxy_scheme_combo.addItem("HTTPS", "https")
        self._proxy_scheme_combo.setToolTip("Used for entries written without socks5:// or http://")
        self._proxy_scheme_combo.setFixedWidth(170)
        type_row.addWidget(self._proxy_scheme_combo)
        type_row.addStretch(1)
        lay.addLayout(type_row)

        formats = QLabel(
            "Any format works: host:port · host:port:user:pass · user:pass@host:port · "
            "host:port@user:pass · user:pass:host:port · \"host port user pass\" · "
            "with or without socks5:// socks4:// http:// https://"
        )
        formats.setWordWrap(True)
        formats.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(formats)

        # Gateway
        self._proxy_gateway_box = QWidget()
        gw_lay = QHBoxLayout(self._proxy_gateway_box)
        gw_lay.setContentsMargins(0, 0, 0, 0)
        gw_lay.setSpacing(8)
        gw_lbl = QLabel("Proxy:")
        gw_lbl.setStyleSheet(label_style)
        gw_lay.addWidget(gw_lbl)
        self._proxy_gateway_edit = QLineEdit()
        self._proxy_gateway_edit.setPlaceholderText("host:port:user:pass  or  socks5://user:pass@host:port")
        self._proxy_gateway_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        gw_lay.addWidget(self._proxy_gateway_edit, 1)
        lay.addWidget(self._proxy_gateway_box)

        self._proxy_manual_chk = QCheckBox("Enter details manually")
        self._proxy_manual_chk.setStyleSheet(f"QCheckBox {{ color: {MUTED}; font-size: 11px; }}")
        lay.addWidget(self._proxy_manual_chk)

        self._proxy_manual_box = QWidget()
        man_lay = QVBoxLayout(self._proxy_manual_box)
        man_lay.setContentsMargins(0, 0, 0, 0)
        man_lay.setSpacing(6)
        self._proxy_manual_fields = {}
        for key, label, placeholder, secret in (
            ("host", "Host / IP:", "e.g. gate.provider.com or 1.2.3.4", False),
            ("port", "Port:", "e.g. 1080", False),
            ("user", "Username:", "leave empty if none", False),
            ("password", "Password:", "leave empty if none", True),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 90px;")
            row.addWidget(lbl)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            if secret:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            row.addWidget(edit, 1)
            man_lay.addLayout(row)
            self._proxy_manual_fields[key] = edit
        lay.addWidget(self._proxy_manual_box)
        self._proxy_manual_chk.toggled.connect(self._update_proxy_mode_visibility)

        self._proxy_gateway_hint = QLabel(
            "Tip: to keep one IP for a few minutes, use your provider's sticky-session username "
            "(for example user-session-abc123-sesstime-5). Check your provider's docs for the exact format."
        )
        self._proxy_gateway_hint.setWordWrap(True)
        self._proxy_gateway_hint.setStyleSheet(f"color: {NOTE}; font-size: 10px;")
        lay.addWidget(self._proxy_gateway_hint)

        # List
        self._proxy_list_box = QWidget()
        list_lay = QVBoxLayout(self._proxy_list_box)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(8)
        list_lbl = QLabel("Proxies (one per line):")
        list_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        list_lay.addWidget(list_lbl)
        self._proxy_list_edit = QTextEdit()
        self._proxy_list_edit.setAcceptRichText(False)
        self._proxy_list_edit.setPlaceholderText(
            "1.2.3.4:1080:user:pass\nsocks5://user:pass@5.6.7.8:1080\nhttp://9.9.9.9:8080"
        )
        self._proxy_list_edit.setMinimumHeight(110)
        list_lay.addWidget(self._proxy_list_edit, 1)

        rot_row = QHBoxLayout()
        rot_row.setSpacing(8)
        rot_lbl = QLabel("Rotate every (min):")
        rot_lbl.setStyleSheet(label_style)
        rot_row.addWidget(rot_lbl)
        self._proxy_rotate_spin = QSpinBox()
        self._proxy_rotate_spin.setRange(1, 1440)
        self._proxy_rotate_spin.setValue(5)
        self._proxy_rotate_spin.setFixedWidth(60)
        self._proxy_rotate_spin.setStyleSheet(
            f"QSpinBox {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px; border-radius: 3px; }}"
            "QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: 0; }"
        )
        rot_row.addWidget(self._proxy_rotate_spin)
        self._proxy_random_chk = QCheckBox("Random order")
        self._proxy_random_chk.setStyleSheet(f"QCheckBox {{ color: {MUTED}; font-size: 11px; }}")
        rot_row.addWidget(self._proxy_random_chk)
        rot_row.addStretch(1)
        list_lay.addLayout(rot_row)
        lay.addWidget(self._proxy_list_box, 1)

        # Buttons
        btn_style = (
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 12px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:disabled {{ color: {MUTED}; }}"
        )
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._proxy_save_btn = QPushButton("Save && Apply")
        self._proxy_save_btn.clicked.connect(self._on_proxy_save)
        self._proxy_rotate_btn = QPushButton("Rotate Now")
        self._proxy_rotate_btn.clicked.connect(self._on_proxy_rotate_now)
        self._proxy_test_btn = QPushButton("Test / Check IP")
        self._proxy_test_btn.setToolTip("Shows the public IP Roblox will see for the manager's requests")
        self._proxy_test_btn.clicked.connect(self._on_proxy_test)
        for btn in (self._proxy_save_btn, self._proxy_rotate_btn, self._proxy_test_btn):
            btn.setFixedHeight(26)
            btn.setStyleSheet(btn_style)
            btn_row.addWidget(btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self._proxy_result_lbl = QLabel("")
        self._proxy_result_lbl.setWordWrap(True)
        self._proxy_result_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._proxy_result_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        lay.addWidget(self._proxy_result_lbl)

        self._load_proxy_settings()

        self._proxy_status_timer = QTimer(self)
        self._proxy_status_timer.setInterval(1000)
        self._proxy_status_timer.timeout.connect(self._refresh_proxy_status)
        self._proxy_status_timer.start()
        self._refresh_proxy_status()
        return panel

    def _load_proxy_settings(self) -> None:
        cfg = proxy_mod.load_config()
        widgets = (
            self._proxy_enabled_chk, self._proxy_mode_gateway, self._proxy_mode_list,
            self._proxy_scheme_combo, self._proxy_rotate_spin, self._proxy_random_chk,
        )
        for w in widgets:
            w.blockSignals(True)
        self._proxy_enabled_chk.setChecked(bool(cfg["enabled"]))
        if cfg["mode"] == proxy_mod.MODE_LIST:
            self._proxy_mode_list.setChecked(True)
        else:
            self._proxy_mode_gateway.setChecked(True)
        idx = self._proxy_scheme_combo.findData(cfg["default_scheme"])
        self._proxy_scheme_combo.setCurrentIndex(max(0, idx))
        self._proxy_gateway_edit.setText(cfg["gateway"])
        manual = actions.get_ui_setting("proxy_manual", {}) or {}
        if isinstance(manual, dict) and manual.get("enabled"):
            self._proxy_manual_chk.setChecked(True)
            for key in ("host", "port", "user"):
                self._proxy_manual_fields[key].setText(str(manual.get(key, "") or ""))
            # The password lives only in the saved proxy entry, never separately.
            parts = str(cfg["gateway"]).split(":", 3)
            if len(parts) == 4:
                self._proxy_manual_fields["password"].setText(parts[3])
        self._proxy_list_edit.setPlainText("\n".join(cfg["proxies"]))
        self._proxy_rotate_spin.setValue(int(cfg["rotate_minutes"]))
        self._proxy_random_chk.setChecked(bool(cfg["randomize"]))
        for w in widgets:
            w.blockSignals(False)
        self._update_proxy_mode_visibility()
        self._sync_proxy_toggle_btn(self._proxy_enabled_chk.isChecked())

    def _collect_proxy_config(self) -> dict:
        lines = [
            line.strip()
            for line in self._proxy_list_edit.toPlainText().splitlines()
            if line.strip()
        ]
        return {
            "enabled": self._proxy_enabled_chk.isChecked(),
            "mode": proxy_mod.MODE_LIST if self._proxy_mode_list.isChecked() else proxy_mod.MODE_GATEWAY,
            "default_scheme": self._proxy_scheme_combo.currentData() or "socks5h",
            "gateway": self._manual_proxy_text() if self._proxy_manual_chk.isChecked()
            else self._proxy_gateway_edit.text().strip(),
            "proxies": lines,
            "rotate_minutes": self._proxy_rotate_spin.value(),
            "randomize": self._proxy_random_chk.isChecked(),
        }

    def _manual_proxy_text(self) -> str:
        f = {k: e.text().strip() for k, e in self._proxy_manual_fields.items()}
        actions.save_ui_setting("proxy_manual", {
            "enabled": True, "host": f["host"], "port": f["port"], "user": f["user"],
        })
        if not f["host"] or not f["port"]:
            return ""
        if f["user"]:
            return f"{f['host']}:{f['port']}:{f['user']}:{f['password']}"
        return f"{f['host']}:{f['port']}"

    def _sync_proxy_toggle_btn(self, on: bool) -> None:
        btn = self._proxy_toggle_btn
        btn.blockSignals(True)
        btn.setChecked(bool(on))
        btn.blockSignals(False)
        btn.setText("ON" if on else "OFF")
        btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BRIGHT if on else INPUT}; color: {ON_ACCENT if on else TEXT};"
            f" border: 1px solid {ACCENT_BRIGHT if on else LINE}; font-weight: 700; font-size: 12px; }}"
        )

    def _update_proxy_mode_visibility(self, *_args) -> None:
        is_list = self._proxy_mode_list.isChecked()
        manual = self._proxy_manual_chk.isChecked() and not is_list
        self._proxy_manual_chk.setVisible(not is_list)
        self._proxy_manual_box.setVisible(manual)
        self._proxy_gateway_box.setVisible(not is_list and not manual)
        self._proxy_gateway_hint.setVisible(not is_list)
        self._proxy_list_box.setVisible(is_list)
        self._proxy_rotate_btn.setVisible(is_list)

    def _apply_proxy_config(self, cfg: dict) -> None:
        raw = cfg["proxies"] if cfg["mode"] == proxy_mod.MODE_LIST else [cfg["gateway"]]
        raw = [line for line in raw if line and not line.startswith("#")]
        valid = proxy_mod.resolved_proxies(cfg)
        bad = len(raw) - len(valid)
        error = ""

        if cfg["enabled"] and not valid:
            error = "No valid proxy entered. Proxy stays off."
            cfg = dict(cfg, enabled=False)
            self._proxy_enabled_chk.blockSignals(True)
            self._proxy_enabled_chk.setChecked(False)
            self._proxy_enabled_chk.blockSignals(False)
        elif cfg["enabled"] and any(u.startswith("socks") for u in valid) and not proxy_mod.socks_available():
            error = 'SOCKS support is missing. Run: pip install "requests[socks]" and restart.'

        proxy_mod.save_config(cfg)
        proxy_mod.manager.apply(cfg)
        self._proxy_ping_tick = 0
        self._proxy_ping_text = ""
        self._refresh_proxy_status()

        if error:
            self._proxy_result_lbl.setStyleSheet("color: #e06c6c; font-size: 11px;")
            self._proxy_result_lbl.setText(error)
            return
        if cfg["enabled"]:
            note = f"Saved. {len(valid)} {'proxy' if len(valid) == 1 else 'proxies'} loaded"
            if bad:
                note += f", {bad} unreadable line{'s' if bad != 1 else ''} skipped"
            note += "."
        else:
            note = "Saved. Proxy is off."
        self._proxy_result_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._proxy_result_lbl.setText(note)

    def _on_proxy_save(self) -> None:
        if not self._proxy_manual_chk.isChecked():
            actions.save_ui_setting("proxy_manual", {"enabled": False})
        self._apply_proxy_config(self._collect_proxy_config())

    def _on_proxy_enabled_toggled(self, _checked: bool) -> None:
        self._apply_proxy_config(self._collect_proxy_config())

    def _on_proxy_rotate_now(self) -> None:
        new = proxy_mod.manager.rotate_now()
        self._proxy_result_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._proxy_result_lbl.setText(f"Switched to {new}" if new else "Proxy is off.")
        self._refresh_proxy_status()

    def _on_proxy_test(self) -> None:
        url = proxy_mod.manager.current
        if not url:
            # Not enabled yet: test the first entry in the form so it can be tried before saving.
            entries = proxy_mod.resolved_proxies(self._collect_proxy_config())
            url = entries[0] if entries else None
        self._proxy_test_btn.setEnabled(False)
        self._proxy_result_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._proxy_result_lbl.setText(
            f"Testing {proxy_mod.mask(url)}..." if url else "No proxy set, checking your direct IP..."
        )

        def _worker(target=url):
            ok, info, secs = proxy_mod.test_proxy(target)
            label = proxy_mod.mask(target) if target else "Direct connection"
            if ok:
                msg = f"{label}  →  public IP {info}  ({secs * 1000:.0f} ms)"
            else:
                msg = f"{label}  →  failed: {info}"
            self._bridge.proxy_test_done.emit(ok, msg)

        threading.Thread(target=_worker, daemon=True, name="ProxyTest").start()

    def _on_proxy_test_done(self, ok: bool, message: str) -> None:
        if not isValid(self._proxy_test_btn):
            return
        self._proxy_test_btn.setEnabled(True)
        color = MUTED if ok else "#e06c6c"
        self._proxy_result_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._proxy_result_lbl.setText(message)

    def _start_proxy_ping(self) -> None:
        url = proxy_mod.manager.current
        if not url or getattr(self, "_proxy_ping_running", False):
            return
        self._proxy_ping_running = True

        def _worker(target=url):
            ok, info, secs = proxy_mod.test_proxy(target, timeout=10)
            self._bridge.proxy_ping_done.emit(ok, f"{secs * 1000:.0f} ms" if ok else "no response")

        threading.Thread(target=_worker, daemon=True, name="ProxyPing").start()

    def _on_proxy_ping_done(self, ok: bool, text: str) -> None:
        self._proxy_ping_running = False
        self._proxy_ping_text = text if ok else "⚠ " + text
        self._refresh_proxy_status()

    def _refresh_proxy_status(self) -> None:
        if not isValid(self._proxy_status_lbl):
            return
        st = proxy_mod.manager.status()
        live = getattr(self, "_proxy_live_lbl", None)
        if live is not None and isValid(live):
            if st["enabled"]:
                ping = self._proxy_ping_text or "checking…"
                live.setText(f"Status: On  ·  Ping {ping}")
                bad = self._proxy_ping_text.startswith("⚠")
                live.setStyleSheet(
                    f"color: {'#e06c6c' if bad else ACCENT_BRIGHT}; font-size: 12px; font-weight: 700;"
                )
                # Ping every 30 seconds while on.
                tick = getattr(self, "_proxy_ping_tick", 0) + 1
                self._proxy_ping_tick = tick
                if tick == 1 or tick % 30 == 0:
                    self._start_proxy_ping()
            else:
                self._proxy_ping_tick = 0
                self._proxy_ping_text = ""
                live.setText("Status: Off")
                live.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 700;")
        if not st["enabled"]:
            self._proxy_status_lbl.setText("Status: Off")
            self._proxy_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            self._proxy_rotate_btn.setEnabled(False)
            return
        text = f"Status: On  ·  {st['current']}"
        if st["count"] > 1:
            text += f"  ({st['position']}/{st['count']})"
        nxt = st["next_rotation_seconds"]
        if nxt is not None:
            text += f"  ·  next in {nxt // 60}:{nxt % 60:02d}"
        self._proxy_status_lbl.setText(text)
        self._proxy_status_lbl.setStyleSheet(f"color: {ACCENT_BRIGHT}; font-size: 11px;")
        self._proxy_rotate_btn.setEnabled(st["count"] > 1)

    def _build_multi_roblox_panel(self) -> QFrame: # Multi Roblox
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        ttl = QLabel("Multi Roblox")
        ttl.setObjectName("sectionTitle")
        hdr.addWidget(ttl)
        hdr.addStretch(1)
        self._mr_status_lbl = QLabel("Status: Disabled")
        self._mr_status_lbl.setStyleSheet("color: #EF5350; font-size: 11px;")
        hdr.addWidget(self._mr_status_lbl)
        lay.addLayout(hdr)

        desc = QLabel(
            "Allows multiple Roblox instances to run simultaneously. "
            "Choose Default (mutex) or Handle64 (Sysinternals) method."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(desc)

        # Enable checkbox
        self._mr_enabled_chk = QCheckBox("Enable Multi Roblox")
        self._mr_enabled_chk.setStyleSheet(f"QCheckBox {{ color: {TEXT}; font-size: 12px; font-weight: 700; }}")
        self._mr_enabled_chk.stateChanged.connect(self._on_mr_enabled_changed)
        lay.addWidget(self._mr_enabled_chk)

        # Settings form
        form = QVBoxLayout()
        form.setContentsMargins(0, 8, 0, 0)
        form.setSpacing(10)

        # Method row
        row_method = QHBoxLayout()
        row_method.setSpacing(8)
        lbl_method = QLabel("Method:")
        lbl_method.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        row_method.addWidget(lbl_method)

        self._mr_default_radio = QRadioButton("Default")
        self._mr_default_radio.setStyleSheet(f"QRadioButton {{ color: {TEXT}; font-size: 11px; }}")
        self._mr_default_radio.setToolTip(
            "Uses a Windows mutex (ROBLOX_singletonEvent) to allow multiple\n"
            "Roblox instances. Works without administrator rights.\n"
            "Also applies the Error 773 (cookie lock) fix automatically."
        )
        self._mr_default_radio.toggled.connect(self._on_mr_method_changed)
        row_method.addWidget(self._mr_default_radio)

        self._mr_handle64_radio = QRadioButton("Handle64")
        self._mr_handle64_radio.setStyleSheet(f"QRadioButton {{ color: {TEXT}; font-size: 11px; }}")
        self._mr_handle64_radio.setToolTip(
            "Uses handle64.exe (Sysinternals) to close the singleton handle\n"
            "in each Roblox process as it launches. More reliable but\n"
            "REQUIRES administrator privileges and handle64.exe to be present."
        )
        self._mr_handle64_radio.toggled.connect(self._on_mr_method_changed)
        row_method.addWidget(self._mr_handle64_radio)
        row_method.addStretch(1)
        form.addLayout(row_method)

        # Handle64 status row
        row_h64 = QHBoxLayout()
        row_h64.setSpacing(8)
        lbl_h64 = QLabel("Handle64:")
        lbl_h64.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        row_h64.addWidget(lbl_h64)

        self._mr_h64_status_lbl = QLabel()
        self._mr_h64_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        row_h64.addWidget(self._mr_h64_status_lbl)

        self._mr_dl_btn = QPushButton("Download Handle64")
        self._mr_dl_btn.setToolTip(
            "Downloads handle64.exe from Sysinternals (Microsoft)\n"
            "and saves it to the AccountManagerData folder."
        )
        self._mr_dl_btn.setFixedHeight(24)
        self._mr_dl_btn.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE}; "
            f"padding: 2px 8px; font-size: 11px; color: {TEXT}; border-radius: 3px; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:disabled {{ color: {MUTED}; }}"
        )
        self._mr_dl_btn.clicked.connect(self._on_mr_download_handle64)
        row_h64.addWidget(self._mr_dl_btn)
        row_h64.addStretch(1)
        form.addLayout(row_h64)

        lay.addLayout(form)

        # Batch queue
        q_title = QLabel("Batch Queue")
        q_title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 700; margin-top: 14px;")
        lay.addWidget(q_title)
        q_desc = QLabel(
            "Launch big selections a few at a time. Select all the accounts you want and press "
            "Join / Launch: only the first batch opens. As soon as all of its Roblox windows are "
            "closed (Kill All Roblox, or closed any other way) the next batch opens by itself, "
            "until every account has had its turn. Pause stops it from opening the next one."
        )
        q_desc.setWordWrap(True)
        q_desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(q_desc)

        self._queue_enabled_chk = QCheckBox("Enable Batch Queue")
        self._queue_enabled_chk.setChecked(bool(actions.get_ui_setting("mr_batch_enabled", False)))
        self._queue_enabled_chk.toggled.connect(
            lambda on: actions.save_ui_setting("mr_batch_enabled", bool(on))
        )
        lay.addWidget(self._queue_enabled_chk)

        q_row = QHBoxLayout()
        q_row.setSpacing(8)
        q_lbl = QLabel("Accounts per batch:")
        q_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; min-width: 80px;")
        q_row.addWidget(q_lbl)
        self._queue_size_edit = QLineEdit(str(int(actions.get_ui_setting("mr_batch_size", 5) or 5)))
        self._queue_size_edit.setValidator(QIntValidator(1, 500, self._queue_size_edit))
        self._queue_size_edit.setFixedSize(70, 28)
        self._queue_size_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._queue_size_edit.setPlaceholderText("5")
        self._queue_size_edit.setToolTip("Type how many accounts open per batch")
        self._queue_size_edit.textChanged.connect(self._on_queue_size_changed)
        q_row.addWidget(self._queue_size_edit)
        q_row.addSpacing(16)
        retry_lbl = QLabel("Retries:")
        retry_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        retry_lbl.setToolTip("How many more times an account that didn't open (installer error, network)\n"
                             "is tried again at the end of the queue. Security checks are never retried.")
        q_row.addWidget(retry_lbl)
        self._queue_retry_edit = QLineEdit(str(self._queue_retry_limit()))
        self._queue_retry_edit.setValidator(QIntValidator(0, 5, self._queue_retry_edit))
        self._queue_retry_edit.setFixedSize(50, 28)
        self._queue_retry_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._queue_retry_edit.setToolTip(retry_lbl.toolTip())
        self._queue_retry_edit.textChanged.connect(
            lambda text: text.isdigit() and actions.save_ui_setting(self.QUEUE_RETRY_SETTING, int(text))
        )
        q_row.addWidget(self._queue_retry_edit)
        q_row.addStretch(1)
        lay.addLayout(q_row)

        q_btns = QHBoxLayout()
        q_btn_style = (
            f"QPushButton {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 12px; border-radius: 0; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
            f"QPushButton:disabled {{ color: {MUTED}; }}"
        )
        self._queue_next_btn = QPushButton("Next Batch")
        self._queue_next_btn.setToolTip("Close running Roblox now and open the next batch (even when paused)")
        self._queue_next_btn.clicked.connect(self._on_queue_next_clicked)
        self._queue_pause_btn = QPushButton("Pause")
        self._queue_pause_btn.setToolTip(
            "While paused, closing Roblox will NOT open the next batch.\nPress Resume to continue."
        )
        self._queue_pause_btn.clicked.connect(self._on_queue_pause_clicked)
        self._queue_stop_btn = QPushButton("Stop Queue")
        self._queue_stop_btn.clicked.connect(self._stop_launch_queue)
        for b in (self._queue_next_btn, self._queue_pause_btn, self._queue_stop_btn):
            b.setFixedHeight(26)
            b.setStyleSheet(q_btn_style)
            q_btns.addWidget(b)
        q_btns.addStretch(1)
        lay.addLayout(q_btns)

        self._queue_status_lbl = QLabel("Queue: empty")
        self._queue_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        lay.addWidget(self._queue_status_lbl)
        lay.addStretch(1)

        self._launch_queue = None
        self._update_queue_status()

        # Load saved state
        self._load_mr_settings()
        return panel

    # Batch queue -----------------------------------------------------------
    # A queue moves through: "starting" (batch sent, waiting for its clients to
    # appear) -> "running" (clients seen) -> all clients gone -> next batch, or
    # "waiting" when paused. A poll every 2 seconds counts Roblox clients on a
    # worker thread, so the queue advances however the windows were closed.
    _QUEUE_POLL_MS = 2000
    _QUEUE_START_TIMEOUT = 180.0

    def _queue_size(self) -> int:
        try:
            return max(1, int(self._queue_size_edit.text() or 0))
        except (ValueError, AttributeError):
            return 5

    def _on_queue_size_changed(self, text: str) -> None:
        try:
            value = int(text)
        except ValueError:
            return
        if value >= 1:
            actions.save_ui_setting("mr_batch_size", value)

    def _queue_or_all(self, usernames: list[str], send_batch) -> list[str] | None:
        """Start a queue when it is enabled and the selection is bigger than a batch.

        Returns the accounts to launch now, or the full list when no queue applies.
        """
        if not getattr(self, "_queue_enabled_chk", None) or not self._queue_enabled_chk.isChecked():
            return usernames
        size = self._queue_size()
        if len(usernames) <= size:
            return usernames
        batches = (len(usernames) + size - 1) // size
        self._launch_queue = {
            "remaining": list(usernames[size:]),
            "send": send_batch,
            "size": size,
            "batch": 1,
            "batches": batches,
            "total": len(usernames),
            "paused": False,
            "force": False,
            "phase": "starting",
            "phase_since": time.monotonic(),
            "empty_polls": 0,
            "failed": [],
            "security": [],
            "attempts": {},
            "ever_failed": set(),
            "cooldown_until": 0.0,
            "advancing": False,
            "pause_reason": "",
        }
        print(f"[INFO] Batch queue: {len(usernames)} accounts in {batches} batches of {size}. Starting batch 1.")
        self._ensure_queue_timer()
        self._update_queue_status()
        return list(usernames[:size])

    def _ensure_queue_timer(self) -> None:
        timer = getattr(self, "_queue_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(self._QUEUE_POLL_MS)
            timer.timeout.connect(self._poll_queue)
            self._queue_timer = timer
            self._bridge.queue_poll_done.connect(self._on_queue_poll)
        if not timer.isActive():
            timer.start()

    def _poll_queue(self) -> None:
        if not getattr(self, "_launch_queue", None):
            self._queue_timer.stop()
            return
        if getattr(self, "_queue_polling", False):
            return
        self._queue_polling = True

        manager = getattr(self, "manager", None)

        def _worker():
            try:
                keep = actions.locked_roblox_pids(manager, max_probes=2) if manager is not None else set()
                closed = actions.close_roblox_error_windows(keep)
                for title in closed:
                    print(f"[WARNING] Batch queue: closed a Roblox error window ({title}). "
                          "That account will be retried.")
                pids = set(actions.presence_mod.get_roblox_processes(max_age=0.0 if closed else 1.0))
                count = len(pids - keep)  # locked windows stay open
            except Exception:
                count = -1
            try:
                self._bridge.queue_poll_done.emit(count)
            except RuntimeError:
                pass

        threading.Thread(target=_worker, daemon=True, name="QueuePoll").start()

    def _on_queue_poll(self, count: int) -> None:
        self._queue_polling = False
        q = getattr(self, "_launch_queue", None)
        if not q or count < 0:
            return
        now = time.monotonic()
        if q["phase"] == "starting":
            if count > 0:
                q["phase"], q["phase_since"], q["empty_polls"] = "running", now, 0
                self._update_queue_status()
            elif now - q["phase_since"] > self._QUEUE_START_TIMEOUT:
                # The queue never pauses itself: move on to the next batch.
                q["phase"], q["phase_since"] = "waiting", now
                print(f"[WARNING] Batch queue: batch {q['batch']} did not start within "
                      f"{self._QUEUE_START_TIMEOUT:.0f}s. Moving on to the next batch.")
                self._update_queue_status()
            else:
                return
        if q["phase"] == "running":
            # Two empty polls in a row: avoids reacting to a client restarting.
            q["empty_polls"] = q["empty_polls"] + 1 if count == 0 else 0
            if q["empty_polls"] < 2:
                return
            q["phase"], q["phase_since"] = "waiting", now
            print(f"[INFO] Batch queue: batch {q['batch']} closed.")
            self._update_queue_status()
        if q["phase"] == "waiting" and count == 0:
            if now < q.get("cooldown_until", 0.0) and not q["force"]:
                self._update_queue_status()
                return
            if not q["remaining"]:
                self._finish_queue()
            elif not q["paused"] or q["force"]:
                self._launch_next_batch()

    def _launch_next_batch(self) -> None:
        q = getattr(self, "_launch_queue", None)
        if not q or not q["remaining"]:
            self._finish_queue()
            return
        batch = q["remaining"][: q["size"]]
        q["remaining"] = q["remaining"][q["size"]:]
        q["batch"] += 1
        q["batches"] = max(q["batches"], q["batch"] + -(-len(q["remaining"]) // q["size"]))
        q["force"] = False
        q["advancing"] = False
        q["pause_reason"] = ""
        q["cooldown_until"] = 0.0
        q["phase"], q["phase_since"], q["empty_polls"] = "starting", time.monotonic(), 0
        print(f"[INFO] Batch queue: starting batch {q['batch']}/{q['batches']} ({len(batch)} accounts)")
        try:
            q["send"](batch)
        except Exception as exc:
            print(f"[ERROR] Batch queue could not launch the next batch: {exc}")
        self._update_queue_status()

    def _finish_queue(self) -> None:
        q = getattr(self, "_launch_queue", None)
        if q:
            print("[INFO] Batch queue finished: every account has had its turn.")
        self._launch_queue = None
        self._update_queue_status()
        show = getattr(self, "_show_queue_summary", None)
        if q and callable(show) and actions.load_ui_settings().get(QUEUE_SELECT_FAILED_SETTING, True):
            QTimer.singleShot(0, lambda: show(q))

    def _on_queue_next_clicked(self) -> None:
        q = getattr(self, "_launch_queue", None)
        if not q or q.get("advancing"):
            return  # one press = one batch
        q["advancing"] = True
        q["force"] = True
        if q["phase"] != "waiting":
            q["phase"], q["phase_since"] = "waiting", time.monotonic()
        self._on_kill_all_roblox()

    def _on_queue_pause_clicked(self) -> None:
        q = getattr(self, "_launch_queue", None)
        if not q:
            return
        q["paused"] = not q["paused"]
        print(f"[INFO] Batch queue {'paused' if q['paused'] else 'resumed'}.")
        self._update_queue_status()
        if not q["paused"]:
            self._poll_queue()  # resume right away if everything is already closed

    def _stop_launch_queue(self) -> None:
        if getattr(self, "_launch_queue", None):
            print("[INFO] Batch queue stopped.")
        self._launch_queue = None
        self._update_queue_status()

    def _update_queue_status(self) -> None:
        q = getattr(self, "_launch_queue", None)
        label = getattr(self, "_queue_status_lbl", None)
        nav = getattr(self, "_nav_queue_lbl", None)
        if q:
            waiting = len(q["remaining"])
            left = f"{waiting} account{'s' if waiting != 1 else ''} waiting"
            if q.get("advancing"):
                state = f"opening batch {q['batch'] + 1}…"
            elif q["phase"] == "starting":
                state = "starting"
            elif q["phase"] == "running":
                state = "running"
            elif q["paused"]:
                state = "PAUSED (press Resume)"
                if q.get("pause_reason"):
                    state += f" · {q['pause_reason']}"
            elif q.get("cooldown_until", 0.0) > time.monotonic():
                state = f"waiting {max(1, int(q['cooldown_until'] - time.monotonic()))}s before the next batch"
            else:
                state = "closed, opening next…"
            if q["paused"] and q["phase"] != "waiting":
                state += " · paused"
            text = f"Batch {q['batch']}/{q['batches']} {state} · {left}"
            short = f"Batch {q['batch']}/{q['batches']}" + (" ⏸" if q["paused"] else "")
        else:
            text, short = "Queue: empty", ""
        if label is not None and isValid(label):
            label.setText(text)
            color = "#e0a85c" if q and q["paused"] else (ACCENT_BRIGHT if q else MUTED)
            label.setStyleSheet(f"color: {color}; font-size: 11px;")
            self._queue_next_btn.setEnabled(bool(q and q["remaining"] and not q.get("advancing")))
            self._queue_pause_btn.setEnabled(bool(q))
            self._queue_pause_btn.setText("Resume" if q and q["paused"] else "Pause")
            self._queue_stop_btn.setEnabled(bool(q))
        if nav is not None and isValid(nav):
            nav.setText(short)
            nav.setVisible(bool(q))

    def _is_admin(self) -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _load_mr_settings(self):
        saved = actions.load_ui_settings()
        self._mr_method = saved.get("multi_roblox_method", "default")
        self._mr_enabled = saved.get("multi_roblox_enabled", False)

        handle64_available = bool(actions.find_handle64())
        self._mr_handle64_radio.setEnabled(handle64_available)

        self._mr_handle64_radio.blockSignals(True)
        self._mr_default_radio.blockSignals(True)
        if self._mr_method == "handle64" and handle64_available:
            self._mr_handle64_radio.setChecked(True)
        else:
            if self._mr_method == "handle64" and not handle64_available:
                self._mr_method = "default"
                actions.save_ui_setting("multi_roblox_method", "default")
            self._mr_default_radio.setChecked(True)
        self._mr_handle64_radio.blockSignals(False)
        self._mr_default_radio.blockSignals(False)

        self._mr_enabled_chk.blockSignals(True)
        self._mr_enabled_chk.setChecked(self._mr_enabled)
        self._mr_enabled_chk.blockSignals(False)
        self._update_mr_h64_status()
        self._update_mr_status()

    def _restore_multi_roblox(self) -> None:
        # Called after the splash finishes and app.exec() starts.
        if self._mr_enabled:
            self._start_multi_roblox()

    def _update_mr_h64_status(self):
        path = actions.find_handle64()
        if path:
            self._mr_h64_status_lbl.setText("[handle64 found]")
            self._mr_h64_status_lbl.setStyleSheet("color: #4CAF50; font-size: 10px;")
            self._mr_handle64_radio.setEnabled(True)
            self._mr_dl_btn.setText("Downloaded")
            self._mr_dl_btn.setEnabled(False)
        else:
            self._mr_h64_status_lbl.setText("[handle64 not found]")
            self._mr_h64_status_lbl.setStyleSheet("color: #EF5350; font-size: 10px;")
            self._mr_handle64_radio.setEnabled(False)
            self._mr_dl_btn.setText("Download Handle64")
            self._mr_dl_btn.setEnabled(True)

    def _on_mr_method_changed(self, checked: bool = False):
        if not checked:
            return
        if self._mr_handle64_radio.isChecked():
            if not self._is_admin():
                self._mr_method = "handle64"
                actions.save_ui_setting("multi_roblox_method", self._mr_method)
                if self._mr_ask_restart_as_admin():
                    return
                self._mr_default_radio.blockSignals(True)
                self._mr_handle64_radio.blockSignals(True)
                self._mr_default_radio.setChecked(True)
                self._mr_default_radio.blockSignals(False)
                self._mr_handle64_radio.blockSignals(False)
                self._mr_method = "default"
                actions.save_ui_setting("multi_roblox_method", self._mr_method)
                if self._mr_enabled:
                    self._stop_multi_roblox()
                    self._start_multi_roblox()
                return
            self._mr_method = "handle64"
        else:
            self._mr_method = "default"
        actions.save_ui_setting("multi_roblox_method", self._mr_method)
        if self._mr_enabled:
            self._stop_multi_roblox()
            self._start_multi_roblox()
        else:
            self._update_mr_status()

    def _on_mr_enabled_changed(self, state):
        try:
            self._mr_enabled = (state == Qt.CheckState.Checked.value)
            actions.save_ui_setting("multi_roblox_enabled", self._mr_enabled)

            if self._mr_enabled:
                QTimer.singleShot(0, self._start_multi_roblox)
            else:
                self._stop_multi_roblox()

            self._update_mr_status()
            
        except Exception as e:
            print(f"Error in _on_mr_enabled_changed: {e}")

    def _start_multi_roblox(self):
        if getattr(self, "_admin_restart_pending", False):
            return
        if actions.is_multi_roblox_running(self._mr_method):
            self._update_mr_status()
            return

        if self._mr_method == "default":
            roblox_running = actions.is_roblox_running()
            if roblox_running:
                reply = QMessageBox.question(
                    self,
                    "Multi Roblox",
                    "Roblox is currently running.\n"
                    "Multi Roblox (default) must be enabled before Roblox starts.\n"
                    "Do you want to close all Roblox processes now (including locked accounts)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    actions.kill_all_roblox_fast()
                    self._wait_for_roblox_exit(3.0, self._finish_start_multi_roblox)
                    return
                else:
                    self._mr_enabled = False
                    self._mr_enabled_chk.blockSignals(True)
                    self._mr_enabled_chk.setChecked(False)
                    self._mr_enabled_chk.blockSignals(False)
                    actions.save_ui_setting("multi_roblox_enabled", False)
                    self._update_mr_status() 
                    return

        self._finish_start_multi_roblox()

    def _wait_for_roblox_exit(self, timeout: float, on_done) -> None:
        deadline = time.monotonic() + timeout
        timer = QTimer(self)
        timer.setInterval(200)

        def _check() -> None:
            if actions.is_roblox_running() and time.monotonic() < deadline:
                return
            timer.stop()
            timer.deleteLater()
            on_done()

        timer.timeout.connect(_check)
        timer.start()

    def _ask_close_lock_holders(self) -> bool:
        """Say which program holds Roblox's login file; True if the user closed it."""
        holders = list(actions.mr_lock_holders)
        others = [name for _pid, name in holders if file_holders_mod.is_this_app(name)]
        if not holders:
            text = ("Multi Roblox needs to lock Roblox's login file, but another program is using it.\n"
                    "Close Roblox, Roblox Studio and Bloxstrap-type launchers, then try again.")
        elif others:
            text = ("Another copy of ANAne RAM is still running and holding Roblox's login file "
                    "(maybe left over from an update).")
        else:
            names = "\n".join(f"• {name} (PID {pid})" for pid, name in holders)
            text = f"Multi Roblox needs to lock Roblox's login file, but this is using it:\n{names}"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Multi Roblox")
        box.setText(text)
        retry = box.addButton("Close it and retry", QMessageBox.ButtonRole.AcceptRole) if holders else None
        box.addButton("Cancel" if holders else "OK", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if retry is not None and box.clickedButton() is retry:
            return actions.close_lock_holders() > 0
        return False

    def _finish_start_multi_roblox(self) -> None:
        ok, msg = actions.enable_multi_roblox(self._mr_method)
        if not ok:
            if msg == "NEEDS_ADMIN":
                if self._mr_ask_restart_as_admin():
                    return
            elif msg == "ROBLOX_RUNNING":
                QMessageBox.critical(
                    self,
                    "Multi Roblox",
                    "Roblox is still running. Close all Roblox clients before enabling Multi Roblox.",
                )
            elif msg == "ROBLOX_LAUNCH_IN_PROGRESS":
                QMessageBox.critical(
                    self,
                    "Multi Roblox",
                    "A Roblox launch is still in progress. Wait for it to finish, "
                    "then try enabling Multi Roblox again.",
                )
            elif msg == "HANDLE64_NOT_FOUND":
                QMessageBox.critical(
                    self,
                    "Multi Roblox",
                    "Handle64 was not found. Download Handle64 before enabling this mode.",
                )
            elif msg == "ROBLOX_COOKIES_LOCK_FAILED":
                if self._ask_close_lock_holders():
                    QTimer.singleShot(0, self._finish_start_multi_roblox)  # try again
                    return
            elif msg == "HANDLE64_UNVERIFIED":
                QMessageBox.critical(
                    self,
                    "Multi Roblox",
                    "The Handle64 file is not signed by Microsoft, so it was not run "
                    "with administrator rights. Delete it and download Handle64 again.",
                )
            elif msg == "MUTEX_CREATE_FAILED":
                QMessageBox.critical(
                    self,
                    "Multi Roblox",
                    "Windows could not create the Roblox singleton mutex. "
                    "Check the console for the Windows error code.",
                )
            else:
                QMessageBox.critical(
                    self,
                    "Multi Roblox",
                    f"Multi Roblox could not be started.\n\n{msg}",
                )
            self._mr_enabled = False
            self._mr_enabled_chk.blockSignals(True)
            self._mr_enabled_chk.setChecked(False)
            self._mr_enabled_chk.blockSignals(False)
            actions.save_ui_setting("multi_roblox_enabled", False)
            self._update_mr_status()
        else:
            self._update_mr_status()

    def _mr_ask_restart_as_admin(self) -> bool:
        if self._admin_restart_pending:
            return True
        reply = QMessageBox.question(
            self,
            "Administrator Required",
            "The program is not running as administrator.\n\n"
            "Handle64 mode requires administrator privileges.\n\n"
            "Do you want to relaunch the app as administrator?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                if getattr(sys, "frozen", False):
                    arguments = sys.argv[1:]
                else:
                    arguments = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
                params = subprocess.list2cmdline([*arguments, "--data-dir", get_data_dir()])
                launch = ctypes.windll.shell32.ShellExecuteW
                launch.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
                launch.restype = ctypes.c_void_p
                result = launch(int(self.winId()), "runas", sys.executable, params, os.getcwd(), 1)
                if not result or result <= 32:
                    raise OSError(f"Windows did not start the elevated app (code {result or 0}).")
                self._admin_restart_pending = True
                self.hide()
                QApplication.quit()
                return True
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to restart as administrator:\n{e}")
        return False

    def _stop_multi_roblox(self):
        actions.disable_multi_roblox()
        self._update_mr_status()

    def _update_mr_status(self):
        method_str = "Handle64" if getattr(self, "_mr_method", "default") == "handle64" else "Default"
        running = actions.is_multi_roblox_running(getattr(self, "_mr_method", "default"))
        if running:
            self._mr_status_lbl.setText(f"Status: Running ({method_str})")
            self._mr_status_lbl.setStyleSheet("color: #4CAF50; font-size: 11px;")
        else:
            self._mr_status_lbl.setText("Status: Disabled")
            self._mr_status_lbl.setStyleSheet("color: #EF5350; font-size: 11px;")

    def _on_mr_download_handle64(self):
        self._mr_dl_btn.setText("Downloading...")
        self._mr_dl_btn.setEnabled(False)

        def _thread():
            ok = actions.download_handle64()
            self._bridge.mr_download_done.emit(ok)

        threading.Thread(target=_thread, daemon=True).start()

    def _build_settings_panel(self) -> QFrame: # Settings panel
        panel = QFrame()
        panel.setObjectName("centerPanel")

        root_lay = QHBoxLayout(panel)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Left category list
        cat_panel = QFrame()
        cat_panel.setObjectName('settingsNavSurface')
        cat_panel.setFixedWidth(120)
        cat_panel.setStyleSheet(
            f"QFrame {{ background: transparent; border: 0; border-right: 1px solid {LINE}; }}"
        )
        cat_lay = QVBoxLayout(cat_panel)
        cat_lay.setContentsMargins(0, 10, 0, 10)
        cat_lay.setSpacing(2)

        cat_header = QLabel("Settings")
        cat_header.setStyleSheet(
            f"color: {MUTED}; font-size: 9px; font-weight: 700; "
            f"letter-spacing: 0.5px; padding: 0 10px 6px 10px;"
        )
        cat_lay.addWidget(cat_header)

        # Right stacked content
        content_stack = QStackedWidget()
        content_stack.setStyleSheet("background: transparent;")

        CATEGORIES = ["General", "Roblox", "Themes", "Misc", "Proxy", "Developer"]
        cat_buttons: list[QPushButton] = []

        def _switch_cat(idx: int):
            content_stack.setCurrentIndex(idx)
            motion.slide_fade_in(content_stack.currentWidget(), offset=10, enabled=self._animations_on)
            for i, b in enumerate(cat_buttons):
                b.setChecked(i == idx)

        self._settings_switch_cat = _switch_cat
        for i, name in enumerate(CATEGORIES):
            btn = QPushButton(name)
            btn.setObjectName("navTab")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda _=False, idx=i: _switch_cat(idx))
            cat_lay.addWidget(btn)
            cat_buttons.append(btn)

        cat_lay.addStretch(1)

        root_lay.addWidget(cat_panel)
        root_lay.addWidget(content_stack, 1)

        # Shared helpers
        def _scrollable() -> tuple[QScrollArea, QVBoxLayout]:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.Shape.NoFrame)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sa.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            w = QWidget()
            w.setStyleSheet("background: transparent;")
            # A fixed-width column keeps each value box next to its label
            # instead of at the far edge of a wide window.
            w.setMaximumWidth(560)
            lay = QVBoxLayout(w)
            lay.setContentsMargins(16, 14, 16, 14)
            lay.setSpacing(6)
            sa.setWidget(w)
            sa.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            return sa, lay

        S = actions.load_ui_settings() # Snapshot for initial values

        def _chk(key, label: str, tip: str, default=False,
                 on_change=None) -> QCheckBox:
            cb = QCheckBox(label)
            cb.setChecked(S.get(key, default) if key is not None else False)
            cb.setToolTip(tip)
            def _h(state):
                val = (state == Qt.CheckState.Checked.value)
                if key is not None:
                    actions.save_ui_setting(key, val)
                if on_change:
                    on_change(val)
            cb.stateChanged.connect(_h)
            return cb

        def _sec(title: str) -> QLabel:
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"color: {MUTED}; font-size: 9px; font-weight: 700; "
                f"letter-spacing: 0.5px; margin-top: 8px;"
            )
            return lbl

        def _sub_indent(widget, px=18):
            row = QHBoxLayout()
            row.setContentsMargins(px, 0, 0, 0)
            row.addWidget(widget)
            return row

        # General (Page 0)
        sa, f = _scrollable()
        content_stack.addWidget(sa)

        f.addWidget(_sec("WINDOW"))
        self._sett_topmost_chk = _chk(
            "enable_topmost", "Always on Top",
            "Keep this window above all other windows.\n"
            "Useful when managing accounts alongside other apps.",
            on_change=self._on_sett_topmost,
        )
        f.addWidget(self._sett_topmost_chk)

        self._sett_tray_chk = _chk(
            "hide_to_system_tray", "Hide to System Tray",
            "Keep ANAne RAM running in the system tray when the main window is closed.\n"
            "Use the tray icon to show the window again or exit the application.",
            on_change=self._on_sett_tray,
        )
        f.addWidget(self._sett_tray_chk)

        self._sett_animations_chk = _chk(
            "ui_animations", "Smooth Animations",
            "Fade between pages and highlight buttons when you point at or click them.\n"
            "Turned off automatically when Windows animations are off. Takes effect on the next start.",
            default=True,
        )
        f.addWidget(self._sett_animations_chk)

        self._sett_splash_chk = _chk(
            "startup_splash", "Show Loading Screen",
            "Show a small loading screen while the application starts.\n"
            "Takes effect on the next start.",
            default=True,
        )
        f.addWidget(self._sett_splash_chk)

        f.addWidget(_sec("LAUNCH"))
        self._sett_confirm_chk = _chk(
            "confirm_before_launch", "Confirm Before Launch",
            "Show a confirmation prompt before any Roblox join/launch action.\n"
            "Prevents accidental launches.",
        )
        f.addWidget(self._sett_confirm_chk)

        launch_delay_row = QHBoxLayout()
        launch_delay_row.setContentsMargins(0, 0, 0, 0)
        launch_delay_label = QLabel("Launch Delay")
        launch_delay_label.setToolTip(
            "Wait this long between accounts during bulk launches.\n"
            "Set to 0 to disable the additional delay."
        )
        launch_delay_row.addWidget(launch_delay_label)
        launch_delay_row.addStretch(1)
        self._sett_launch_delay_spin = QDoubleSpinBox()
        self._sett_launch_delay_spin.setRange(0.0, 300.0)
        self._sett_launch_delay_spin.setDecimals(1)
        self._sett_launch_delay_spin.setSingleStep(0.5)
        self._sett_launch_delay_spin.setValue(
            actions.get_launch_delay_seconds(S)
        )
        self._sett_launch_delay_spin.setSuffix(" s")
        self._sett_launch_delay_spin.setFixedWidth(80)
        self._sett_launch_delay_spin.setButtonSymbols(
            QDoubleSpinBox.ButtonSymbols.NoButtons
        )
        self._sett_launch_delay_spin.valueChanged.connect(
            lambda value: actions.save_ui_setting(
                "launch_delay_seconds",
                round(float(value), 1),
            )
        )
        launch_delay_row.addWidget(self._sett_launch_delay_spin)
        f.addLayout(launch_delay_row)

        f.addWidget(_sec("ACCOUNTS LIST"))
        self._sett_multisel_chk = _chk(
            "enable_multi_select", "Multi-Select (Ctrl / Shift + Click)",
            "Allow selecting multiple accounts simultaneously.\n"
            "Enables batch join, launch, and removal.",
            on_change=self._on_sett_multi_select,
        )
        f.addWidget(self._sett_multisel_chk)

        self._sett_cookie_check_chk = _chk(
            "validate_cookies_on_startup", "Check Cookies on Startup",
            "Check every saved account cookie with Roblox shortly after the app starts.\n"
            "Off by default: cookies are only checked when you press the button on the\n"
            "Account Health page. Takes effect on the next start.",
            default=False,
        )
        f.addWidget(self._sett_cookie_check_chk)

        cookie_delay_row = QHBoxLayout()
        cookie_delay_row.setContentsMargins(0, 0, 0, 0)
        cookie_delay_label = QLabel("Cookie Check Delay")
        cookie_delay_label.setToolTip(
            "Wait this long between accounts while checking cookies.\n"
            "Raise it if Roblox rate limits the check. Takes effect on the next start."
        )
        cookie_delay_row.addWidget(cookie_delay_label)
        cookie_delay_row.addStretch(1)
        self._sett_cookie_delay_spin = QDoubleSpinBox()
        self._sett_cookie_delay_spin.setRange(
            self._cv_mod.MIN_DELAY, self._cv_mod.MAX_DELAY
        )
        self._sett_cookie_delay_spin.setDecimals(1)
        self._sett_cookie_delay_spin.setSingleStep(0.5)
        self._sett_cookie_delay_spin.setValue(self._cv_mod.get_validation_delay(S))
        self._sett_cookie_delay_spin.setSuffix(" s")
        self._sett_cookie_delay_spin.setFixedWidth(80)
        self._sett_cookie_delay_spin.setButtonSymbols(
            QDoubleSpinBox.ButtonSymbols.NoButtons
        )
        self._sett_cookie_delay_spin.valueChanged.connect(
            lambda value: actions.save_ui_setting(
                "cookie_validation_delay_seconds",
                round(float(value), 1),
            )
        )
        cookie_delay_row.addWidget(self._sett_cookie_delay_spin)
        f.addLayout(cookie_delay_row)

        import_browsers_row = QHBoxLayout()
        import_browsers_row.setContentsMargins(0, 0, 0, 0)
        import_browsers_label = QLabel("Import Browsers at Once")
        import_browsers_label.setToolTip(
            "How many browser windows User:Pass import opens together.\n"
            "Lower it on slower computers or if logins fail when many windows open."
        )
        import_browsers_row.addWidget(import_browsers_label)
        import_browsers_row.addStretch(1)
        self._sett_import_browsers_spin = QSpinBox()
        self._sett_import_browsers_spin.setRange(
            actions.IMPORT_BATCH_MIN, actions.IMPORT_BATCH_MAX
        )
        self._sett_import_browsers_spin.setValue(actions.get_import_batch_size(S))
        self._sett_import_browsers_spin.setFixedWidth(80)
        self._sett_import_browsers_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_import_browsers_spin.valueChanged.connect(
            lambda value: actions.save_ui_setting(actions.IMPORT_BATCH_SETTING, int(value))
        )
        import_browsers_row.addWidget(self._sett_import_browsers_spin)
        f.addLayout(import_browsers_row)

        f.addWidget(_sec("SYSTEM"))
        # Updates are required, so the startup check can't be turned off.
        self._sett_update_now_btn = QPushButton("Check for Updates Now")
        self._sett_update_now_btn.setToolTip(
            "Look for a newer version on GitHub right now. The app checks on every start too."
        )
        self._sett_update_now_btn.clicked.connect(self._on_check_updates_now)
        f.addWidget(self._sett_update_now_btn)

        log_retention_row = QHBoxLayout()
        log_retention_row.setContentsMargins(0, 0, 0, 0)
        log_retention_label = QLabel("Log Files to Keep")
        log_retention_label.setToolTip(
            "Keep this many of the newest session, crash, hang and update logs.\n"
            "The oldest is deleted each time a new log file is made. Set to 0 to keep everything."
        )
        log_retention_row.addWidget(log_retention_label)
        log_retention_row.addStretch(1)
        self._sett_log_retention_spin = QSpinBox()
        self._sett_log_retention_spin.setRange(0, 1000)
        self._sett_log_retention_spin.setValue(diagnostics.get_log_retention())
        self._sett_log_retention_spin.setFixedWidth(80)
        self._sett_log_retention_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_log_retention_spin.valueChanged.connect(
            lambda value: actions.save_ui_setting("log_retention_count", int(value))
        )
        log_retention_row.addWidget(self._sett_log_retention_spin)
        f.addLayout(log_retention_row)

        avatar_cache_row = QHBoxLayout()
        avatar_cache_row.setContentsMargins(0, 0, 0, 0)
        avatar_cache_label = QLabel("Refresh Avatars After")
        avatar_cache_label.setToolTip(
            "Download an account's avatar again once the saved copy is this many days old.\n"
            "Set to 0 to keep saved avatars forever. Applies the next time avatars load."
        )
        avatar_cache_row.addWidget(avatar_cache_label)
        avatar_cache_row.addStretch(1)
        self._sett_avatar_cache_spin = QSpinBox()
        self._sett_avatar_cache_spin.setRange(0, 365)
        self._sett_avatar_cache_spin.setValue(avatars.get_cache_days())
        self._sett_avatar_cache_spin.setSuffix(" days")
        self._sett_avatar_cache_spin.setFixedWidth(80)
        self._sett_avatar_cache_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_avatar_cache_spin.valueChanged.connect(
            lambda value: actions.save_ui_setting(avatars.CACHE_DAYS_SETTING, int(value))
        )
        avatar_cache_row.addWidget(self._sett_avatar_cache_spin)
        f.addLayout(avatar_cache_row)

        # Start Menu shortcut
        self._sett_startmenu_chk = QCheckBox("Add to Start Menu")
        self._sett_startmenu_chk.setChecked(windows_startup_mod.is_start_menu_enabled())
        self._sett_startmenu_chk.setToolTip(
            "Create (or remove) a Start Menu shortcut for this application.\n"
            "Works with both the .exe build and the Python script."
        )
        self._sett_startmenu_chk.stateChanged.connect(self._on_sett_start_menu)
        f.addWidget(self._sett_startmenu_chk)

        _startup_enabled = windows_startup_mod.is_startup_enabled()
        self._sett_startup_chk = QCheckBox("Start with Windows")
        self._sett_startup_chk.setChecked(_startup_enabled)
        self._sett_startup_chk.setToolTip(
            "Start ANAne RAM automatically when you sign in to Windows.\n"
            "This creates a shortcut in your Windows Startup folder."
        )
        self._sett_startup_chk.stateChanged.connect(self._on_sett_startup)
        f.addWidget(self._sett_startup_chk)

        f.addWidget(_sec("RECENT GAMES"))
        mg_row = QHBoxLayout()
        mg_row.setContentsMargins(0, 0, 0, 0)
        mg_l = QLabel("Max Recent Games")
        mg_l.setToolTip(
            "How many recent games to keep in the history list.\n"
            "Older entries are dropped automatically."
        )
        mg_row.addWidget(mg_l)
        mg_row.addStretch(1)
        self._sett_mg_spin = QSpinBox()
        self._sett_mg_spin.setRange(5, 50)
        self._sett_mg_spin.setValue(actions.get_recent_games_limit(S))
        self._sett_mg_spin.setFixedWidth(64)
        self._sett_mg_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_mg_spin.valueChanged.connect(
            self._on_sett_max_recent_games
        )
        mg_row.addWidget(self._sett_mg_spin)
        f.addLayout(mg_row)

        f.addStretch(1)

        # Roblox (Page 1)
        sa, f = _scrollable()
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content_stack.addWidget(sa)

        f.addWidget(_sec("LAUNCH PROBLEMS"))
        _lp_lbl = QLabel("Only needed if some accounts often fail to open (for example Roblox's "
                         "\"Installer encountered a critical error\").")
        _lp_lbl.setWordWrap(True)
        _lp_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_lp_lbl)
        self._sett_queue_failed_chk = _chk(
            QUEUE_SELECT_FAILED_SETTING, "Select failed accounts when the queue finishes",
            "During a Batch Queue, remember every account that didn't open.\n"
            "When the queue is done they are selected in the Account List, ready for Join.\n"
            "The error popup is not shown after each batch while the queue runs.",
        )
        f.addWidget(self._sett_queue_failed_chk)
        self._sett_first_alone_chk = _chk(
            actions.FIRST_ALONE_SETTING, "Start one account first when launching all at once",
            "With Launch Delay 0, the first account opens alone and the rest start once it is ready.\n"
            "This lets a Roblox update finish first, so several installers don't crash each other.",
        )
        f.addWidget(self._sett_first_alone_chk)
        self._sett_install_guard_chk = _chk(
            install_guard_mod.SETTING, "Prevent Roblox installer errors while Roblox is open",
            "While a Roblox window is open, new accounts start with the Roblox version already\n"
            "running, so Roblox's updater can't crash with \"Installer encountered a critical error\".\n"
            "Roblox error boxes are closed automatically, and when a Roblox update is out the\n"
            "first account opens alone so the update installs before the rest.\n"
            "Applies to the Automatic (roblox://) launcher.",
            default=True,
        )
        f.addWidget(self._sett_install_guard_chk)

        self._build_window_size_section(f, _sec)

        f.addWidget(_sec("PERFORMANCE"))
        _moved_lbl = QLabel("FPS cap and graphics quality now have their own window: the Performance button on the Accounts page, under Recent games.")
        _moved_lbl.setWordWrap(True)
        _moved_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_moved_lbl)
        _moved_row = QHBoxLayout()
        _moved_btn = QPushButton("Open Performance")
        _moved_btn.clicked.connect(lambda: self._open_settings_sheet("performance"))
        _moved_row.addWidget(_moved_btn)
        _moved_row.addStretch(1)
        f.addLayout(_moved_row)

        f.addWidget(_sec("LAUNCHER"))
        _launcher_lbl = QLabel(
            "Choose how Roblox is launched when you join a game."
        )
        _launcher_lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        _launcher_lbl.setWordWrap(True)
        f.addWidget(_launcher_lbl)

        LAUNCHERS = [
            ("default",   "Automatic (roblox://)",
             "Launch via the standard roblox:// URI protocol (roblox-player)."),
            ("bloxstrap", "Bloxstrap",
             "Launch using Bloxstrap, an open-source Roblox bootstrapper with extra features."),
            ("fishstrap", "Fishstrap",
             "Launch using Fishstrap, a Bloxstrap fork."),
            ("froststrap", "Froststrap",
             "Launch using Froststrap."),
            ("client", "Roblox Client (direct .exe)",
             "Directly invoke the RobloxPlayerBeta.exe."),
            ("custom",    "Custom",
             "Specify a custom executable path to use as the launcher."),
        ]
        _cur_launcher = S.get("roblox_launcher", "default")
        _launcher_grp = QButtonGroup(sa)
        self._sett_launcher_radios: dict[str, QRadioButton] = {}

        for key, label, tip in LAUNCHERS:
            rb = QRadioButton(label)
            rb.setToolTip(tip)
            rb.setChecked(key == _cur_launcher)
            _launcher_grp.addButton(rb)
            self._sett_launcher_radios[key] = rb
            f.addWidget(rb)

        # Custom path row
        _custom_row = QHBoxLayout()
        _custom_row.setContentsMargins(20, 0, 0, 0)
        self._sett_custom_launcher_edit = QLineEdit()
        self._sett_custom_launcher_edit.setPlaceholderText("Path to launcher .exe ...")
        self._sett_custom_launcher_edit.setText(str(S.get("custom_roblox_launcher_path", "") or ""))
        self._sett_custom_launcher_edit.setEnabled(_cur_launcher == "custom")
        self._sett_custom_launcher_edit.editingFinished.connect(
            lambda: actions.save_ui_setting(
                "custom_roblox_launcher_path",
                self._sett_custom_launcher_edit.text(),
            )
        )
        _custom_row.addWidget(self._sett_custom_launcher_edit)
        _browse_btn = QPushButton("Browse")
        _browse_btn.setFixedWidth(60)
        _browse_btn.setEnabled(_cur_launcher == "custom")
        _browse_btn.clicked.connect(self._on_sett_browse_launcher)
        _custom_row.addWidget(_browse_btn)
        f.addLayout(_custom_row)
        self._sett_browse_btn = _browse_btn

        def _on_launcher_toggled(key):
            def _h(checked):
                if checked:
                    actions.save_ui_setting("roblox_launcher", key)
                    is_custom = (key == "custom")
                    self._sett_custom_launcher_edit.setEnabled(is_custom)
                    self._sett_browse_btn.setEnabled(is_custom)
            return _h

        for key, rb in self._sett_launcher_radios.items():
            rb.toggled.connect(_on_launcher_toggled(key))

        f.addWidget(_sec("WINDOWS"))
        self._sett_rename_chk = _chk(
            "rename_roblox_windows", "Rename Roblox to",
            "Set each Roblox window's title bar to the account username or saved note.",
            on_change=self._on_sett_rename_windows,
        )
        f.addWidget(self._sett_rename_chk)

        rename_mode_row = QHBoxLayout()
        rename_mode_row.setContentsMargins(20, 0, 0, 0)
        rename_mode_label = QLabel("Mode:")
        rename_mode_row.addWidget(rename_mode_label)
        self._sett_rename_mode_group = QButtonGroup(self)
        self._sett_rename_username_radio = QRadioButton("Username")
        self._sett_rename_note_radio = QRadioButton("Note")
        self._sett_rename_mode_group.addButton(self._sett_rename_username_radio)
        self._sett_rename_mode_group.addButton(self._sett_rename_note_radio)
        rename_mode = S.get("rename_roblox_windows_mode", "username")
        if rename_mode not in ("username", "note"):
            rename_mode = "username"
        self._sett_rename_username_radio.setChecked(rename_mode == "username")
        self._sett_rename_note_radio.setChecked(rename_mode == "note")
        self._sett_rename_username_radio.toggled.connect(
            lambda checked: self._on_sett_rename_mode("username", checked)
        )
        self._sett_rename_note_radio.toggled.connect(
            lambda checked: self._on_sett_rename_mode("note", checked)
        )
        rename_mode_row.addWidget(self._sett_rename_username_radio)
        rename_mode_row.addWidget(self._sett_rename_note_radio)
        rename_mode_row.addStretch(1)
        f.addLayout(rename_mode_row)
        self._update_rename_mode_controls()

        f.addWidget(_sec("RAM"))
        _ram_moved = QLabel("RAM Usage and RAM Optimization are in the Performance window "
                            "(Accounts page, under Recent games).")
        _ram_moved.setWordWrap(True)
        _ram_moved.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_ram_moved)
        _ram_row = QHBoxLayout()
        _ram_btn = QPushButton("Open Performance")
        _ram_btn.clicked.connect(lambda: self._open_settings_sheet("performance"))
        _ram_row.addWidget(_ram_btn)
        _ram_row.addStretch(1)
        f.addLayout(_ram_row)

        f.addWidget(_sec("SHORTCUT KEYS"))
        _moved_lbl = QLabel("The Window Grid, Kill All and Minimize / Restore shortcut keys now have their own window: the Hotkey button on the Accounts page, under Recent games.")
        _moved_lbl.setWordWrap(True)
        _moved_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_moved_lbl)
        _moved_row = QHBoxLayout()
        _moved_btn = QPushButton("Open Hotkey")
        _moved_btn.clicked.connect(lambda: self._open_settings_sheet("hotkey"))
        _moved_row.addWidget(_moved_btn)
        _moved_row.addStretch(1)
        f.addLayout(_moved_row)

        f.addWidget(_sec("FIXES"))
        self._sett_installer_fix_chk = _chk(
            "roblox_installer_fix", "Roblox Installer Fix",
            "Moves RobloxPlayerInstaller.exe out of each Roblox version folder\n"
            "on launch to stop the installer popup, then restores it on exit.",
            on_change=self._on_sett_installer_fix,
        )
        f.addWidget(self._sett_installer_fix_chk)

        f.addWidget(_sec("ROBLOX DOWNLOADER"))
        _roblox_downloader_desc = QLabel(
            "Download a Windows Roblox Player deployment directly from Roblox."
        )
        _roblox_downloader_desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        _roblox_downloader_desc.setWordWrap(True)
        f.addWidget(_roblox_downloader_desc)

        _roblox_downloader_custom = bool(
            S.get("roblox_downloader_customizations", False)
        )
        self._sett_roblox_downloader_custom_chk = _chk(
            "roblox_downloader_customizations",
            "Enable Customizations",
            "Enable a custom Roblox version hash and download location.\n"
            "When disabled, the latest LIVE version is downloaded to Roblox's "
            "default Versions folder.",
            on_change=self._on_sett_roblox_downloader_customizations,
        )
        f.addWidget(self._sett_roblox_downloader_custom_chk)

        roblox_version_row = QHBoxLayout()
        roblox_version_row.setContentsMargins(20, 0, 0, 0)
        roblox_version_label = QLabel("Version")
        roblox_version_label.setFixedWidth(78)
        roblox_version_label.setToolTip(
            "Use LIVE for the latest WindowsPlayer version,\n"
            "or enter a specific version hash."
        )
        roblox_version_row.addWidget(roblox_version_label)
        self._sett_roblox_downloader_version_edit = QLineEdit()
        self._sett_roblox_downloader_version_edit.setPlaceholderText("LIVE")
        self._sett_roblox_downloader_version_edit.setText(
            str(S.get("roblox_downloader_version", "LIVE") or "LIVE")
        )
        self._sett_roblox_downloader_version_edit.setEnabled(
            _roblox_downloader_custom
        )
        self._sett_roblox_downloader_version_edit.editingFinished.connect(
            lambda: actions.save_ui_setting(
                "roblox_downloader_version",
                self._sett_roblox_downloader_version_edit.text(),
            )
        )
        roblox_version_row.addWidget(self._sett_roblox_downloader_version_edit, 1)
        f.addLayout(roblox_version_row)

        roblox_location_row = QHBoxLayout()
        roblox_location_row.setContentsMargins(20, 0, 0, 0)
        roblox_location_label = QLabel("Location Path")
        roblox_location_label.setFixedWidth(78)
        roblox_location_label.setToolTip(
            "Folder that will contain the downloaded version folder."
        )
        roblox_location_row.addWidget(roblox_location_label)
        self._sett_roblox_downloader_location_edit = QLineEdit()
        self._sett_roblox_downloader_location_edit.setPlaceholderText(
            roblox_downloader_mod.get_default_versions_path()
        )
        self._sett_roblox_downloader_location_edit.setText(
            str(
                S.get(
                    "roblox_downloader_location",
                    roblox_downloader_mod.get_default_versions_path(),
                )
                or roblox_downloader_mod.get_default_versions_path()
            )
        )
        self._sett_roblox_downloader_location_edit.setEnabled(
            _roblox_downloader_custom
        )
        self._sett_roblox_downloader_location_edit.editingFinished.connect(
            lambda: actions.save_ui_setting(
                "roblox_downloader_location",
                self._sett_roblox_downloader_location_edit.text(),
            )
        )
        roblox_location_row.addWidget(
            self._sett_roblox_downloader_location_edit,
            1,
        )
        self._sett_roblox_downloader_browse_btn = QPushButton("Browse Folder")
        self._sett_roblox_downloader_browse_btn.setEnabled(
            _roblox_downloader_custom
        )
        self._sett_roblox_downloader_browse_btn.clicked.connect(
            self._on_sett_browse_roblox_download_path
        )
        roblox_location_row.addWidget(self._sett_roblox_downloader_browse_btn)
        f.addLayout(roblox_location_row)

        self._sett_roblox_downloader_btn = QPushButton("Download Latest Roblox")
        self._sett_roblox_downloader_btn.setToolTip(
            "Download and extract the selected WindowsPlayer deployment."
        )
        self._sett_roblox_downloader_btn.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; color: {TEXT}; "
            f"border: 1px solid {LINE}; border-radius: 0; "
            f"text-align: center; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
        )
        self._sett_roblox_downloader_btn.clicked.connect(
            self._on_sett_download_roblox
        )
        f.addWidget(self._sett_roblox_downloader_btn)

        headless_hdr = QHBoxLayout()
        headless_hdr.setContentsMargins(0, 0, 0, 0)
        headless_hdr.addWidget(_sec("HEADLESS MANAGER"))
        headless_hdr.addStretch(1)
        f.addLayout(headless_hdr)

        self._sett_headless_chk = _chk(
            "headless_manager_enabled", "Enable Headless Manager",
            "Lists every running Roblox process so you can hide or show\n"
            "its window on demand.",
            on_change=self._on_sett_headless_manager,
        )
        f.addWidget(self._sett_headless_chk)

        headless_interval_row = QHBoxLayout()
        headless_interval_row.setContentsMargins(0, 0, 0, 0)
        headless_interval_label = QLabel("Scan Interval")
        headless_interval_label.setToolTip(
            "How often the Headless Manager looks for new Roblox windows.\n"
            "A longer interval uses less CPU but notices new clients later."
        )
        headless_interval_row.addWidget(headless_interval_label)
        headless_interval_row.addStretch(1)
        self._sett_headless_interval_spin = QSpinBox()
        self._sett_headless_interval_spin.setRange(
            int(headless_manager_mod.SCAN_INTERVAL_MIN),
            int(headless_manager_mod.SCAN_INTERVAL_MAX),
        )
        self._sett_headless_interval_spin.setValue(
            round(headless_manager_mod.get_scan_interval(S))
        )
        self._sett_headless_interval_spin.setSuffix(" s")
        self._sett_headless_interval_spin.setFixedWidth(80)
        self._sett_headless_interval_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_headless_interval_spin.valueChanged.connect(self._on_sett_headless_interval)
        headless_interval_row.addWidget(self._sett_headless_interval_spin)
        f.addLayout(headless_interval_row)

        self._headless_list = QListWidget()
        self._headless_list.setFixedHeight(160)
        self._headless_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._headless_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._headless_list.customContextMenuRequested.connect(self._headless_on_context_menu)
        f.addWidget(self._headless_list)
        self._refresh_headless_list([])

        f.addWidget(_sec("ROBLOX SETTINGS"))
        self._roblox_settings_config = roblox_settings_mod.get_customization_config(
            settings=S
        )
        self._roblox_settings_pending_config = dict(self._roblox_settings_config)

        _roblox_settings_desc = QLabel(
            "Close Roblox before applying changes so it does not overwrite them."
        )
        _roblox_settings_desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        _roblox_settings_desc.setWordWrap(True)
        f.addWidget(_roblox_settings_desc)

        f.addLayout(self._build_roblox_settings_path_row())

        self._roblox_settings_search = QLineEdit()
        self._roblox_settings_search.setPlaceholderText("Search Roblox settings...")
        self._roblox_settings_search.textChanged.connect(
            self._filter_roblox_settings
        )
        f.addWidget(self._roblox_settings_search)

        self._roblox_settings_tree = QTreeWidget()
        self._roblox_settings_tree.setColumnCount(3)
        self._roblox_settings_tree.setHeaderLabels(["Setting", "Type", "Value"])
        self._roblox_settings_tree.setFixedHeight(200)
        self._roblox_settings_tree.setRootIsDecorated(False)
        self._roblox_settings_tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._roblox_settings_tree.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._roblox_settings_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._roblox_settings_tree.header().setStretchLastSection(True)
        self._roblox_settings_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._roblox_settings_tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._roblox_settings_tree.itemSelectionChanged.connect(
            self._on_roblox_setting_selected
        )
        f.addWidget(self._roblox_settings_tree)

        _selected_row = QHBoxLayout()
        _selected_row.setContentsMargins(20, 0, 0, 0)
        _selected_row.addWidget(QLabel("Selected Setting"))
        self._roblox_settings_selected_label = QLabel("None")
        self._roblox_settings_selected_label.setStyleSheet(f"color: {MUTED};")
        _selected_row.addWidget(self._roblox_settings_selected_label, 1)
        f.addLayout(_selected_row)

        _type_row = QHBoxLayout()
        _type_row.setContentsMargins(20, 0, 0, 0)
        _type_row.addWidget(QLabel("Type"))
        self._roblox_settings_type_label = QLabel("None")
        self._roblox_settings_type_label.setStyleSheet(f"color: {MUTED};")
        _type_row.addWidget(self._roblox_settings_type_label, 1)
        f.addLayout(_type_row)

        _state_row = QHBoxLayout()
        _state_row.setContentsMargins(20, 0, 0, 0)
        _state_row.addWidget(QLabel("State"))
        self._roblox_settings_state_label = QLabel("Ready")
        self._roblox_settings_state_label.setStyleSheet(f"color: {MUTED};")
        _state_row.addWidget(self._roblox_settings_state_label, 1)
        f.addLayout(_state_row)

        _value_row = QHBoxLayout()
        _value_row.setContentsMargins(20, 0, 0, 0)
        _value_row.addWidget(QLabel("Value"))
        self._roblox_settings_value_stack = QStackedWidget()
        self._roblox_settings_value_edit = QLineEdit()
        self._roblox_settings_value_edit.editingFinished.connect(
            self._on_roblox_setting_text_finished
        )
        self._roblox_settings_value_stack.addWidget(
            self._roblox_settings_value_edit
        )
        self._roblox_settings_bool_edit = QCheckBox("false")
        self._roblox_settings_bool_edit.stateChanged.connect(
            self._on_roblox_setting_bool_changed
        )
        self._roblox_settings_value_stack.addWidget(
            self._roblox_settings_bool_edit
        )
        self._roblox_settings_value_stack.setEnabled(False)
        _value_row.addWidget(self._roblox_settings_value_stack, 1)
        f.addLayout(_value_row)

        _roblox_settings_btn_row = QHBoxLayout()
        _roblox_settings_btn_row.setContentsMargins(20, 0, 0, 0)
        self._roblox_settings_reload_btn = QPushButton("Reload from Roblox")
        self._roblox_settings_reload_btn.clicked.connect(
            self._reload_roblox_settings
        )
        self._roblox_settings_apply_btn = QPushButton("Apply to Roblox")
        self._roblox_settings_apply_btn.setEnabled(False)
        self._roblox_settings_apply_btn.clicked.connect(
            self._apply_roblox_settings
        )
        _roblox_settings_btn_row.addWidget(self._roblox_settings_reload_btn)
        _roblox_settings_btn_row.addStretch(1)
        _roblox_settings_btn_row.addWidget(self._roblox_settings_apply_btn)
        f.addLayout(_roblox_settings_btn_row)

        self._roblox_settings_auto_apply_chk = QCheckBox(
            "Auto Apply Settings"
        )
        self._roblox_settings_auto_apply_chk.setChecked(
            bool(self._roblox_settings_config.get("auto_apply", False))
        )
        self._roblox_settings_auto_apply_chk.stateChanged.connect(
            self._on_roblox_advanced_auto_apply_toggle
        )
        f.addWidget(self._roblox_settings_auto_apply_chk)

        f.addStretch(1)


        # Themes (Page 3)
        sa, f = _scrollable()
        content_stack.addWidget(sa)
        f.addWidget(_sec("THEME"))
        _theme_hint = QLabel("Pick a theme. It is used the next time the application starts.")
        _theme_hint.setWordWrap(True)
        _theme_hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_theme_hint)

        _gallery = QGridLayout()
        _gallery.setContentsMargins(0, 4, 0, 4)
        _gallery.setHorizontalSpacing(12)
        _gallery.setVerticalSpacing(12)
        _saved_theme = color_themes.theme_name(actions.load_ui_settings())
        self._theme_cards = {}
        _group = QButtonGroup(self)
        _group.setExclusive(True)
        for _i, (_key, _label) in enumerate(color_themes.theme_choices()):
            _card = _ThemeCard(_key, _label, color_themes.resolve_colors({color_themes.THEME_SETTING: _key}))
            _card.setChecked(_key == _saved_theme)
            _card.clicked.connect(lambda _checked=False, key=_key: self._on_theme_card_clicked(key))
            _group.addButton(_card)
            self._theme_cards[_key] = _card
            _gallery.addWidget(_card, _i // 3, _i % 3)
        for _column in range(3):
            _gallery.setColumnStretch(_column, 1)
        f.addLayout(_gallery)

        self._theme_restart_banner = QFrame()
        self._theme_restart_banner.setObjectName("themeRestartBanner")
        self._theme_restart_banner.setStyleSheet(
            f"QFrame#themeRestartBanner {{ background: {ACCENT_TINT}; border: 1px solid {ACCENT_TINT_LINE}; "
            f"border-radius: 10px; }}"
        )
        _banner_row = QHBoxLayout(self._theme_restart_banner)
        _banner_row.setContentsMargins(14, 10, 10, 10)
        _banner_label = QLabel("Restart to apply")
        _banner_label.setStyleSheet(f"color: {TEXT}; font-weight: 600; background: transparent; border: 0;")
        _banner_row.addWidget(_banner_label)
        _banner_row.addStretch(1)
        self._theme_restart_btn = QPushButton("Restart now")
        self._theme_restart_btn.setObjectName("primary")
        self._theme_restart_btn.setFixedHeight(30)
        self._theme_restart_btn.clicked.connect(self._restart_for_theme)
        _banner_row.addWidget(self._theme_restart_btn)
        f.addWidget(self._theme_restart_banner)
        self._refresh_theme_restart_banner()
        f.addStretch(1)

        # Misc (Page 4)
        sa, f = _scrollable()
        content_stack.addWidget(sa)

        f.addWidget(_sec("BROWSER ENGINE"))
        _br_lbl = QLabel(
            "Supported browsers: Chrome, Firefox, Edge, Brave, and built-in Chromium. "
            "Opera GX users should select Chromium."
        )
        _br_lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        _br_lbl.setWordWrap(True)
        f.addWidget(_br_lbl)

        _cur_br = S.get("browser_type", actions.DEFAULT_BROWSER)
        _br_grp = QButtonGroup(sa)
        for br_key, br_label, br_tip in [
            ("chrome", "Google Chrome",
             "Use Google Chrome through Selenium. Google Chrome must be installed."),
            ("firefox", "Mozilla Firefox",
             "Use Mozilla Firefox through Selenium. Mozilla Firefox must be installed."),
            ("edge", "Microsoft Edge",
             "Use Microsoft Edge through Selenium. Microsoft Edge must be installed."),
            ("brave", "Brave",
             "Use Brave through Selenium (Chromium-based). Brave must be installed."),
            ("chromium", "Chromium",
             "Use the built-in Chromium and matching driver. Download it with the button below."),
        ]:
            rb = QRadioButton(br_label)
            rb.setChecked(br_key == _cur_br)
            rb.setToolTip(br_tip)
            _br_grp.addButton(rb)
            rb.toggled.connect(
                (lambda k: lambda checked: actions.save_ui_setting("browser_type", k) if checked else None)(br_key)
            )
            f.addWidget(rb)

        _chromium_installed = bool(chromium_mod.validate_chromium())
        self._sett_chromium_btn = QPushButton(
            "Reinstall Chromium" if _chromium_installed else "Download Chromium"
        )
        self._sett_chromium_btn.setEnabled(True)
        self._sett_chromium_btn.setToolTip(
            "Download the latest portable Chromium and matching ChromeDriver.\n"
            "Reinstalling replaces the current Chromium folder with a clean copy."
        )
        self._sett_chromium_btn.clicked.connect(self._on_sett_dl_chromium)
        f.addLayout(_sub_indent(self._sett_chromium_btn))
        if self._chromium_status_result is not None:
            self._on_chromium_status(self._chromium_status_result)

        self._enc_section_lbl = _sec("ENCRYPTION")
        self._misc_settings_scroll = sa
        f.addWidget(self._enc_section_lbl)
        _enc_btn = QPushButton("Switch Encryption Method")
        _enc_btn.setToolTip(
            "Change between hardware-based and password-based encryption.\n"
            "You will need to re-import your accounts after switching."
        )
        _enc_btn.clicked.connect(self._on_sett_switch_encryption)
        f.addWidget(_enc_btn)
        _move_lbl = QLabel(
            "Moving to another PC: Device Locked accounts only open on this PC. Switch to Password "
            "Locked (or use Export Accounts with a password) before copying the data folder; "
            "Password Locked and Unprotected data opens on any PC."
        )
        _move_lbl.setWordWrap(True)
        _move_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_move_lbl)

        f.addWidget(_sec("DATA"))
        _export_btn = QPushButton("Export Accounts")
        _export_btn.setToolTip(
            "Save all accounts to a file protected by a password you choose.\n"
            "Use it to back up your accounts or to move them to another computer."
        )
        _export_btn.clicked.connect(self._on_sett_export_accounts)
        f.addWidget(_export_btn)

        _import_btn = QPushButton("Import Accounts")
        _import_btn.setToolTip(
            "Add the accounts from a file made with Export Accounts.\n"
            "You can choose whether accounts that already exist are replaced."
        )
        _import_btn.clicked.connect(self._on_sett_import_accounts)
        f.addWidget(_import_btn)

        f.addWidget(_sec("AUTO BACKUP"))
        _auto_backup_info = QLabel(
            f"Accounts are backed up automatically every 24 hours. The newest {auto_backup_mod.KEEP} "
            "automatic backups are kept; backups made with Backup now are never deleted."
        )
        _auto_backup_info.setWordWrap(True)
        _auto_backup_info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(_auto_backup_info)
        self._backup_status_lbl = QLabel()
        self._backup_status_lbl.setWordWrap(True)
        self._backup_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(self._backup_status_lbl)
        _backup_row = QHBoxLayout()
        _backup_row.setSpacing(8)
        for _label, _slot, _tip in (
            ("Backup now", self._on_backup_now, "Save a backup right now"),
            ("Restore from backup", self._on_restore_backup, "Pick a backup by date and restore its accounts"),
            ("Open backups folder", self._open_backup_folder, "Show the backup files in Explorer"),
        ):
            _b = QPushButton(_label)
            _b.setToolTip(_tip)
            _b.clicked.connect(_slot)
            _backup_row.addWidget(_b)
        _backup_row.addStretch(1)
        f.addLayout(_backup_row)
        _backup_row2 = QHBoxLayout()
        _backup_row2.setSpacing(8)
        _pw_btn = QPushButton("Backup password…")
        _pw_btn.setToolTip("Set a password so backups can be restored on another PC")
        _pw_btn.clicked.connect(self._on_set_backup_password)
        _backup_row2.addWidget(_pw_btn)
        _folder_btn = QPushButton("Extra backup folder…")
        _folder_btn.setToolTip("Also save each backup to a folder like a USB drive, Google Drive or OneDrive")
        _folder_btn.clicked.connect(self._on_choose_backup_folder)
        _backup_row2.addWidget(_folder_btn)
        self._backup_clear_folder_btn = QPushButton("Stop extra copies")
        self._backup_clear_folder_btn.clicked.connect(self._on_clear_backup_folder)
        _backup_row2.addWidget(self._backup_clear_folder_btn)
        _backup_row2.addStretch(1)
        f.addLayout(_backup_row2)
        self._refresh_backup_status()

        _wipe_btn = QPushButton("Wipe All Data")
        _wipe_btn.setToolTip(
            "Permanently delete all saved accounts, settings, and cached data.\n"
            "This action cannot be undone."
        )
        _wipe_btn.setStyleSheet(
            "QPushButton { color: #EF5350; border-color: #5A2A2A; }"
            "QPushButton:hover { background: #3A1A1A; color: #FF6B6B; }"
        )
        _wipe_btn.clicked.connect(self._on_sett_wipe_data)
        f.addWidget(_wipe_btn)

        f.addStretch(1)

        # Proxy (Page 5)
        _proxy_sa = QScrollArea()
        _proxy_sa.setWidgetResizable(True)
        _proxy_sa.setFrameShape(QFrame.Shape.NoFrame)
        _proxy_sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _proxy_sa.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        _proxy_panel = self._build_proxy_panel()
        _proxy_panel.setStyleSheet("QFrame#centerPanel { background: transparent; }")
        _proxy_sa.setWidget(_proxy_panel)
        content_stack.addWidget(_proxy_sa)

        # Developer (Page 6)
        sa, f = _scrollable()
        content_stack.addWidget(sa)

        f.addWidget(_sec("DEVELOPER"))
        self._sett_devmode_chk = _chk(
            "developer_mode", "Developer Mode",
            "Unlock developer-only features.\n"
            "Enables Copy Cookie and WebSocket controls.",
            on_change=self._on_sett_developer_mode,
        )
        f.addWidget(self._sett_devmode_chk)

        self._sett_copycookie_chk = _chk(
            "enable_copy_cookie", "Enable Copy Cookie Button",
            "Show a Copy Cookie button on each account entry.\n"
            "WARNING: cookies grant full account access, never share them.",
        )
        f.addWidget(self._sett_copycookie_chk)
        _dev_on = S.get("developer_mode", False)
        self._sett_copycookie_chk.setEnabled(_dev_on)

        f.addWidget(_sec("WEBSOCKET SERVER"))
        self._sett_ws_chk = _chk(
            "websocket_enabled", "Enable WebSocket Server",
            "Start a local WebSocket server so external scripts can\n"
            "query account data and trigger actions.",
            on_change=self._on_sett_ws_changed,
        )
        f.addWidget(self._sett_ws_chk)
        self._sett_ws_chk.setEnabled(_dev_on)

        ws_port_row = QHBoxLayout()
        ws_port_row.setContentsMargins(20, 0, 0, 0)
        _wsp_lbl = QLabel("Port")
        _wsp_lbl.setToolTip("TCP port for the WebSocket server (default: 7963).")
        ws_port_row.addWidget(_wsp_lbl)
        ws_port_row.addStretch(1)
        self._sett_ws_port = QSpinBox()
        self._sett_ws_port.setRange(1024, 65535)
        self._sett_ws_port.setValue(int(S.get("websocket_port", 7963)))
        self._sett_ws_port.setFixedWidth(80)
        self._sett_ws_port.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_ws_port.valueChanged.connect(
            lambda v: actions.save_ui_setting("websocket_port", v)
        )
        ws_port_row.addWidget(self._sett_ws_port)
        f.addLayout(ws_port_row)

        self._sett_ws_pw_chk = _chk(
            "websocket_require_password", "Require Password",
            "Clients must supply a password to connect to the WebSocket server.",
            on_change=self._on_sett_ws_password_required,
        )
        f.addLayout(_sub_indent(self._sett_ws_pw_chk))

        self._sett_ws_saved_password = str(
            self.manager.get_secure_setting("websocket_password", "") or ""
        )
        ws_password_row = QHBoxLayout()
        ws_password_row.setContentsMargins(20, 0, 0, 0)
        ws_password_label = QLabel("Password")
        ws_password_row.addWidget(ws_password_label)
        ws_password_row.addStretch(1)
        self._sett_ws_password_edit = QLineEdit()
        self._sett_ws_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._sett_ws_password_edit.setMaxLength(256)
        self._sett_ws_password_edit.setFixedWidth(180)
        self._sett_ws_password_edit.setText(self._sett_ws_saved_password)
        self._sett_ws_password_edit.setToolTip(
            "Password used by AUTH <password> | <command>."
        )
        ws_password_row.addWidget(self._sett_ws_password_edit)
        self._sett_ws_password_set_btn = QPushButton("Set")
        self._sett_ws_password_set_btn.setFixedWidth(60)
        self._sett_ws_password_set_btn.clicked.connect(
            self._on_sett_ws_password_save
        )
        ws_password_row.addWidget(self._sett_ws_password_set_btn)
        f.addLayout(ws_password_row)

        self._update_ws_password_controls()

        ws_docs_btn = QPushButton("Read Documentation")
        ws_docs_btn.clicked.connect(
            lambda: webbrowser.open("https://www.evanovarram.com/documentation/developer")
        )
        f.addWidget(ws_docs_btn)

        f.addStretch(1)

        return panel

    def _on_sett_topmost(self, enabled: bool):
        current = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if current == enabled:
            return
        restore_window_grid = self._window_grid_hotkey_registered
        if restore_window_grid:
            self._unregister_window_grid_hotkey()
        restore_kill = bool(getattr(self, "_kill_hotkey_hwnd", 0))
        if restore_kill:
            self._unregister_kill_hotkey()
        restore_winkeys = bool(getattr(self, "_window_keys_hwnd", 0))
        if restore_winkeys:
            self._unregister_window_keys()
        self.hide()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()
        for detached in self._detached_windows.values():
            was_visible = detached.isVisible()
            was_maximized = detached.isMaximized()
            was_minimized = detached.isMinimized()
            detached.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
            if not was_visible:
                continue
            if was_maximized:
                detached.showMaximized()
            elif was_minimized:
                detached.showMinimized()
            else:
                detached.showNormal()
        if restore_window_grid:
            QTimer.singleShot(
                0,
                lambda: self._apply_window_grid_hotkey(show_error=False),
            )
        if restore_kill:
            QTimer.singleShot(0, lambda: self._apply_kill_hotkey(show_error=False))
        if restore_winkeys:
            QTimer.singleShot(0, lambda: self._apply_window_keys(show_error=False))
        print(f"[INFO] Always on Top {'enabled' if enabled else 'disabled'}")

    def _on_sett_multi_select(self, enabled: bool):
        if hasattr(self, "_account_list") and self._account_list:
            mode = (QAbstractItemView.SelectionMode.ExtendedSelection
                    if enabled else
                    QAbstractItemView.SelectionMode.SingleSelection)
            self._account_list.setSelectionMode(mode)

    def _on_sett_start_menu(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        self._run_shortcut_toggle(
            "start_menu", enabled, self._sett_startmenu_chk,
            windows_startup_mod.enable_start_menu if enabled else windows_startup_mod.disable_start_menu,
        )

    def _run_shortcut_toggle(self, kind: str, enabled: bool, checkbox: QCheckBox, work) -> None:
        checkbox.setEnabled(False)

        def _worker() -> None:
            self._bridge.shortcut_toggle_done.emit(kind, enabled, work())

        threading.Thread(target=_worker, daemon=True, name=f"shortcut-{kind}").start()

    def _on_shortcut_toggle_done(self, kind: str, enabled: bool, result) -> None:
        startup = kind == "startup"
        checkbox = self._sett_startup_chk if startup else self._sett_startmenu_chk
        checkbox.setEnabled(True)
        if result:
            if startup:
                actions.save_ui_setting("start_with_windows", enabled)
                print(f"[INFO] Start with Windows {'enabled' if enabled else 'disabled'}")
            else:
                print(f"[INFO] Start Menu shortcut {'created' if enabled else 'removed'}")
            return

        checkbox.blockSignals(True)
        checkbox.setChecked(not enabled)
        checkbox.blockSignals(False)
        if startup:
            actions.save_ui_setting("start_with_windows", not enabled)
        self._show_operation_error(result)

    def _on_sett_startup(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        self._run_shortcut_toggle(
            "startup", enabled, self._sett_startup_chk,
            windows_startup_mod.enable_startup if enabled else windows_startup_mod.disable_startup,
        )

    def _on_sett_boost_ram(self, enabled: bool):
        if hasattr(self, "_sett_ram_spin"):
            self._sett_ram_spin.setEnabled(enabled)
        if enabled:
            self._start_ram_boost()
        else:
            self._stop_ram_boost()

    def _on_sett_rename_windows(self, enabled: bool):
        self._update_rename_mode_controls()
        if enabled:
            self._start_rename_windows()
        else:
            self._stop_rename_windows()

    def _update_rename_mode_controls(self):
        if not hasattr(self, "_sett_rename_chk"):
            return
        enabled = self._sett_rename_chk.isChecked()
        self._sett_rename_username_radio.setEnabled(enabled)
        self._sett_rename_note_radio.setEnabled(enabled)

    def _on_sett_rename_mode(self, mode: str, checked: bool):
        if not checked:
            return
        if mode not in ("username", "note"):
            mode = "username"
        actions.save_ui_setting("rename_roblox_windows_mode", mode)
        if self._window_renamer is not None:
            self._window_renamer.set_title_mode(mode)

    def _on_sett_presence_indicator(self, enabled: bool):
        if enabled:
            self._start_presence_scanner()
        else:
            self._stop_presence_scanner()
        self._refresh_account_list()

    def _on_sett_installer_fix(self, enabled: bool):
        try:
            if enabled:
                RobloxAPI.quarantine_installers()
            else:
                RobloxAPI.restore_installers()
        except Exception as e:
            print(f"[ERROR] Roblox Installer Fix toggle failed: {e}")

    def _build_roblox_settings_path_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(20, 0, 0, 0)
        label = QLabel("Path")
        label.setFixedWidth(78)
        label.setToolTip(
            "Defaults to Roblox's GlobalBasicSettings_13.xml in Local AppData.\n"
            "Browse for a custom Roblox settings XML file."
        )
        row.addWidget(label)
        self._roblox_settings_path_edit = QLineEdit()
        self._roblox_settings_path_edit.setReadOnly(True)
        self._roblox_settings_path_edit.setMinimumWidth(0)
        self._roblox_settings_path_edit.setAccessibleName("Roblox settings XML location")
        self._roblox_settings_path_edit.setPlaceholderText("Default Roblox settings location")
        label.setBuddy(self._roblox_settings_path_edit)
        row.addWidget(self._roblox_settings_path_edit, 1)
        self._roblox_settings_browse_btn = QPushButton("Browse File")
        self._roblox_settings_browse_btn.clicked.connect(self._browse_roblox_settings_path)
        row.addWidget(self._roblox_settings_browse_btn)
        self._refresh_roblox_settings_path()
        return row

    def _refresh_roblox_settings_path(self):
        path = roblox_settings_mod.get_settings_path()
        text = str(path) if path is not None else ""
        self._roblox_settings_path_edit.setText(text)
        self._roblox_settings_path_edit.setToolTip(text)
        self._update_roblox_settings_path_controls()

    def _update_roblox_settings_path_controls(self):
        if not hasattr(self, "_roblox_settings_browse_btn"):
            return
        busy = any((
            self._roblox_settings_loading,
            self._roblox_settings_applying,
            self._roblox_settings_auto_applying,
            self._roblox_settings_path_changing,
        ))
        self._roblox_settings_browse_btn.setEnabled(not busy)

    def _browse_roblox_settings_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Roblox Settings XML", self._roblox_settings_path_edit.text(),
            "XML files (*.xml);;All files (*)",
        )
        if path:
            self._set_roblox_settings_path(path)

    def _set_roblox_settings_path(self, path: str):
        if any((
            self._roblox_settings_loading, self._roblox_settings_applying,
            self._roblox_settings_auto_applying, self._roblox_settings_path_changing,
        )):
            return
        self._roblox_settings_path_changing = True
        self._roblox_settings_reload_btn.setEnabled(False)
        self._roblox_settings_apply_btn.setEnabled(False)
        self._roblox_settings_auto_apply_chk.setEnabled(False)
        self._roblox_settings_value_stack.setEnabled(False)
        self._roblox_settings_state_label.setText("Loading location...")
        self._update_roblox_settings_path_controls()
        roblox_settings_mod.set_settings_path_async(
            path, self._bridge.roblox_settings_path_changed.emit,
        )

    def _on_roblox_settings_path_changed(self, result: OperationResult):
        self._roblox_settings_path_changing = False
        self._refresh_roblox_settings_path()
        if result and bool((result.data or {}).get("changed", False)):
            self._load_roblox_settings(show_error=True)
            return
        self._roblox_settings_reload_btn.setEnabled(True)
        self._roblox_settings_auto_apply_chk.setEnabled(bool(self._roblox_settings_records))
        self._update_roblox_settings_apply_state()
        self._on_roblox_setting_selected()
        if not result:
            self._show_operation_error(result)

    def _load_roblox_settings(
        self,
        show_error: bool = True,
        reload_from_roblox: bool = False,
    ):
        if self._roblox_settings_loading or self._roblox_settings_applying:
            return
        self._roblox_settings_loading = True
        self._update_roblox_settings_path_controls()
        self._roblox_settings_show_load_error = show_error
        self._roblox_settings_reload_btn.setEnabled(False)
        self._roblox_settings_apply_btn.setEnabled(False)
        self._roblox_settings_auto_apply_chk.setEnabled(False)
        if reload_from_roblox:
            roblox_settings_mod.reload_local_profile_from_roblox_async(
                self._bridge.roblox_settings_loaded.emit
            )
        else:
            roblox_settings_mod.load_local_profile_async(
                self._bridge.roblox_settings_loaded.emit
            )

    def _on_roblox_settings_loaded(self, result: OperationResult):
        self._roblox_settings_loading = False
        self._update_roblox_settings_path_controls()
        self._roblox_settings_reload_btn.setEnabled(True)
        if not result:
            self._roblox_settings_records.clear()
            self._roblox_settings_pending.clear()
            self._roblox_settings_file_hash = ""
            self._roblox_settings_tree.clear()
            self._roblox_settings_selected_label.setText("None")
            self._roblox_settings_type_label.setText("None")
            self._roblox_settings_value_stack.setEnabled(False)
            self._roblox_settings_auto_apply_chk.setEnabled(False)
            self._roblox_settings_startup_reload = False
            self._update_roblox_settings_apply_state()
            if self._roblox_settings_show_load_error:
                self._show_operation_error(result)
            return

        data = result.data or {}
        self._roblox_settings_file_hash = str(data.get("file_hash", ""))
        self._roblox_settings_records = {
            str(record["key"]): dict(record)
            for record in data.get("settings", [])
        }
        self._roblox_settings_pending.clear()
        self._roblox_settings_config = roblox_settings_mod.get_customization_config(
            settings=actions.load_ui_settings(),
            records=self._roblox_settings_records,
        )
        self._roblox_settings_pending_config = dict(self._roblox_settings_config)
        self._populate_roblox_settings_tree()
        self._roblox_settings_auto_apply_chk.blockSignals(True)
        self._roblox_settings_auto_apply_chk.setEnabled(True)
        self._roblox_settings_auto_apply_chk.setChecked(
            bool(self._roblox_settings_config.get("auto_apply", False))
        )
        self._roblox_settings_auto_apply_chk.blockSignals(False)
        self._update_roblox_settings_apply_state()
        if self._roblox_settings_startup_reload:
            self._roblox_settings_startup_reload = False
            if (
                bool(self._roblox_settings_config.get("auto_apply", False))
                or bool(self._roblox_settings_config.get("lock_owned", False))
            ):
                QTimer.singleShot(0, self._start_roblox_auto_apply)

    def _populate_roblox_settings_tree(self):
        selected_key = self._current_roblox_setting_key()
        self._roblox_settings_tree.clear()
        search_text = self._roblox_settings_search.text().strip().lower()
        selected_item = None
        for key, record in sorted(
            self._roblox_settings_records.items(),
            key=lambda item: item[1].get("name", item[0]).lower(),
        ):
            value = str(record.get("value", ""))
            searchable = " ".join((
                key,
                str(record.get("name", "")),
                str(record.get("xml_type", "")),
                value,
            )).lower()
            if search_text and search_text not in searchable:
                continue
            setting_name = str(record.get("name", key))
            if bool(record.get("pending", False)):
                setting_name = f"{setting_name} *"
            item = QTreeWidgetItem([
                setting_name,
                str(record.get("xml_type", "")),
                value,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            self._roblox_settings_tree.addTopLevelItem(item)
            if key == selected_key:
                selected_item = item

        if self._roblox_settings_tree.topLevelItemCount() == 0:
            empty_item = QTreeWidgetItem(["No settings found", "", ""])
            empty_item.setFlags(
                empty_item.flags() & ~Qt.ItemFlag.ItemIsEnabled
            )
            self._roblox_settings_tree.addTopLevelItem(empty_item)
            self._on_roblox_setting_selected()
        elif selected_item is not None:
            self._roblox_settings_tree.setCurrentItem(selected_item)
        else:
            self._roblox_settings_tree.setCurrentItem(
                self._roblox_settings_tree.topLevelItem(0)
            )

    def _filter_roblox_settings(self, _text: str):
        self._populate_roblox_settings_tree()

    def _current_roblox_setting_key(self) -> str:
        item = self._roblox_settings_tree.currentItem()
        if item is None:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _on_roblox_setting_selected(self):
        key = self._current_roblox_setting_key()
        record = self._roblox_settings_records.get(key)
        if record is None:
            self._roblox_settings_selected_label.setText("None")
            self._roblox_settings_type_label.setText("None")
            self._roblox_settings_state_label.setText("Ready")
            self._roblox_settings_value_stack.setEnabled(False)
            return

        value = str(record.get("value", ""))
        xml_type = str(record.get("xml_type", "string"))
        self._roblox_settings_editor_active = True
        self._roblox_settings_value_stack.setEnabled(
            bool(record.get("editable", True))
        )
        self._roblox_settings_selected_label.setText(key)
        self._roblox_settings_type_label.setText(xml_type)
        if bool(record.get("pending", False)):
            self._roblox_settings_state_label.setText("Pending")
        else:
            self._roblox_settings_state_label.setText("Ready")
        if xml_type == "bool":
            self._roblox_settings_value_stack.setCurrentIndex(1)
            checked = value.strip().lower() == "true"
            self._roblox_settings_bool_edit.setChecked(checked)
            self._roblox_settings_bool_edit.setText("true" if checked else "false")
        else:
            self._roblox_settings_value_stack.setCurrentIndex(0)
            self._roblox_settings_value_edit.setText(value)
        self._roblox_settings_editor_active = False

    def _on_roblox_setting_text_finished(self):
        if not self._roblox_settings_editor_active:
            self._stage_roblox_setting_value(
                self._roblox_settings_value_edit.text()
            )

    def _on_roblox_setting_bool_changed(self, state: int):
        checked = state == Qt.CheckState.Checked.value
        self._roblox_settings_bool_edit.setText("true" if checked else "false")
        if not self._roblox_settings_editor_active:
            self._stage_roblox_setting_value("true" if checked else "false")

    def _stage_roblox_setting_value(self, value: str):
        if self._roblox_settings_path_changing or self._roblox_settings_loading:
            return
        key = self._current_roblox_setting_key()
        record = self._roblox_settings_records.get(key)
        if record is None or not bool(record.get("editable", True)):
            return
        save_result = roblox_settings_mod.save_advanced_setting(key, str(value))
        if not save_result:
            self._roblox_settings_editor_active = True
            if str(record.get("xml_type", "string")) == "bool":
                checked = str(record.get("value", "")).lower() == "true"
                self._roblox_settings_bool_edit.setChecked(checked)
                self._roblox_settings_bool_edit.setText(
                    "true" if checked else "false"
                )
            else:
                self._roblox_settings_value_edit.setText(
                    str(record.get("value", ""))
                )
            self._roblox_settings_editor_active = False
            self._show_operation_error(save_result)
            return
        normalized = str((save_result.data or {}).get("settings", {}).get(key, {}).get("value", value))
        record["value"] = normalized
        record["pending"] = normalized != str(record.get("source_value", ""))
        self._roblox_settings_config = roblox_settings_mod.get_customization_config(
            records=self._roblox_settings_records
        )
        self._roblox_settings_pending_config = dict(self._roblox_settings_config)
        self._refresh_roblox_setting_row(key)
        self._update_roblox_settings_apply_state()
        self._on_roblox_setting_selected()

    def _refresh_roblox_setting_row(self, key: str):
        for index in range(self._roblox_settings_tree.topLevelItemCount()):
            item = self._roblox_settings_tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == key:
                record = self._roblox_settings_records.get(key, {})
                setting_name = str(record.get("name", key))
                if bool(record.get("pending", False)):
                    setting_name = f"{setting_name} *"
                item.setText(
                    2,
                    str(record.get("value", "")),
                )
                item.setText(
                    0,
                    setting_name,
                )
                break

    def _update_roblox_settings_apply_state(self):
        enabled = any(
            bool(record.get("pending", False))
            for record in self._roblox_settings_records.values()
        )
        self._roblox_settings_apply_btn.setEnabled(
            enabled
            and bool(self._roblox_settings_records)
            and not self._roblox_settings_loading
            and not self._roblox_settings_applying
            and not self._roblox_settings_path_changing
            and not self._roblox_settings_auto_applying
        )


    def _reload_roblox_settings(self):
        self._load_roblox_settings(
            show_error=True,
            reload_from_roblox=True,
        )

    def _apply_roblox_settings(self):
        if self._roblox_settings_applying:
            return
        if not any(
            bool(record.get("pending", False))
            for record in self._roblox_settings_records.values()
        ):
            return
        self._roblox_settings_applying = True
        self._update_roblox_settings_path_controls()
        self._roblox_settings_apply_btn.setEnabled(False)
        self._roblox_settings_reload_btn.setEnabled(False)
        roblox_settings_mod.apply_local_profile_async(
            self._bridge.roblox_settings_applied.emit
        )

    def _on_roblox_settings_applied(self, result: OperationResult):
        self._roblox_settings_applying = False
        self._update_roblox_settings_path_controls()
        self._roblox_settings_reload_btn.setEnabled(True)
        if not result:
            self._update_roblox_settings_apply_state()
            self._show_operation_error(result)
            return
        data = result.data or {}
        self._roblox_settings_file_hash = str(data.get("file_hash", ""))
        self._roblox_settings_records = {
            str(record["key"]): dict(record)
            for record in data.get("settings", [])
        }
        self._roblox_settings_pending.clear()
        self._roblox_settings_config = roblox_settings_mod.get_customization_config(
            records=self._roblox_settings_records
        )
        self._roblox_settings_pending_config = dict(self._roblox_settings_config)
        self._populate_roblox_settings_tree()
        self._roblox_settings_auto_apply_chk.blockSignals(True)
        self._roblox_settings_auto_apply_chk.setChecked(
            bool(self._roblox_settings_config.get("auto_apply", False))
        )
        self._roblox_settings_auto_apply_chk.blockSignals(False)
        self._update_roblox_settings_apply_state()

    def _on_roblox_advanced_auto_apply_toggle(self, state: int):
        enabled = state == Qt.CheckState.Checked.value
        result = roblox_settings_mod.save_advanced_auto_apply(enabled)
        if not result:
            self._roblox_settings_auto_apply_chk.blockSignals(True)
            self._roblox_settings_auto_apply_chk.setChecked(not enabled)
            self._roblox_settings_auto_apply_chk.blockSignals(False)
            self._show_operation_error(result)
            return
        self._roblox_settings_config = roblox_settings_mod.get_customization_config(
            records=self._roblox_settings_records
        )
        self._roblox_settings_pending_config = dict(
            self._roblox_settings_config
        )

    def _start_roblox_auto_apply(self):
        if self._roblox_settings_auto_applying:
            return
        self._roblox_settings_auto_applying = True
        self._update_roblox_settings_path_controls()
        roblox_settings_mod.apply_saved_customizations_async(
            None,
            self._bridge.roblox_settings_auto_applied.emit,
        )

    def _on_roblox_settings_auto_applied(self, result: OperationResult):
        self._roblox_settings_auto_applying = False
        self._update_roblox_settings_path_controls()
        if not result:
            print(
                f"[ERROR] Roblox settings Auto Apply failed: "
                f"{result.code} {result.message}"
            )
            self._show_operation_error(result)
            return
        if 4 not in self._built_pages:
            return
        data = result.data or {}
        self._roblox_settings_file_hash = str(data.get("file_hash", ""))
        self._roblox_settings_records = {
            str(record["key"]): dict(record)
            for record in data.get("settings", [])
        }
        self._roblox_settings_config = roblox_settings_mod.get_customization_config(
            records=self._roblox_settings_records
        )
        self._roblox_settings_pending_config = dict(
            self._roblox_settings_config
        )
        self._populate_roblox_settings_tree()
        self._roblox_settings_auto_apply_chk.blockSignals(True)
        self._roblox_settings_auto_apply_chk.setChecked(
            bool(self._roblox_settings_config.get("auto_apply", False))
        )
        self._roblox_settings_auto_apply_chk.blockSignals(False)
        self._update_roblox_settings_apply_state()

    _WINDOW_KEY_SPECS = (
        ("minimize_all_hotkey", "MINIMIZE_ALL_HOTKEY_ID", "DEFAULT_MINIMIZE_HOTKEY"),
        ("restore_all_hotkey", "RESTORE_ALL_HOTKEY_ID", "DEFAULT_RESTORE_HOTKEY"),
        ("minimize_one_hotkey", "MINIMIZE_ONE_HOTKEY_ID", "DEFAULT_MINIMIZE_ONE_HOTKEY"),
    )

    def _unregister_window_keys(self) -> None:
        hwnd = getattr(self, "_window_keys_hwnd", 0)
        if hwnd:
            for _key, id_name, _default in self._WINDOW_KEY_SPECS:
                window_grid_mod.unregister_hotkey(hwnd, getattr(window_grid_mod, id_name))
        self._window_keys_hwnd = 0

    def _apply_window_keys(self, show_error: bool = True) -> bool:
        self._unregister_window_keys()
        settings = actions.load_ui_settings()
        if not settings.get("window_keys_enabled", False):
            return False
        window_handle = int(self.winId())
        failed = []
        for setting_key, id_name, default_name in self._WINDOW_KEY_SPECS:
            default = getattr(window_grid_mod, default_name)
            sequence = str(settings.get(setting_key, default) or default)
            result = window_grid_mod.register_hotkey(
                window_handle, sequence, getattr(window_grid_mod, id_name)
            )
            if result:
                print(f"[INFO] Shortcut {sequence}: {setting_key.replace('_hotkey', '').replace('_', ' ')}")
            else:
                failed.append(f"{sequence}: {result.message}")
        self._window_keys_hwnd = window_handle
        if failed:
            print("[WARNING] Some window shortcuts could not be registered: " + "; ".join(failed))
            if show_error:
                _show_error(
                    self, "Shortcut Not Available",
                    "These keys could not be used:\n\n" + "\n".join(
                        line.replace("Window Grid", "this").replace(window_grid_mod.DEFAULT_HOTKEY, "its previous value")
                        for line in failed
                    ),
                )
        return not failed

    def _on_sett_window_keys_enabled(self, enabled: bool) -> None:
        for btn in getattr(self, "_sett_winkey_btns", {}).values():
            if isValid(btn):
                btn.setEnabled(bool(enabled))
        if enabled:
            self._apply_window_keys()
        else:
            self._unregister_window_keys()

    def _on_sett_window_key_changed(self, setting_key: str, sequence: str, btn) -> None:
        if isValid(btn):
            QTimer.singleShot(0, btn.clearFocus)
        actions.save_ui_setting(setting_key, sequence)
        self._apply_window_keys()

    def _unregister_kill_hotkey(self) -> None:
        hwnd = getattr(self, "_kill_hotkey_hwnd", 0)
        if hwnd:
            window_grid_mod.unregister_hotkey(hwnd, window_grid_mod.KILL_ALL_HOTKEY_ID)
        self._kill_hotkey_hwnd = 0

    def _apply_kill_hotkey(self, show_error: bool = True) -> bool:
        self._unregister_kill_hotkey()
        settings = actions.load_ui_settings()
        if not settings.get("kill_all_hotkey_enabled", False):
            return False
        sequence = str(settings.get("kill_all_hotkey", "Ctrl+Shift+K") or "Ctrl+Shift+K")
        window_handle = int(self.winId())
        result = window_grid_mod.register_hotkey(
            window_handle, sequence, window_grid_mod.KILL_ALL_HOTKEY_ID
        )
        if result:
            self._kill_hotkey_hwnd = window_handle
            print(f"[INFO] Kill All Roblox shortcut: {sequence}")
            return True
        print(f"[WARNING] Kill All shortcut {sequence} could not be registered: {result.message}")
        if show_error:
            _show_error(
                self, "Shortcut Not Available",
                f"{sequence} could not be used as the Kill All shortcut.\n\n"
                f"{result.message.replace('Window Grid', 'Kill All').replace(window_grid_mod.DEFAULT_HOTKEY, 'its previous value')}",
            )
        return False

    # Performance (FPS cap / graphics) ---------------------------------------
    def _pre_launch_settings(self):
        """Runs before every launch: saved Roblox customizations, then Performance."""
        result = roblox_settings_mod.apply_saved_customizations()
        if result is not None and not result:
            return result
        settings = actions.load_ui_settings()
        note = window_size_mod.write_launch_size()
        if note:
            print(f"[Window Size] {note}")
        if settings.get("perf_apply_on_launch", True) and roblox_settings_mod.performance_values(settings):
            perf = roblox_settings_mod.apply_performance(settings)
            if not perf:
                # Never block a launch over this; just report it.
                print(f"[WARNING] Performance settings were not applied: {perf.message}")
            elif (perf.data or {}).get("changed"):
                print(f"[INFO] Performance applied before launch: {perf.data['changed']}")
        return result

    def _update_perf_status(self) -> None:
        label = getattr(self, "_perf_status_lbl", None)
        if label is None or not isValid(label):
            return
        wanted = roblox_settings_mod.performance_values(actions.load_ui_settings())
        if not wanted:
            label.setText("Nothing selected. Roblox uses its own FPS and graphics settings.")
            return
        parts = []
        if "FramerateCap" in wanted:
            parts.append(f"{wanted['FramerateCap']} FPS")
        if "SavedQualityLevel" in wanted:
            parts.append(f"Graphics {wanted['SavedQualityLevel']}")
        when = "on every launch" if actions.get_ui_setting("perf_apply_on_launch", True) else "when you press Apply now"
        label.setText(f"Will use {', '.join(parts)} {when}.")

    def _on_perf_apply_now(self) -> None:
        settings = actions.load_ui_settings()
        if not roblox_settings_mod.performance_values(settings):
            self._perf_status_lbl.setText("Tick Limit FPS or Set Graphics Quality first.")
            return
        self._perf_apply_btn.setEnabled(False)
        self._perf_status_lbl.setText("Applying…")

        def _worker():
            self._bridge.perf_applied.emit(roblox_settings_mod.apply_performance(settings))

        threading.Thread(target=_worker, daemon=True, name="PerfApply").start()

    def _on_perf_applied(self, result) -> None:
        if not isValid(self._perf_apply_btn):
            return
        self._perf_apply_btn.setEnabled(True)
        if result:
            changed = (result.data or {}).get("changed") or {}
            text = "Applied." if changed else "Already set."
            self._perf_status_lbl.setText(
                f"{text} New Roblox windows will use it; open ones keep their settings until relaunched."
            )
        else:
            self._perf_status_lbl.setText(f"Could not apply: {result.message}")

    def _sync_kill_toggle_btn(self, on: bool) -> None:
        btn = getattr(self, "_sett_kill_toggle_btn", None)
        if btn is None or not isValid(btn):
            return
        btn.blockSignals(True)
        btn.setChecked(bool(on))
        btn.blockSignals(False)
        btn.setText("ON" if on else "OFF")
        btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BRIGHT if on else INPUT}; color: {ON_ACCENT if on else TEXT};"
            f" border: 1px solid {ACCENT_BRIGHT if on else LINE}; font-weight: 700; font-size: 12px; }}"
        )
        key = str(actions.get_ui_setting("kill_all_hotkey", "Ctrl+Shift+K") or "Ctrl+Shift+K")
        lbl = self._sett_kill_state_lbl
        lbl.setText(f"Press {key} to kill all Roblox" if on else "Shortcut is off")
        lbl.setStyleSheet(f"color: {ACCENT_BRIGHT if on else MUTED}; font-size: 11px;")

    def _toggle_kill_hotkey_from_menu(self) -> None:
        enabled = not bool(actions.get_ui_setting("kill_all_hotkey_enabled", False))
        actions.save_ui_setting("kill_all_hotkey_enabled", enabled)
        chk = getattr(self, "_sett_kill_key_chk", None)
        if chk is not None and isValid(chk):
            chk.setChecked(enabled)  # drives the same path as the Settings button
        else:
            self._on_sett_kill_hotkey_enabled(enabled)
        print(f"[INFO] Kill All shortcut {'ON' if enabled else 'OFF'}")

    def _show_kill_button_menu(self, pos) -> None:
        enabled = bool(actions.get_ui_setting("kill_all_hotkey_enabled", False))
        key = str(actions.get_ui_setting("kill_all_hotkey", "Ctrl+Shift+K") or "Ctrl+Shift+K")
        menu = QMenu(self)
        act = menu.addAction(
            f"Turn shortcut OFF ({key})" if enabled else f"Turn shortcut ON ({key})"
        )
        if menu.exec(self._kill_roblox_btn.mapToGlobal(pos)) == act:
            self._toggle_kill_hotkey_from_menu()

    def _on_sett_kill_hotkey_enabled(self, enabled: bool) -> None:
        if hasattr(self, "_sett_kill_key_btn"):
            self._sett_kill_key_btn.setEnabled(bool(enabled))
        if enabled:
            self._apply_kill_hotkey()
        else:
            self._unregister_kill_hotkey()

    def _on_sett_kill_hotkey_changed(self, sequence: str) -> None:
        # Drop focus so the shortcut key (e.g. Space) can't "click" the picker again.
        btn = getattr(self, "_sett_kill_key_btn", None)
        if btn is not None and isValid(btn):
            QTimer.singleShot(0, btn.clearFocus)
        actions.save_ui_setting("kill_all_hotkey", sequence)
        self._apply_kill_hotkey()
        self._sync_kill_toggle_btn(bool(actions.get_ui_setting("kill_all_hotkey_enabled", False)))

    def _unregister_window_grid_hotkey(self) -> None:
        if self._window_grid_hotkey_registered:
            window_grid_mod.unregister_hotkey(
                self._window_grid_hotkey_hwnd
            )
        self._window_grid_hotkey_registered = False
        self._window_grid_hotkey_hwnd = 0

    def _apply_window_grid_hotkey(self, show_error: bool = True) -> bool:
        self._unregister_window_grid_hotkey()
        settings = actions.load_ui_settings()
        if not settings.get("window_grid_enabled", False):
            return True

        sequence = str(
            settings.get(
                "window_grid_keybind",
                window_grid_mod.DEFAULT_HOTKEY,
            )
            or window_grid_mod.DEFAULT_HOTKEY
        )
        window_handle = int(self.winId())
        result = window_grid_mod.register_hotkey(window_handle, sequence)
        if result:
            self._window_grid_hotkey_registered = True
            self._window_grid_hotkey_hwnd = window_handle
            print(f"[Window Grid] Keybind enabled: {sequence}")
            return True

        print(f"[Window Grid] {result.code}: {result.message}")
        if result.code == "WINDOW_GRID_KEYBIND_INVALID":
            invalid_result = result
            default_sequence = window_grid_mod.DEFAULT_HOTKEY
            actions.save_ui_setting(
                "window_grid_keybind",
                default_sequence,
            )
            if hasattr(self, "_sett_window_grid_key_btn"):
                self._sett_window_grid_key_btn.set_sequence(
                    default_sequence
                )

            result = window_grid_mod.register_hotkey(
                window_handle,
                default_sequence,
            )
            if result:
                self._window_grid_hotkey_registered = True
                self._window_grid_hotkey_hwnd = window_handle
                print(
                    f"[Window Grid] Invalid keybind reset to "
                    f"{default_sequence}."
                )
                if show_error:
                    self._show_operation_error(invalid_result)
                return True

        actions.save_ui_setting("window_grid_enabled", False)
        if hasattr(self, "_sett_window_grid_chk"):
            self._sett_window_grid_chk.blockSignals(True)
            self._sett_window_grid_chk.setChecked(False)
            self._sett_window_grid_chk.blockSignals(False)
        if hasattr(self, "_sett_window_grid_key_btn"):
            self._sett_window_grid_key_btn.setEnabled(False)
        if show_error:
            self._show_operation_error(result)
        return False

    def _on_sett_window_grid(self, enabled: bool) -> None:
        if hasattr(self, "_sett_window_grid_key_btn"):
            self._sett_window_grid_key_btn.setEnabled(enabled)
        if enabled:
            self._apply_window_grid_hotkey()
        else:
            self._unregister_window_grid_hotkey()
            print("[Window Grid] Keybind disabled.")

    def _restore_window_grid_hotkey_after_recording(self) -> None:
        if actions.load_ui_settings().get("window_grid_enabled", False):
            self._apply_window_grid_hotkey(show_error=False)

    def _on_sett_window_grid_keybind(self, sequence: str) -> None:
        actions.save_ui_setting("window_grid_keybind", sequence)
        if actions.load_ui_settings().get("window_grid_enabled", False):
            self._apply_window_grid_hotkey()

    _HM_HIDDEN_COLOR = "#4CAF50"
    _HM_SHOWN_COLOR = "#EF5350"

    def _on_sett_headless_manager(self, enabled: bool):
        if enabled:
            self._start_headless_manager()
        else:
            self._stop_headless_manager()

    def _on_sett_headless_interval(self, value: int) -> None:
        actions.save_ui_setting(headless_manager_mod.SCAN_INTERVAL_SETTING, int(value))
        if self._headless_manager is not None:
            self._headless_manager.set_scan_interval(value)

    def _start_headless_manager(self) -> None:
        if self._headless_manager is not None:
            return
        self._headless_manager = headless_manager_mod.HeadlessManager(
            on_update=lambda rows: self._bridge.headless_update.emit(rows),
            scan_interval=headless_manager_mod.get_scan_interval(actions.load_ui_settings()),
        )
        self._headless_manager.start()
        print("[INFO] Headless Manager started.")

    def _stop_headless_manager(self) -> None:
        if self._headless_manager is None:
            return
        self._headless_manager.stop(restore=True)
        self._headless_manager = None
        self._headless_latest_rows = []
        if hasattr(self, "_headless_list"):
            self._refresh_headless_list([])
        print("[INFO] Headless Manager stopped, Roblox windows restored.")

    def _headless_selected_pid(self) -> int | None:
        item = self._headless_list.currentItem() if self._headless_list else None
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _headless_selected_pids(self) -> list[int]:
        if not self._headless_list:
            return []
        pids = []
        for it in self._headless_list.selectedItems():
            pid = it.data(Qt.ItemDataRole.UserRole)
            if pid:
                pids.append(pid)
        return pids

    def _on_headless_update(self, rows: list[dict]):
        if self._headless_manager is None:
            return
        self._headless_latest_rows = [dict(row) for row in rows]
        if hasattr(self, "_headless_list"):
            self._refresh_headless_list(rows)

    def _refresh_headless_list(self, rows: list[dict]):
        # The scan repeats every few seconds; rebuilding every row widget (and
        # re-requesting avatars) when nothing changed is what makes the window
        # sluggish with many Roblox clients open.
        signature = (
            bool(self._headless_manager),
            tuple((r.get("pid"), r.get("username"), r.get("hidden")) for r in rows),
        )
        if signature == getattr(self, "_headless_list_signature", None) and self._headless_list.count():
            return
        self._headless_list_signature = signature
        cur = self._headless_selected_pid()
        selected = set(self._headless_selected_pids())
        self._headless_list.clear()
        self._headless_avatar_labels.clear()
        self._headless_status_labels.clear()

        AV = avatars.AVATAR_SIZE
        ITEM_H = AV + 6

        if not rows:
            empty = QListWidgetItem(
                "No Roblox processes found." if self._headless_manager else
                "Headless Manager is disabled."
            )
            empty.setForeground(QColor(MUTED))
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._headless_list.addItem(empty)
            return

        for row_data in rows:
            pid = row_data["pid"]
            username = row_data["username"]
            hidden = row_data["hidden"]

            item = QListWidgetItem("")
            item.setSizeHint(QSize(0, ITEM_H))
            item.setData(Qt.ItemDataRole.UserRole, pid)

            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(4, 0, 6, 0)
            row_lay.setSpacing(6)

            av_lbl = QLabel()
            av_lbl.setFixedSize(AV, AV)
            av_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
            av_lbl.setPixmap(self._make_placeholder_pixmap(AV))
            row_lay.addWidget(av_lbl)
            self._headless_avatar_labels[pid] = av_lbl

            name_lbl = QLabel(username)
            name_lbl.setObjectName("accountName")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            row_lay.addWidget(name_lbl)

            sep = QLabel("|")
            sep.setObjectName("noteSep")
            sep.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            row_lay.addWidget(sep)

            status_str = "hidden" if hidden else "shown"
            status_color = self._HM_HIDDEN_COLOR if hidden else self._HM_SHOWN_COLOR
            status_lbl = QLabel(status_str)
            status_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            status_lbl.setStyleSheet(f"color: {status_color}; font-size: 10px;")
            row_lay.addWidget(status_lbl)
            self._headless_status_labels[pid] = status_lbl

            row_lay.addStretch(1)

            row.setFixedHeight(ITEM_H)
            self._headless_list.addItem(item)
            self._headless_list.setItemWidget(item, row)

        for i in range(self._headless_list.count()):
            it = self._headless_list.item(i)
            pid = it.data(Qt.ItemDataRole.UserRole) if it else None
            if pid in selected:
                it.setSelected(True)
            if cur and pid == cur:
                self._headless_list.setCurrentItem(it)

        self._headless_load_avatars_async(rows)

    def _headless_load_avatars_async(self, rows: list[dict]):
        by_pid = {r["pid"]: r for r in rows}
        for pid, label in list(self._headless_avatar_labels.items()):
            row_data = by_pid.get(pid)
            if not row_data:
                continue
            user_id = row_data.get("user_id")
            username = row_data.get("username")
            if not user_id:
                continue
            avatars.fetch_avatar_async(
                user_id, username,
                on_done=lambda u, b, p=pid: self._bridge.headless_avatar_ready.emit(p, b),
            )

    def _on_headless_avatar_ready(self, pid: int, img_bytes: object):
        try:
            pix = self._make_circular_pixmap(bytes(img_bytes), avatars.AVATAR_SIZE)
            if pix.isNull():
                return
            lbl = self._headless_avatar_labels.get(pid)
            if lbl is not None:
                lbl.setPixmap(pix)
        except Exception:
            pass

    def _headless_set_status_label(self, pid: int, hidden: bool) -> None:
        lbl = self._headless_status_labels.get(pid)
        if lbl is None:
            return
        lbl.setText("hidden" if hidden else "shown")
        color = self._HM_HIDDEN_COLOR if hidden else self._HM_SHOWN_COLOR
        lbl.setStyleSheet(f"color: {color}; font-size: 10px;")

    def _headless_on_context_menu(self, pos):
        item = self._headless_list.itemAt(pos)
        if item is None or self._headless_manager is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if not pid:
            return

        if pid not in self._headless_selected_pids():
            self._headless_list.setCurrentItem(item)

        pids = self._headless_selected_pids()
        any_shown = any(not self._headless_manager.is_hidden(p) for p in pids)
        any_hidden = any(self._headless_manager.is_hidden(p) for p in pids)

        menu = QMenu(self)
        act_hide = menu.addAction("Hide") if any_shown else None
        act_show = menu.addAction("Show") if any_hidden else None

        chosen = menu.exec(self._headless_list.mapToGlobal(pos))
        if act_hide and chosen == act_hide:
            for p in pids:
                self._headless_manager.set_hidden(p, True)
                self._headless_set_status_label(p, True)
        elif act_show and chosen == act_show:
            for p in pids:
                self._headless_manager.set_hidden(p, False)
                self._headless_set_status_label(p, False)

    def _start_update_check(self) -> None:
        # Always runs: updates are required (the old "check on startup" switch is ignored).
        def _worker():
            result = updater_mod.get_latest_release()
            if not result:
                print(f"[INFO] Update check failed: {result.detail}")
                return
            latest = result.data["version"]
            # No skipping: a newer version is offered on every start until installed.
            if updater_mod.is_newer(APP_VERSION, latest):
                self._latest_release = result.data
                self._bridge.update_available.emit(latest)
        threading.Thread(target=_worker, daemon=True, name="UpdateCheck").start()

    def _on_check_updates_now(self) -> None:
        self._sett_update_now_btn.setEnabled(False)
        self._sett_update_now_btn.setText("Checking...")

        def _worker():
            self._bridge.update_check_finished.emit(updater_mod.get_latest_release())

        threading.Thread(target=_worker, daemon=True, name="UpdateCheckNow").start()

    def _on_update_check_finished(self, result) -> None:
        self._sett_update_now_btn.setEnabled(True)
        self._sett_update_now_btn.setText("Check for Updates Now")
        if not result:
            self._show_operation_error(result)
            return
        latest = result.data["version"]
        if updater_mod.is_newer(APP_VERSION, latest):
            self._latest_release = result.data
            self._show_update_dialog(latest)
        else:
            _show_info(self, "Up to Date", f"You are running the latest version (v{APP_VERSION}).")

    def _offer_secure_settings_reset(self) -> None:
        """Data copied from another PC: settings an older version locked to that PC."""
        if not getattr(self.manager, "secure_settings_unavailable", False):
            return
        reply = QMessageBox.question(
            self, "Settings from another PC",
            "Your accounts opened fine, but a few saved settings (backup password, auto-backup key, "
            "WebSocket password) were locked to the PC they came from by an older version, so they "
            "can't be used here and backups can't be made.\n\n"
            "Reset them on this PC? Your accounts are not touched. You can set a backup password "
            "again in Settings → Misc.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            result = self.manager.reset_unavailable_secure_settings()
            if not result:
                self._show_operation_error(result)

    def _on_update_available(self, latest_version: str) -> None:
        self._show_update_dialog(latest_version)

    def _show_update_dialog(self, latest_version: str) -> None:
        release = getattr(self, "_latest_release", None) or {}
        notes = release.get("notes", "") if release.get("version") == latest_version else ""
        # Required update: no "Later", no close button, Esc does nothing, and
        # closing the window some other way quits the app.
        dlg = _RequiredUpdateDialog(self)
        dlg.on_close_attempt = lambda: QTimer.singleShot(0, getattr(self, "_quit_for_update", lambda: None))
        dlg.setWindowTitle("Update Required")
        dlg.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        dlg.setFixedSize(440, 420 if notes else 270)
        dlg.setStyleSheet(f"""
            QDialog   {{ background: {BG}; }}
            QLabel    {{ color: {TEXT}; background: transparent; }}
            QPushButton {{
                background: {INPUT}; color: {TEXT};
                border: 1px solid {LINE}; border-radius: 0;
                padding: 6px 14px; font-size: 12px;
            }}
            QPushButton:hover    {{ background: {SELECT}; border-color: {HOVER}; }}
            QPushButton:disabled {{ color: {MUTED}; }}
        """)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        # Header
        hdr = QLabel("Update Required")
        hdr.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT};")
        lay.addWidget(hdr)

        # Version Info Card
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {PANEL}; border: none; }}")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 10, 14, 10)
        card_lay.setSpacing(4)
        lbl_cur = QLabel(f"Your version:  v{APP_VERSION}  (no longer supported)")
        lbl_cur.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lbl_new = QLabel(f"Latest version:  v{latest_version}")
        lbl_new.setStyleSheet(f"color: {ACCENT_TEXT}; font-size: 13px; font-weight: 600;")
        card_lay.addWidget(lbl_cur)
        card_lay.addWidget(lbl_new)
        lay.addWidget(card)

        if notes:
            notes_view = QTextEdit()
            notes_view.setReadOnly(True)
            notes_view.setPlainText(notes)
            notes_view.setAccessibleName("Release notes")
            notes_view.setStyleSheet(
                f"QTextEdit {{ background: {INPUT}; color: {TEXT}; border: 1px solid {LINE};"
                f" font-size: 11px; }}"
            )
            lay.addWidget(notes_view, 1)

        # Progress bar / Retry button (only clickable after a failed download)
        dl_btn = QPushButton("Downloading...  0%")
        dl_btn.setFixedHeight(34)
        dl_btn.setEnabled(False)
        lay.addWidget(dl_btn)

        # Status label
        status_lbl = QLabel("The update downloads now. The app restarts when it's installed.")
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(status_lbl)

        # Helpers for the chromium-style progress bar
        def _set_progress(pct: int) -> None:
            pct = max(0, min(100, pct))
            dl_btn.setText(f"Downloading...  {pct}%")
            if pct == 0:
                dl_btn.setStyleSheet("")
            else:
                a = f"{pct / 100:.4f}"
                b = f"{min(pct / 100 + 0.001, 1.0):.4f}"
                dl_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background: qlineargradient("
                    f"    x1:0, y1:0, x2:1, y2:0,"
                    f"    stop:0 {ACCENT_SOFT}, stop:{a} {ACCENT_SOFT},"
                    f"    stop:{b} {INPUT}, stop:1 {INPUT}"
                    f"  );"
                    f"  color: {TEXT}; border: 1px solid {LINE}; border-radius: 4px;"
                    f"}}"
                )

        def _set_buttons_enabled(enabled: bool) -> None:
            dl_btn.setEnabled(enabled)

        # Download signal connections
        def _on_progress(pct: int) -> None:
            _set_progress(pct)

        def _on_done(success: bool, err: str) -> None:
            try:
                self._bridge.update_progress.disconnect(_on_progress)
                self._bridge.update_done.disconnect(_on_done)
            except RuntimeError:
                pass
            if success:
                # Wait for the user: OK closes the app, the update installs and
                # the new version opens (closing the window does the same).
                dl_btn.setText("OK")
                dl_btn.setStyleSheet(
                    f"QPushButton {{ background: #1E4D1E; color: {TEXT}; font-weight: 700; "
                    f"border: 1px solid #2E6D2E; border-radius: 4px; }}"
                    f"QPushButton:hover {{ background: #286128; }}"
                )
                status_lbl.setText(f"Update ready. Click OK to restart into v{latest_version}.")
                try:
                    dl_btn.clicked.disconnect()
                except (RuntimeError, TypeError):
                    pass

                def _restart() -> None:
                    dl_btn.setEnabled(False)
                    dl_btn.setText("Restarting...")
                    dlg._allow_close = True
                    QTimer.singleShot(200, self._quit_for_update)
                dl_btn.clicked.connect(_restart)
                dl_btn.setEnabled(True)
                dl_btn.setFocus()
            else:
                _set_buttons_enabled(True)
                dl_btn.setText("Retry")
                dl_btn.setStyleSheet("")
                status_lbl.setText(f"Download failed: {err}\n"
                                   "Check your internet connection, then press Retry.")

        # Button actions
        def _on_download_clicked() -> None:
            if not isValid(dl_btn) or getattr(self, "_bridge", None) is None:
                return
            _set_buttons_enabled(False)
            _set_progress(0)
            status_lbl.setText("Starting download...")
            try:
                self._bridge.update_progress.disconnect()
            except RuntimeError:
                pass
            try:
                self._bridge.update_done.disconnect()
            except RuntimeError:
                pass
            self._bridge.update_progress.connect(_on_progress)
            self._bridge.update_done.connect(_on_done)
            updater_mod.download_update(
                on_progress=lambda p: self._bridge.update_progress.emit(p),
                on_done=lambda ok, e: self._bridge.update_done.emit(ok, e),
            )

        dl_btn.clicked.connect(_on_download_clicked)
        QTimer.singleShot(0, dlg, _on_download_clicked)  # starts by itself

        dlg.exec()

    # Cookie Validator
    def _start_cookie_validator(self) -> None:
        if self._cv_validator is not None:
            return
        settings = actions.load_ui_settings()
        if not self._cv_mod.validation_enabled(settings):
            print("[INFO] Cookie validation on startup is turned off.")
            return
        self._cv_validator = self._cv_mod.CookieValidator(
            self.manager,
            on_result=lambda u, status: self._bridge.cookie_validated.emit(
                u,
                status,
            ),
            delay_sec=self._cv_mod.get_validation_delay(settings),
        )
        self._cv_validator.start()

    def _on_cookie_validated(self, username: str, status: str) -> None:
        self._apply_cookie_status_to_row(
            username,
            status == self._cv_mod.INVALID,
        )

    def _apply_cookie_status_to_row(self, username: str, flagged: bool) -> None:
        badge = self._invalid_badges.get(username)
        if badge is None and flagged:
            container = self._account_avatar_containers.get(username)
            if container is not None:
                badge = self._create_invalid_badge(container)
                self._invalid_badges[username] = badge
        if badge is not None:
            badge.setVisible(flagged)
        name_label = self._account_name_labels.get(username)
        if name_label is not None:
            name_label.setStyleSheet(
                "color: #E8A020; font-style: italic;" if flagged else ""
            )
            name_label.setToolTip(
                "Cookie validation received repeated unauthorized responses.\n"
                "You can still try launching this account."
                if flagged else ""
            )
        row = self._account_rows.get(username)
        if row is not None:
            row.setStyleSheet(
                "QWidget { background: rgba(200, 50, 50, 0.06); }"
                if flagged else ""
            )

    def _is_account_invalid(self, username: str) -> bool:
        data = self.manager.accounts.get(username)
        return self._cv_mod.is_flagged(data) if isinstance(data, dict) else False

    def _guard_invalid(self, usernames: list[str]) -> bool:
        bad = [u for u in usernames if self._is_account_invalid(u)]
        if not bad:
            return True
        names = ", ".join(bad)
        reply = QMessageBox.question(
            self,
            "Cookie May Be Invalid",
            f"The following account(s) received repeated unauthorized "
            f"responses during cookie validation:\n\n  {names}\n\n"
            "Would you like to try launching anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _start_presence_scanner(self) -> None:
        if self._presence_scanner is not None:
            return
        self._presence_scanner = self._presence_mod.PresenceScanner(
            self.manager,
            on_update=lambda snapshot: self._bridge.presence_update.emit(snapshot),
            interval_sec=5,
        )
        self._presence_scanner.start()
        print("[INFO] Account Activity Monitor started.")

    def _stop_presence_scanner(self) -> None:
        if self._presence_scanner is None:
            self._online_usernames = set()
            self._activity_snapshot = {}
            self._update_presence_dots()
            self._update_activity_rows()
            return
        self._presence_scanner.stop()
        self._presence_scanner = None
        self._online_usernames = set()
        self._activity_snapshot = {}
        self._update_presence_dots()
        self._update_activity_rows()
        print("[INFO] Account Activity Monitor stopped.")

    def _on_presence_update(self, snapshot: object) -> None:
        if isinstance(snapshot, dict):
            self._activity_snapshot = {
                str(username): dict(metrics)
                for username, metrics in snapshot.items()
                if isinstance(metrics, dict)
            }
        else:
            self._activity_snapshot = {}
        self._online_usernames = set(self._activity_snapshot)
        self._update_presence_dots()
        self._update_activity_rows()

    def _update_presence_dots(self) -> None:
        for username, dot in self._presence_dots.items():
            dot.setVisible(username in self._online_usernames)
        for username, (dot, text) in getattr(self, "_status_labels", {}).items():
            if isValid(dot) and isValid(text):
                self._apply_account_status(dot, text, username in self._online_usernames)

    @staticmethod
    def _apply_account_status(dot: QLabel, text: QLabel, online: bool) -> None:
        color = ONLINE_GREEN if online else "#6B7280"
        dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        text.setText("In game" if online else "Idle")
        text.setStyleSheet(f"color: {ONLINE_GREEN if online else MUTED};")

    _TOTAL_RAM_MB = None

    @classmethod
    def _total_ram_mb(cls) -> float:
        if cls._TOTAL_RAM_MB is None:
            try:
                cls._TOTAL_RAM_MB = psutil.virtual_memory().total / 1024 / 1024
            except Exception:
                cls._TOTAL_RAM_MB = 0.0
        return cls._TOTAL_RAM_MB

    @staticmethod
    def _format_mb(mb: float) -> str:
        return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"

    def _update_ram_total(self) -> None:
        count = getattr(self, "_last_running_count", None)
        if count is not None:
            self._on_running_count(count, from_poll=False)

    def _running_header_text(self, count: int) -> tuple[str, str]:
        """Header next to "Account List": running windows, then the whole PC's RAM and CPU."""
        text = f"{count} running"
        tip = "Roblox windows running on this PC"
        settings = actions.load_ui_settings()
        if settings.get("presence_indicator", False):
            running = [m for m in self._activity_snapshot.values() if m.get("ram_available")]
            if running:
                total = sum(float(m.get("ram_mb", 0.0) or 0.0) for m in running)
                tip += f"\nRAM used by the running accounts: {self._format_mb(total)}"
        usage = getattr(self, "_pc_usage", None)
        if usage and settings.get(PC_USAGE_SETTING, settings.get("ram_show_total", True)):
            used_mb, total_mb, cpu = usage
            ram_pct = used_mb / total_mb * 100 if total_mb else 0.0
            ram = f"RAM {used_mb / 1024:.1f} / {total_mb / 1024:.0f} GB ({ram_pct:.0f}%)"
            cpu_text = f"CPU {cpu:.0f}%"
            if ram_pct >= PC_RAM_WARN:
                ram = f'<span style="color:{PC_WARN_COLOR};">{ram}</span>'
            if cpu >= PC_CPU_WARN:
                cpu_text = f'<span style="color:{PC_WARN_COLOR};">{cpu_text}</span>'
            text += f' <span style="color:{MUTED};">·</span> {ram} <span style="color:{MUTED};">·</span> {cpu_text}'
            tip += "\nWhole PC: memory in use and CPU load (like Task Manager). Orange means it's near the limit."
        return text, tip

    def _start_pc_usage_timer(self) -> None:
        """Whole-PC RAM/CPU, refreshed every second like Task Manager.

        Reading the counters takes well under a millisecond, so it runs on the
        UI thread; the slower Roblox window count keeps its own 2 s timer.
        """
        self._pc_usage_reader = pc_usage_mod.PcUsageReader()
        print(f"[INFO] PC usage: CPU from {self._pc_usage_reader.source}")
        self._pc_usage_timer = QTimer(self)
        self._pc_usage_timer.setInterval(PC_USAGE_INTERVAL_MS)
        self._pc_usage_timer.timeout.connect(self._tick_pc_usage)
        self._pc_usage_timer.start()
        QTimer.singleShot(250, self._tick_pc_usage)  # first real number right away

    def _tick_pc_usage(self) -> None:
        reader = getattr(self, "_pc_usage_reader", None)
        if reader is None:
            return
        self._pc_usage = reader.read()
        count = getattr(self, "_last_running_count", None)
        if count is not None:
            self._on_running_count(count, from_poll=False)

    def _start_running_counter(self) -> None:
        self._running_polling = False
        self._running_timer = QTimer(self)
        self._running_timer.setInterval(2000)
        self._running_timer.timeout.connect(self._poll_running_count)
        self._bridge.roblox_count_ready.connect(self._on_running_count)
        self._running_timer.start()
        QTimer.singleShot(300, self._poll_running_count)

    def _poll_running_count(self) -> None:
        if getattr(self, "_running_polling", False):
            return
        self._running_polling = True

        def _worker():
            try:
                headless = headless_manager_mod.get_active_manager()
                hidden = headless.get_hidden_pids() if headless is not None else set()
                count = len(presence_mod.game_window_pids(hidden))
            except Exception:
                count = -1
            try:
                self._bridge.roblox_count_ready.emit(count)
            except RuntimeError:
                pass  # window already closed

        threading.Thread(target=_worker, daemon=True, name="RunningCount").start()

    def _on_running_count(self, count: int, from_poll: bool = True) -> None:
        if from_poll:
            self._running_polling = False
        label = getattr(self, "_running_lbl", None)
        if label is None or not isValid(label) or count < 0:
            return
        self._last_running_count = count
        text, tip = self._running_header_text(count)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(text)
        label.setToolTip(tip)
        style = f"color: {ONLINE_GREEN if count else MUTED};"
        if label.styleSheet() != style:
            label.setStyleSheet(style)

    def _update_activity_rows(self) -> None:
        show_percent = bool(actions.get_ui_setting("ram_show_percent", False))
        pc_total = self._total_ram_mb() if show_percent else 0.0
        self._update_ram_total()
        for username, container in self._activity_widgets.items():
            metrics = self._activity_snapshot.get(username)
            if not metrics:
                container.setVisible(False)
                continue

            ram_mb = float(metrics.get("ram_mb", 0.0) or 0.0)
            cpu_percent = float(metrics.get("cpu_percent", 0.0) or 0.0)
            ram_text = (
                f"{ram_mb:.0f} MB"
                if metrics.get("ram_available", False)
                else "N/A"
            )
            if pc_total and metrics.get("ram_available", False):
                ram_text += f" ({ram_mb / pc_total * 100:.0f}%)"
            cpu_text = (
                f"{cpu_percent:.1f}%"
                if metrics.get("cpu_available", False)
                else "N/A"
            )
            labels = self._activity_labels.get(username)
            if labels is not None:
                ram_label, cpu_label = labels
                if ram_label.text() != ram_text:
                    ram_label.setText(ram_text)
                if cpu_label.text() != cpu_text:
                    cpu_label.setText(cpu_text)
            container.setVisible(True)

    # RAM boost background worker
    def _start_ram_boost(self):
        if getattr(self, "_ram_boost_running", False):
            return
        self._ram_boost_running = True
        self._ram_boost_stop = False
        def _worker():
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            print("[INFO] RAM boost started")
            while not self._ram_boost_stop:
                try:
                    limit_mb = int(actions.load_ui_settings().get("optimize_roblox_ram_limit_mb", RAM_LIMIT_DEFAULT_MB))
                    current_pids = set()
                    for proc in psutil.process_iter(["pid", "name"]):
                        if proc.info["name"] and proc.info["name"].lower() == "robloxplayerbeta.exe":
                            current_pids.add(proc.info["pid"])
                    for pid in current_pids:
                        try:
                            mem_mb = psutil.Process(pid).memory_info().rss / 1024 / 1024
                            if mem_mb >= limit_mb:
                                h = kernel32.OpenProcess(0x1F0FFF, False, pid)
                                if h:
                                    psapi.EmptyWorkingSet(h)
                                    kernel32.CloseHandle(h)
                                    print(f"[INFO] Trimmed RAM for Roblox PID {pid} ({mem_mb:.0f} MB)")
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[ERROR] RAM boost error: {e}")
                time.sleep(15)
            self._ram_boost_running = False
            print("[INFO] RAM boost stopped")
        threading.Thread(target=_worker, daemon=True, name="RamBoost").start()

    def _stop_ram_boost(self):
        self._ram_boost_stop = True

    # Rename Roblox Windows worker
    def _start_rename_windows(self):
        if self._window_renamer is not None:
            return
        self._window_renamer = window_renamer_mod.RobloxWindowRenamer(
            self.manager,
            title_mode=actions.get_ui_setting(
                "rename_roblox_windows_mode",
                "username",
            ),
        )
        self._window_renamer.start()

    def _stop_rename_windows(self):
        renamer = self._window_renamer
        self._window_renamer = None
        if renamer is not None:
            renamer.stop()

    def _on_sett_browse_launcher(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Launcher", "", "Executables (*.exe);;All files (*)"
        )
        if path:
            self._sett_custom_launcher_edit.setText(path)
            actions.save_ui_setting(
                "custom_roblox_launcher_path",
                path,
            )

    def _on_sett_roblox_downloader_customizations(self, enabled: bool):
        if hasattr(self, "_sett_roblox_downloader_version_edit"):
            self._sett_roblox_downloader_version_edit.setEnabled(enabled)
        if hasattr(self, "_sett_roblox_downloader_location_edit"):
            self._sett_roblox_downloader_location_edit.setEnabled(enabled)
        if hasattr(self, "_sett_roblox_downloader_browse_btn"):
            self._sett_roblox_downloader_browse_btn.setEnabled(enabled)

    def _on_sett_browse_roblox_download_path(self):
        current_path = self._sett_roblox_downloader_location_edit.text().strip()
        if not current_path:
            current_path = roblox_downloader_mod.get_default_versions_path()
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Roblox Versions Folder",
            current_path,
        )
        if path:
            self._sett_roblox_downloader_location_edit.setText(path)

    def _on_sett_download_roblox(self):
        button = self._sett_roblox_downloader_btn
        customizations_enabled = (
            self._sett_roblox_downloader_custom_chk.isChecked()
        )
        version_value = self._sett_roblox_downloader_version_edit.text()
        location_path = self._sett_roblox_downloader_location_edit.text()

        button.setEnabled(False)
        button.setText("0%")
        self._sett_roblox_downloader_custom_chk.setEnabled(False)
        self._sett_roblox_downloader_version_edit.setEnabled(False)
        self._sett_roblox_downloader_location_edit.setEnabled(False)
        self._sett_roblox_downloader_browse_btn.setEnabled(False)

        def _worker():
            success, result_type, message = roblox_downloader_mod.download_roblox(
                version_value,
                location_path,
                customizations_enabled,
                progress_callback=(
                    lambda percent, text: self._bridge.roblox_download_progress.emit(
                        percent,
                        text,
                    )
                ),
            )
            self._bridge.roblox_download_done.emit(
                success,
                result_type,
                message,
            )

        threading.Thread(
            target=_worker,
            daemon=True,
            name="roblox-downloader",
        ).start()

    def _on_roblox_download_progress(self, percent: int, operation: str):
        if not hasattr(self, "_sett_roblox_downloader_btn"):
            return
        button = self._sett_roblox_downloader_btn
        filled = max(0, min(100, int(percent)))
        button.setText(f"{filled}%")
        button.setToolTip(operation)
        if filled == 0:
            button.setStyleSheet(
                f"QPushButton {{ background: {INPUT}; color: {TEXT}; "
                f"border: 1px solid {LINE}; border-radius: 0; "
                f"text-align: center; }}"
            )
            return

        stop_a = f"{filled / 100:.4f}"
        stop_b = f"{min(filled / 100 + 0.001, 1.0):.4f}"
        button.setStyleSheet(
            f"QPushButton {{"
            f"  background: qlineargradient("
            f"    x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 {ACCENT_SOFT},"
            f"    stop:{stop_a} {ACCENT_SOFT},"
            f"    stop:{stop_b} {INPUT},"
            f"    stop:1 {INPUT}"
            f"  );"
            f"  color: {TEXT}; border: 1px solid {LINE};"
            f"  border-radius: 0; text-align: center;"
            f"}}"
        )

    def _on_roblox_download_done(
        self,
        success: bool,
        result_type: str,
        message: str,
    ):
        if not hasattr(self, "_sett_roblox_downloader_btn"):
            return

        button = self._sett_roblox_downloader_btn
        button.setText("Download Latest Roblox")
        button.setEnabled(True)
        button.setToolTip(
            "Download and extract the selected WindowsPlayer deployment."
        )
        button.setStyleSheet(
            f"QPushButton {{ background: {INPUT}; color: {TEXT}; "
            f"border: 1px solid {LINE}; border-radius: 0; "
            f"text-align: center; }}"
            f"QPushButton:hover {{ background: {SELECT}; }}"
        )

        self._sett_roblox_downloader_custom_chk.setEnabled(True)
        customizations_enabled = (
            self._sett_roblox_downloader_custom_chk.isChecked()
        )
        self._sett_roblox_downloader_version_edit.setEnabled(
            customizations_enabled
        )
        self._sett_roblox_downloader_location_edit.setEnabled(
            customizations_enabled
        )
        self._sett_roblox_downloader_browse_btn.setEnabled(
            customizations_enabled
        )

        if success and result_type == "already_exists":
            QMessageBox.information(
                self,
                "Roblox Downloader",
                "The latest Roblox version has already been downloaded.\n\n"
                f"{message}",
            )
        elif success:
            QMessageBox.information(
                self,
                "Roblox Downloader",
                "Roblox was downloaded successfully.\n\n"
                f"Location:\n{message}",
            )
        else:
            QMessageBox.critical(
                self,
                "Roblox Downloader",
                f"Failed to download Roblox.\n\n{message}",
            )

    def _set_chromium_button_idle(self) -> None:
        if not hasattr(self, "_sett_chromium_btn"):
            return
        installed = bool(chromium_mod.validate_chromium())
        self._sett_chromium_btn.setText(
            "Reinstall Chromium" if installed else "Download Chromium"
        )
        self._sett_chromium_btn.setEnabled(True)
        self._sett_chromium_btn.setStyleSheet("")

    def _start_chromium_status_check(self) -> None:
        chromium_mod.check_chromium_status(
            lambda result: self._bridge.chromium_status.emit(result)
        )

    def _on_chromium_status(self, result) -> None:
        operation_result = ensure_result(
            result,
            failure_code="CHROMIUM_STATUS_FAILED",
            failure_title="Chromium Check Failed",
            failure_message="The latest Chromium version could not be checked.",
        )
        if not operation_result:
            print(
                f"[WARNING] Chromium status check failed: "
                f"{operation_result.code}: {operation_result.detail}"
            )
            return
        if self._chromium_download_active:
            return

        self._chromium_status_result = operation_result
        if not hasattr(self, "_sett_chromium_btn"):
            return

        data = operation_result.data or {}
        installed = bool(data.get("installed"))
        installed_build = str(data.get("installed_build", "") or "")
        latest_build = str(data.get("latest_build", "") or "")
        outdated = bool(data.get("outdated"))

        self._sett_chromium_btn.setText(
            "Reinstall Chromium" if installed else "Download Chromium"
        )
        if installed:
            if outdated:
                detail = (
                    f"Installed snapshot: {installed_build}\n"
                    f"Latest snapshot: {latest_build}\n"
                    "Reinstall Chromium to update it."
                )
            else:
                detail = (
                    f"Installed snapshot: {installed_build or 'Unknown'}\n"
                    f"Latest snapshot: {latest_build}\n"
                    "You can reinstall Chromium with a clean copy."
                )
        else:
            detail = (
                f"Latest snapshot: {latest_build}\n"
                "Download portable Chromium and its matching ChromeDriver."
            )
        self._sett_chromium_btn.setToolTip(detail)

    def _on_chromium_progress(self, pct: int, label: str) -> None:
        if not hasattr(self, "_sett_chromium_btn"):
            return
        btn = self._sett_chromium_btn
        btn.setText(label if label else f"{pct}%")
        filled = max(0, min(100, pct))
        if filled == 0:
            btn.setStyleSheet(
                f"QPushButton {{ background: {INPUT}; color: {TEXT}; "
                f"border: 1px solid {LINE}; text-align: center; }}"
            )
            return

        stop_a = f"{filled / 100:.4f}"
        stop_b = f"{min(filled / 100 + 0.001, 1.0):.4f}"
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: qlineargradient("
            f"    x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 {ACCENT_SOFT},"
            f"    stop:{stop_a} {ACCENT_SOFT},"
            f"    stop:{stop_b} {INPUT},"
            f"    stop:1 {INPUT}"
            f"  );"
            f"  color: {TEXT}; border: 1px solid {LINE}; text-align: center;"
            f"}}"
        )

    def _on_chromium_done(self, result) -> None:
        self._chromium_download_active = False
        operation_result = ensure_result(
            result,
            failure_code="CHROMIUM_DOWNLOAD_FAILED",
            failure_title="Chromium Download Failed",
            failure_message="Chromium could not be downloaded.",
        )
        self._set_chromium_button_idle()
        if operation_result:
            print("[INFO] Chromium download and extraction complete.")
            return

        print(
            f"[ERROR] Chromium download failed: "
            f"{operation_result.code}: {operation_result.detail}"
        )
        self._show_operation_error(operation_result)

    def _on_sett_dl_chromium(self):
        if self._chromium_download_active:
            return

        target_exe = chromium_mod.get_chromium_path()
        action = (
            "Reinstall"
            if bool(chromium_mod.validate_chromium())
            else "Download"
        )
        print(f"[INFO] {action} requested. Target: {target_exe}")

        self._chromium_download_active = True
        self._sett_chromium_btn.setEnabled(False)
        self._sett_chromium_btn.setText("0%")
        print("[INFO] Starting Chromium download thread...")

        chromium_mod.download_chromium(
            lambda percent, label: self._bridge.chromium_progress.emit(
                percent,
                label,
            ),
            lambda result: self._bridge.chromium_done.emit(result),
        )

    def _on_sett_switch_encryption(self):
        method_labels = {"hardware": "Hardware", "password": "Password", "none": "No Encryption"}
        current_method = self.manager.get_encryption_method() or "none"

        other_methods = [m for m in ("hardware", "password", "none") if m != current_method]
        choice, ok = QInputDialog.getItem(
            self, "Switch Encryption Method",
            f"Current method: {method_labels[current_method]}\n"
            "Choose the new encryption method:",
            [method_labels[m] for m in other_methods], 0, False,
        )
        if not ok or not choice:
            return
        new_method = other_methods[[method_labels[m] for m in other_methods].index(choice)]

        new_password = None
        if new_method == "password":
            while True:
                pw1, ok1 = QInputDialog.getText(
                    self, "Set Password", "Enter new password (min. 8 characters):",
                    QLineEdit.EchoMode.Password,
                )
                if not ok1:
                    return
                if len(pw1) < 8:
                    QMessageBox.warning(self, "Invalid Password", "Password must be at least 8 characters.")
                    continue
                pw2, ok2 = QInputDialog.getText(
                    self, "Confirm Password", "Confirm new password:",
                    QLineEdit.EchoMode.Password,
                )
                if not ok2:
                    return
                if pw1 != pw2:
                    QMessageBox.warning(self, "Password Mismatch", "Passwords do not match.")
                    continue
                new_password = pw1
                break

        if new_method == "none":
            reply = QMessageBox.warning(
                self, "No Encryption",
                "Your account data will be stored in plain text.\n"
                "Anyone with access to your files can read your cookies.\n\n"
                "Are you sure you want to continue without encryption?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        else:
            reply = QMessageBox.question(
                self, "Switch Encryption Method",
                f"Switch encryption to {method_labels[new_method]}?\n"
                "Your existing accounts will be re-encrypted in place.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.manager.switch_encryption_method(new_method, password=new_password)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to switch encryption method:\n{e}")
            return

        self._update_encryption_badge()
        _show_info(
            self, "Encryption Switched",
            f"Encryption method switched to {method_labels[new_method]}.",
        )

    # Auto backup
    def _start_auto_backup(self) -> None:
        # Always on: an automatic backup every 24 hours, checked every 10 minutes.
        self._backup_running = False
        timer = QTimer(self)
        timer.setInterval(10 * 60 * 1000)
        timer.timeout.connect(self._auto_backup_tick)
        timer.start()
        self._auto_backup_timer = timer
        self._auto_backup_tick()

    def _auto_backup_tick(self) -> None:
        if auto_backup_mod.backup_due():
            self._run_backup("every 24 hours", automatic=True)

    def _run_backup(self, reason: str, on_done=None, automatic: bool = False) -> None:
        if getattr(self, "_backup_running", False):
            return
        self._backup_running = True
        manager = self.manager

        def _worker():
            try:
                result = auto_backup_mod.make_backup(manager, reason, automatic=automatic)
            except Exception as exc:
                result = OperationResult.failure("BACKUP_FAILED", "Backup Failed", str(exc))
            self._backup_running = False
            if not result and result.code != "BACKUP_NO_ACCOUNTS":
                print(f"[WARNING] Accounts backup failed: {result.message}")
            if on_done is not None:
                try:
                    self._bridge.backup_done.emit(result)
                except RuntimeError:
                    pass

        threading.Thread(target=_worker, daemon=True, name="AccountsBackup").start()

    def _on_backup_now(self) -> None:
        self._run_backup("manual", on_done=True)

    def _on_backup_done(self, result) -> None:
        if result:
            _show_info(self, "Backup Accounts", f"{result.message}\n\nSaved in:\n{result.data.get('path', '')}"
                       + (f"\n\nCopy saved in:\n{result.data['copy']}" if result.data.get("copy") else ""))
        else:
            self._show_operation_error(result)
        self._refresh_backup_status()

    def _open_backup_folder(self) -> None:
        folder = auto_backup_mod.backup_folder()
        os.makedirs(folder, exist_ok=True)
        try:
            os.startfile(folder)
        except Exception as exc:
            _show_error(self, "Backups", f"The folder could not be opened:\n{folder}\n\n{exc}")

    def _refresh_backup_status(self) -> None:
        label = getattr(self, "_backup_status_lbl", None)
        if label is None or not isValid(label):
            return
        backups = auto_backup_mod.list_backups()
        portable = auto_backup_mod.has_user_password(self.manager)
        automatic = sum(1 for backup in backups if backup.get("automatic"))
        lines = [
            ("Backups use your backup password and can be restored on any PC."
             if portable else
             "Backups open on this PC only. Set a backup password (or export with one) "
             "to restore them on another PC."),
            (f"Newest: {backups[0]['when']:%Y-%m-%d %H:%M} ({backups[0]['count']} accounts) · "
             f"{automatic} automatic, {len(backups) - automatic} manual"
             if backups else "No backups yet."),
        ]
        extra = str(actions.get_ui_setting(auto_backup_mod.EXTRA_FOLDER_SETTING, "") or "")
        if extra:
            lines.append(f"Copies also go to: {extra}")
        label.setText("\n".join(lines))
        clear_btn = getattr(self, "_backup_clear_folder_btn", None)
        if clear_btn is not None and isValid(clear_btn):
            clear_btn.setVisible(bool(extra))

    def _on_set_backup_password(self) -> None:
        has_password = auto_backup_mod.has_user_password(self.manager)
        if has_password:
            box = QMessageBox(self)
            box.setWindowTitle("Backup Password")
            box.setText("A backup password is set. New backups can be restored on any PC with it.")
            change = box.addButton("Change", QMessageBox.ButtonRole.AcceptRole)
            remove = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is remove:
                self.manager.set_secure_setting(auto_backup_mod.PASSWORD_KEY, "")
                self._refresh_backup_status()
                return
            if box.clickedButton() is not change:
                return
        password = self._ask_backup_password(confirm=True)
        if password is None:
            return
        if len(password) < account_backup.MIN_PASSWORD_LENGTH:
            _show_error(self, "Backup Password",
                        f"Use at least {account_backup.MIN_PASSWORD_LENGTH} characters.")
            return
        result = self.manager.set_secure_setting(auto_backup_mod.PASSWORD_KEY, password)
        if result is not None and not result:
            self._show_operation_error(result)
            return
        _show_info(self, "Backup Password",
                   "Saved. New backups can be restored on any PC with this password.\n"
                   "Write it down somewhere safe: without it, those backups can't be opened elsewhere.")
        self._run_backup("password set")
        self._refresh_backup_status()

    def _on_choose_backup_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Extra backup folder (USB, Google Drive, OneDrive…)")
        if not folder:
            return
        actions.save_ui_setting(auto_backup_mod.EXTRA_FOLDER_SETTING, folder)
        self._refresh_backup_status()
        self._run_backup("extra folder set")

    def _on_clear_backup_folder(self) -> None:
        actions.save_ui_setting(auto_backup_mod.EXTRA_FOLDER_SETTING, "")
        self._refresh_backup_status()

    def _on_restore_backup(self) -> None:
        dialog = _RestoreBackupDialog(self, auto_backup_mod.list_backups())
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_path:
            return
        path = dialog.selected_path
        passwords = auto_backup_mod.candidate_passwords(self.manager)
        opened_with = None
        for candidate in passwords:
            if account_backup.read_backup(path, candidate):
                opened_with = candidate
                break
        if opened_with is None:
            opened_with = self._ask_backup_password(confirm=False)
            if opened_with is None:
                return
        reply = QMessageBox.question(
            self, "Restore Backup",
            "Replace accounts that already exist with the ones from the backup?\n\n"
            "Yes: replace them · No: keep your current ones and only add missing accounts.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        result = account_backup.import_accounts(
            self.manager, path, opened_with, overwrite=reply == QMessageBox.StandardButton.Yes,
        )
        if not result:
            self._show_operation_error(result)
            return
        print(f"[INFO] Restored backup {os.path.basename(path)}: {result.message}")
        self._refresh_account_list()
        _show_info(self, "Restore Backup", f"Backup restored. {result.message}")

    def _ask_backup_password(self, confirm: bool) -> str | None:
        label = "Backup password:"
        while True:
            password, accepted = QInputDialog.getText(
                self, "Accounts Backup", label, QLineEdit.EchoMode.Password
            )
            if not accepted:
                return None
            if confirm:
                again, accepted = QInputDialog.getText(
                    self, "Accounts Backup", "Repeat the password:", QLineEdit.EchoMode.Password
                )
                if not accepted:
                    return None
                if again != password:
                    QMessageBox.warning(self, "Password Mismatch", "Passwords do not match.")
                    continue
            return password

    def _on_sett_export_accounts(self) -> None:
        if not self.manager.accounts:
            _show_error(self, "Export Accounts", "There are no saved accounts to export.")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export Accounts", "RAM-accounts.rambak",
            "Account backup (*.rambak);;All files (*)",
        )
        if not path:
            return
        password = self._ask_backup_password(confirm=True)
        if password is None:
            return
        with self.manager._accounts_lock:
            accounts = dict(self.manager.accounts)
        result = account_backup.export_accounts(accounts, path, password)
        if not result:
            self._show_operation_error(result)
            return
        print(f"[INFO] Exported {result.data['count']} account(s) to {path}")
        auto_backup_mod.remember_export_password(self.manager, password)
        self._refresh_backup_status()
        _show_info(
            self, "Export Accounts",
            f"{result.message}\n\nKeep the file and its password safe. "
            "Anyone with both can sign in to these accounts.",
        )

    def _on_sett_import_accounts(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "Import Accounts", "",
            "Account backup (*.rambak);;All files (*)",
        )
        if not path:
            return
        password = self._ask_backup_password(confirm=False)
        if password is None:
            return
        reply = QMessageBox.question(
            self, "Import Accounts",
            "Replace accounts that already exist with the ones from the backup?\n\n"
            "Choose No to keep your current accounts and only add new ones.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        result = account_backup.import_accounts(
            self.manager, path, password,
            overwrite=(reply == QMessageBox.StandardButton.Yes),
        )
        if not result:
            self._show_operation_error(result)
            return
        self._refresh_account_list()
        print(f"[INFO] Imported accounts from {path}: {result.message}")
        _show_info(self, "Import Accounts", result.message)

    def _on_sett_wipe_data(self):
        reply = QMessageBox.warning(
            self, "Wipe All Data",
            "This will permanently delete ALL saved accounts, settings,\n"
            "and cached data. This action CANNOT be undone.\n\n"
            "Are you absolutely sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            data_dir = get_data_dir()
            try:
                shutil.rmtree(data_dir, ignore_errors=True)
                QMessageBox.information(
                    self, "Done",
                    "All data wiped. The application will now close."
                )
                QApplication.quit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Wipe failed:\n{e}")

    def _on_sett_developer_mode(self, enabled: bool):
        self._sett_copycookie_chk.setEnabled(enabled)
        self._sett_ws_chk.setEnabled(enabled)
        self._update_ws_password_controls()
        if not enabled:
            self._sett_copycookie_chk.setChecked(False)
            self._sett_ws_chk.setChecked(False)
            actions.save_ui_setting("enable_copy_cookie", False)
            actions.save_ui_setting("websocket_enabled", False)
            self._stop_ws_server()

    def _on_sett_ws_changed(self, enabled: bool):
        self._update_ws_password_controls()
        if enabled:
            self._start_ws_server()
        else:
            self._stop_ws_server()

    def _update_ws_password_controls(self):
        if not hasattr(self, "_sett_ws_pw_chk"):
            return
        developer_enabled = bool(
            hasattr(self, "_sett_devmode_chk")
            and self._sett_devmode_chk.isChecked()
        )
        websocket_enabled = bool(
            hasattr(self, "_sett_ws_chk")
            and self._sett_ws_chk.isChecked()
        )
        controls_enabled = developer_enabled and websocket_enabled
        self._sett_ws_pw_chk.setEnabled(controls_enabled)
        password_enabled = controls_enabled and self._sett_ws_pw_chk.isChecked()
        if hasattr(self, "_sett_ws_password_edit"):
            self._sett_ws_password_edit.setEnabled(password_enabled)
        if hasattr(self, "_sett_ws_password_set_btn"):
            self._sett_ws_password_set_btn.setEnabled(password_enabled)

    def _on_sett_ws_password_required(self, enabled: bool):
        self._update_ws_password_controls()
        if enabled and not self._sett_ws_saved_password:
            self._sett_ws_password_edit.setFocus()

    def _on_sett_ws_password_save(self):
        password = self._sett_ws_password_edit.text()
        previous = self._sett_ws_saved_password
        if password == previous:
            return True
        if not password:
            self._sett_ws_password_edit.setText(previous)
            self._show_operation_error(OperationResult.failure(
                "WEBSOCKET_PASSWORD_REQUIRED",
                "WebSocket Password Required",
                "Enter a WebSocket password before clicking Set.",
            ))
            return False

        result = self.manager.set_secure_setting(
            "websocket_password",
            password,
        )

        if not result:
            self._sett_ws_password_edit.setText(previous)
            self._show_operation_error(result)
            return False

        self._sett_ws_saved_password = password
        return True

    def _start_ws_server(self) -> None:
        if self._ws_server:
            self._ws_server.start()

    def _stop_ws_server(self) -> None:
        if self._ws_server:
            self._ws_server.stop()


    _AR_ACTIVE_COLOR = "#4CAF50"
    _AR_INACTIVE_COLOR = "#EF5350"

    def _ar_refresh_list(self):
        if self._ar_list is None:
            return

        cur = self._ar_selected_account()
        self._ar_list.clear()
        if not hasattr(self, "_ar_avatar_labels"):
            self._ar_avatar_labels: dict[str, QLabel] = {}
        self._ar_avatar_labels.clear()

        AV = avatars.AVATAR_SIZE
        ITEM_H = AV + 6

        if not self._ar_configs:
            empty = QListWidgetItem("No accounts monitored, Press Add Account.")
            empty.setForeground(QColor(MUTED))
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._ar_list.addItem(empty)
            return

        for account, cfg in self._ar_configs.items():
            worker = self._ar_workers.get(account)
            active = worker is not None and worker.is_alive()

            item = QListWidgetItem("")
            item.setSizeHint(QSize(0, ITEM_H))
            item.setData(Qt.ItemDataRole.UserRole, account)

            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(4, 0, 6, 0)
            row_lay.setSpacing(6)

            # Avatar
            av_lbl = QLabel()
            av_lbl.setFixedSize(AV, AV)
            av_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
            av_lbl.setPixmap(self._make_placeholder_pixmap(AV))
            row_lay.addWidget(av_lbl)
            self._ar_avatar_labels[account] = av_lbl

            # Username
            name_lbl = QLabel(account)
            name_lbl.setObjectName("accountName")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            row_lay.addWidget(name_lbl)

            # status
            sep = QLabel("|")
            sep.setObjectName("noteSep")
            sep.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            row_lay.addWidget(sep)

            status_str = "active"   if active else "inactive"
            status_color = self._AR_ACTIVE_COLOR if active else self._AR_INACTIVE_COLOR
            status_detail = self._ar_statuses.get(account, "")
            if active and status_detail.startswith("Waiting:"):
                status_str, status_color = "waiting", NOTE
            elif active and status_detail.startswith(("Launching", "Rejoining")):
                status_str, status_color = "launching", NOTE
            status_lbl = QLabel(status_str)
            status_lbl.setToolTip(status_detail)
            status_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            status_lbl.setStyleSheet(f"color: {status_color}; font-size: 10px;")
            row_lay.addWidget(status_lbl)

            row_lay.addStretch(1)

            destination = (
                f"User: {cfg.get('target_user', '?')}"
                if cfg.get("join_mode") == "user"
                else f"Place: {cfg.get('place_id', '?')}"
            )
            pid_lbl = QLabel(destination)
            pid_lbl.setToolTip(destination)
            pid_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            pid_lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            row_lay.addWidget(pid_lbl)

            row.setFixedHeight(ITEM_H)
            self._ar_list.addItem(item)
            self._ar_list.setItemWidget(item, row)

        # Restore selection
        if cur:
            for i in range(self._ar_list.count()):
                it = self._ar_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == cur:
                    self._ar_list.setCurrentItem(it)
                    break
        if self._ar_list.count() > 0 and not self._ar_list.currentItem():
            self._ar_list.setCurrentRow(0)

        self._ar_load_avatars_async()

    def _ar_load_avatars_async(self): # Load avatar for auto rejoin accounts
        ar_labels = getattr(self, "_ar_avatar_labels", {})
        for account in list(ar_labels.keys()):
            acc_data = self.manager.accounts.get(account, {})
            if not isinstance(acc_data, dict):
                continue
            user_id = str(acc_data.get("user_id") or "")
            if not user_id or user_id == "0":
                continue
            avatars.fetch_avatar_async(
                user_id, account,
                on_done=lambda u, b: self._bridge.avatar_ready.emit(u, b),
            )

    def _on_rejoin_status(self, account: str, status: str) -> None:
        self._ar_statuses[account] = status
        self._ar_refresh_list()

    def _ar_selected_account(self) -> str | None: # get selected account
        item = self._ar_list.currentItem() if self._ar_list else None
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _ar_start_worker(self, account: str) -> None: # start worker for an account
        if account in self._ar_workers and self._ar_workers[account].is_alive():
            return
        cfg = self._ar_configs.get(account)
        if not cfg:
            return
        self._ar_statuses[account] = "Launching..."
        worker = ar.AutoRejoinWorker(
            account, cfg, self.manager,
            on_status=lambda u, s: self._bridge.rejoin_status.emit(u, s),
        )
        self._ar_workers[account] = worker
        worker.start()

    def _ar_on_context_menu(self, pos): # Context menu for auto rejoin accounts
        # Start: start worker to monitor and auto-rejoin for this account
        # Stop: stop worker and auto-rejoin for this account
        # Edit: edit the account's auto-rejoin configuration
        # Remove: stop worker if active, remove account from auto-rejoin list and erase configuration
        item = self._ar_list.itemAt(pos)
        if item is None:
            return
        account = item.data(Qt.ItemDataRole.UserRole)
        if not account:
            return

        self._ar_list.setCurrentItem(item)
        worker = self._ar_workers.get(account)
        active = worker is not None and worker.is_alive()

        menu = QMenu(self)
        act_start = menu.addAction("Start") if not active else None
        act_stop = menu.addAction("Stop") if active else None
        act_edit = menu.addAction("Edit")
        menu.addSeparator()
        act_remove = menu.addAction("Remove")

        chosen = menu.exec(self._ar_list.mapToGlobal(pos))
        if act_start  and chosen == act_start:
            self._ar_on_start()
        elif act_stop  and chosen == act_stop:
            self._ar_on_stop()
        elif chosen == act_edit:
            self._ar_on_edit()
        elif chosen == act_remove:
            self._ar_on_remove()

    # Auto-Rejoin button slots
    def _ar_on_add(self):
        win = _AutoRejoinAddWindow(self.manager, self)
        if win.exec() == QDialog.DialogCode.Accepted:
            for account, cfg in win.result_configs.items():
                self._ar_configs[account] = cfg
            ar.save_configs(self._ar_configs)
            self._ar_refresh_list()

    def _ar_on_edit(self): # Edit config for the selected account
        account = self._ar_selected_account()
        if not account:
            _show_error(self, "No Selection", "Select an account to edit.")
            return
        cfg = self._ar_configs.get(account)
        if not cfg:
            return
        # pre fill the add/edit form with the existing config for this account
        win = _AutoRejoinAddWindow(self.manager, self, edit_account=account, edit_config=cfg)
        if win.exec() == QDialog.DialogCode.Accepted:
            for acc, config in win.result_configs.items():
                self._ar_configs[acc] = config
            ar.save_configs(self._ar_configs)
            self._ar_refresh_list()

    def _ar_on_start(self):
        account = self._ar_selected_account()
        if not account:
            _show_error(self, "No Selection", "Select an account to start.")
            return
        self._ar_start_worker(account)
        self._ar_show_launching_status(account)
        QTimer.singleShot(500, self._ar_refresh_list)

    def _ar_show_launching_status(self, account: str):
        for i in range(self._ar_list.count()):
            item = self._ar_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == account:
                widget = self._ar_list.itemWidget(item)
                if widget:
                    # Find and update the status label
                    for child in widget.children():
                        if isinstance(child, QLabel):
                            style = child.styleSheet()
                            if "active" in style or "inactive" in style:
                                child.setText("launching")
                                child.setStyleSheet(f"color: {NOTE}; font-size: 10px;")
                                break
                break 

    def _ar_on_stop(self):
        account = self._ar_selected_account()
        if not account:
            _show_error(self, "No Selection", "Select an account to stop.")
            return
        worker = self._ar_workers.pop(account, None)
        if worker:
            worker.stop()
        self._ar_refresh_list()

    def _ar_on_start_all(self):
        for account in list(self._ar_configs.keys()):
            self._ar_start_worker(account)
        for account in list(self._ar_configs.keys()):
            self._ar_show_launching_status(account)
        QTimer.singleShot(500, self._ar_refresh_list)

    def _ar_on_stop_all(self):
        for worker in list(self._ar_workers.values()):
            worker.stop()
        self._ar_workers.clear()
        self._ar_refresh_list()

    def nativeEvent(self, event_type, message):
        if window_grid_mod.is_hotkey_message(message, window_grid_mod.KILL_ALL_HOTKEY_ID):
            self._on_kill_all_roblox()
            return True, 0
        if window_grid_mod.is_hotkey_message(message, window_grid_mod.MINIMIZE_ALL_HOTKEY_ID):
            print(f"[INFO] Minimized {window_grid_mod.minimize_roblox_windows()} Roblox window(s)")
            return True, 0
        if window_grid_mod.is_hotkey_message(message, window_grid_mod.RESTORE_ALL_HOTKEY_ID):
            print(f"[INFO] Restored {window_grid_mod.restore_roblox_windows()} Roblox window(s)")
            return True, 0
        if window_grid_mod.is_hotkey_message(message, window_grid_mod.MINIMIZE_ONE_HOTKEY_ID):
            if not window_grid_mod.minimize_one_roblox_window():
                print("[INFO] No open Roblox window left to minimize")
            return True, 0
        if window_grid_mod.is_hotkey_message(message):
            choice = window_size_mod.load_choice()
            if choice.mode != window_size_mod.MODE_OFF:
                # Arrange like the Window Size drawing, at that size, and lock them.
                threading.Thread(target=window_size_mod.arrange_grid, args=(choice,),
                                 daemon=True, name="WindowGridSized").start()
                return True, 0
            result = window_grid_mod.tile_roblox_windows()
            if not result:
                print(f"[Window Grid] {result.code}: {result.message}")
                if result.detail:
                    print(f"[Window Grid] {result.detail}")
            return True, 0
        return super().nativeEvent(event_type, message)

    def _setup_system_tray(self) -> None:
        if actions.load_ui_settings().get("hide_to_system_tray", False):
            result = self._enable_system_tray()
            if not result:
                self._set_tray_checkbox(False)
                actions.save_ui_setting("hide_to_system_tray", False)
                self._show_operation_error(result)

    def _set_tray_checkbox(self, enabled: bool) -> None:
        if not hasattr(self, "_sett_tray_chk"):
            return
        self._sett_tray_chk.blockSignals(True)
        self._sett_tray_chk.setChecked(enabled)
        self._sett_tray_chk.blockSignals(False)

    def _enable_system_tray(self) -> OperationResult:
        if self._tray_icon:
            self._tray_icon.show()
            QApplication.instance().setQuitOnLastWindowClosed(False)
            return OperationResult.success()

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return OperationResult.failure(
                "SYSTEM_TRAY_UNAVAILABLE",
                "System Tray Unavailable",
                "Windows did not provide a system tray for ANAne RAM.",
                detail="QSystemTrayIcon.isSystemTrayAvailable() returned false.",
            )

        try:
            icon = _app_icon(self._icon_path)
            if icon.isNull():
                icon = QApplication.windowIcon()
            if icon.isNull():
                return OperationResult.failure(
                    "SYSTEM_TRAY_SETUP_FAILED",
                    "System Tray Setup Failed",
                    "The application icon could not be loaded for the system tray.",
                    detail="No valid application icon was available.",
                )

            tray_icon = QSystemTrayIcon(icon, self)
            tray_icon.setToolTip("ANAne's Roblox Account Manager")
            menu = QMenu(self)
            show_action = QAction("Show UI", self)
            exit_action = QAction("Exit", self)
            show_action.triggered.connect(self._show_from_tray)
            exit_action.triggered.connect(self._exit_from_tray)
            menu.addAction(show_action)
            menu.addSeparator()
            menu.addAction(exit_action)
            tray_icon.setContextMenu(menu)
            tray_icon.activated.connect(self._on_tray_activated)
            tray_icon.show()

            self._tray_icon = tray_icon
            self._tray_menu = menu
            QApplication.instance().setQuitOnLastWindowClosed(False)
            return OperationResult.success()
        except Exception as exc:
            print(f"[ERROR] Failed to create system tray icon: {exc}")
            return OperationResult.failure(
                "SYSTEM_TRAY_SETUP_FAILED",
                "System Tray Setup Failed",
                "The system tray icon could not be created.",
                detail=f"{type(exc).__name__}: {exc}",
            )

    def _disable_system_tray(self) -> None:
        tray_icon = self._tray_icon
        tray_menu = self._tray_menu
        self._tray_icon = None
        self._tray_menu = None
        if tray_icon:
            tray_icon.hide()
            tray_icon.setContextMenu(None)
            tray_icon.deleteLater()
        if tray_menu:
            tray_menu.deleteLater()
        app = QApplication.instance()
        if app:
            app.setQuitOnLastWindowClosed(True)

    def _on_sett_tray(self, enabled: bool) -> None:
        if enabled:
            result = self._enable_system_tray()
            if result:
                actions.save_ui_setting("hide_to_system_tray", True)
                return
            self._set_tray_checkbox(False)
            actions.save_ui_setting("hide_to_system_tray", False)
            self._show_operation_error(result)
            return

        actions.save_ui_setting("hide_to_system_tray", False)
        self._disable_system_tray()

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        try:
            self.show()
            if self._tray_restore_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            self._restore_detached_pages_from_tray()
            self.raise_()
            self.activateWindow()
        except Exception as exc:
            print(f"[ERROR] Failed to restore UI from system tray: {exc}")
            _show_error(
                self,
                "System Tray Restore Failed",
                "The main window could not be restored from the system tray.\n\n"
                f"Error code: SYSTEM_TRAY_RESTORE_FAILED\n\n{exc}",
            )

    def _exit_from_tray(self) -> None:
        self._tray_exit_requested = True
        self.close()

    def _quit_for_update(self) -> None:
        self._tray_exit_requested = True
        self._disable_system_tray()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()

    def _perform_shutdown_cleanup(self) -> None:
        self._save_window_size()
        diagnostics.set_console_wakeup(None)
        private_manager = getattr(self, '_private_server_manager', None)
        if private_manager is not None and isValid(private_manager):
            private_manager.close()
        if self._shutdown_cleanup_done:
            return
        self._shutdown_cleanup_done = True

        try:
            self._close_detached_pages()
        except Exception:
            pass

        try:
            self._unregister_window_grid_hotkey()
        except Exception:
            pass
        try:
            self._unregister_kill_hotkey()
        except Exception:
            pass
        try:
            self._unregister_window_keys()
        except Exception:
            pass
        try:
            for worker in list(self._ar_workers.values()):
                worker.stop(join_timeout=1.0)
        except Exception:
            pass
        try:
            if self._cv_validator is not None:
                stopped = self._cv_validator.stop(join_timeout=2.0)
                if not stopped:
                    print("[WARNING] Cookie validator did not stop before UI shutdown.")
                self._cv_validator = None
        except Exception as exc:
            print(f"[WARNING] Failed to stop cookie validator: {exc}")
        # Stop WebSocket server
        try:
            if self._ws_server:
                self._ws_server.stop()
        except Exception:
            pass
        # Stop presence scanner
        try:
            self._stop_presence_scanner()
        except Exception:
            pass
        # Stop Roblox window renamer
        try:
            self._stop_rename_windows()
        except Exception:
            pass
        # Cleanup drag-drop filter
        try:
            if hasattr(self, "_drag_filter"):
                self._drag_filter.cleanup()
        except Exception:
            pass
        # Restore quarantined installers
        try:
            if actions.load_ui_settings().get("roblox_installer_fix", False):
                RobloxAPI.restore_installers()
        except Exception as e:
            print(f"[ERROR] Failed to restore installers: {e}")
        # Unlock the Roblox settings file only when this app owns the lock.
        try:
            profile_result = roblox_settings_mod.load_local_profile()
            profile = (profile_result.data or {}).get("profile", {})
            if profile_result and bool(profile.get("lock_owned", False)):
                unlock_result = roblox_settings_mod.unlock_framerate_cap()
                if not unlock_result:
                    print(
                        f"[ERROR] {unlock_result.code}: "
                        f"{unlock_result.message} {unlock_result.detail}"
                    )
                else:
                    profile["lock_owned"] = False
                    roblox_settings_mod.save_local_profile(profile)
        except Exception as e:
            print(f"[ERROR] Failed to unlock framerate cap file: {e}")
        try:
            self._stop_headless_manager()
        except Exception as e:
            print(f"[ERROR] Failed to restore Roblox windows: {e}")
        self._disable_system_tray()

    def closeEvent(self, event):
        self._save_window_size()
        hide_to_tray = actions.load_ui_settings().get(
            "hide_to_system_tray",
            False,
        )
        if hide_to_tray and not self._tray_exit_requested:
            self._tray_restore_maximized = self.isMaximized()
            event.ignore()
            self._hide_detached_pages_for_tray()
            self.hide()
            return

        self._perform_shutdown_cleanup()
        super().closeEvent(event)

    def _ar_on_remove(self):
        account = self._ar_selected_account()
        if not account:
            _show_error(self, "No Selection", "Select an account to remove.")
            return
        reply = QMessageBox.question(
            self, "Remove Auto-Rejoin",
            f"Remove auto-rejoin for '{account}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        worker = self._ar_workers.pop(account, None)
        if worker:
            worker.stop()
        self._ar_configs.pop(account, None)
        ar.save_configs(self._ar_configs)
        self._ar_refresh_list()

    def _build_right_panel(self) -> QFrame: # Right panel actions
        panel = QFrame()
        panel.setObjectName("rightPanel")
        panel.setFixedWidth(326)

        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)

        # Actions card
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 18)
        lay.setSpacing(8)
        column.addWidget(card)

        actions_header = QHBoxLayout()
        actions_header.setContentsMargins(0, 0, 0, 0)
        actions_header.setSpacing(10)
        bolt = QLabel()
        bolt.setPixmap(icons.pixmap("bolt", ACCENT_BRIGHT, 26))
        bolt.setStyleSheet("background: transparent;")
        actions_header.addWidget(bolt)
        title = QLabel("Actions")
        title.setObjectName("cardTitle")
        actions_header.addWidget(title)
        actions_header.addStretch(1)
        _discord_path = os.path.join(get_data_dir(), "discordlogo.png")
        if not os.path.exists(_discord_path):
            _discord_path = get_resource_path("assets", "discordlogo.png")
        discord_btn = QPushButton()
        discord_btn.setObjectName("discordRound")
        discord_btn.setProperty("_motion_attached", True)  # no square hover tint on the round button
        discord_btn.setToolTip("Join Discord server")
        discord_btn.setAccessibleName("Join Discord server")
        discord_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if os.path.exists(_discord_path):
            _dsrc = QPixmap(_discord_path)
            # The logo file has empty space around the circle; crop it so the
            # circle fills the button.
            _margin = round(_dsrc.width() * 0.1225)
            if _dsrc.width() > 2 * _margin:
                _dsrc = _dsrc.copy(_margin, _margin, _dsrc.width() - 2 * _margin, _dsrc.height() - 2 * _margin)
            _ratio = max(1.0, self.devicePixelRatioF())
            _dpix = _dsrc.scaled(
                round(34 * _ratio), round(34 * _ratio),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            _dpix.setDevicePixelRatio(_ratio)
            discord_btn.setIcon(QIcon(_dpix))
            discord_btn.setIconSize(QSize(34, 34))
        discord_btn.clicked.connect(
            lambda: webbrowser.open(self.DISCORD_SERVER_URL)
        )
        actions_header.addWidget(discord_btn)
        lay.addLayout(actions_header)
        lay.addSpacing(4)

        # Current game card
        current = QFrame()
        current.setObjectName("currentGame")
        current_lay = QHBoxLayout(current)
        current_lay.setContentsMargins(14, 8, 14, 8)
        current_lay.setSpacing(12)
        current_title = QLabel("Current:")
        current_title.setStyleSheet("font-size: 13px; font-weight: 700; background: transparent;")
        current_lay.addWidget(current_title)
        self._current_game_icon = QLabel()
        self._current_game_icon.setFixedSize(32, 32)
        self._current_game_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._current_game_icon.setStyleSheet("background: transparent;")
        current_lay.addWidget(self._current_game_icon)
        self._current_game_name = QLabel()
        self._current_game_name.setStyleSheet("font-size: 13px; background: transparent;")
        current_lay.addWidget(self._current_game_name, 1)
        current.setMinimumHeight(52)
        lay.addWidget(current)
        # Other code writes "Current: <name>" here; the card shows it.
        self._game_name_label = _GameNameLabel(self._show_current_game, current)
        self._game_name_label.hide()
        self._show_current_game("")
        lay.addSpacing(4)

        # Place ID
        place_lbl = QLabel("Place ID")
        place_lbl.setObjectName("fieldLabel")
        lay.addWidget(place_lbl)

        self._place_id_edit = QComboBox()
        self._place_id_edit.setObjectName("bigField")
        self._place_id_edit.setEditable(True)
        self._place_id_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._place_id_edit.lineEdit().setPlaceholderText("e.g. 10449761463") # This game is fun
        self._place_id_edit.lineEdit().setTextMargins(0, 0, 26, 0)
        self._place_id_edit.currentTextChanged.connect(self._on_place_id_changed)
        self._place_id_edit.activated.connect(self._on_favorite_selected)
        self._favorite_ctx_filter = _ComboRightClickFilter(self)
        self._favorite_ctx_filter.right_clicked.connect(self._on_favorite_context_menu)
        self._place_id_edit.view().viewport().installEventFilter(self._favorite_ctx_filter)
        self._refresh_favorites_dropdown()

        # Games Selector button sits inside the Place ID field, on the right.
        self._games_selector_btn = QPushButton(self._place_id_edit)
        self._games_selector_btn.setObjectName("gamesSelectorButton")
        self._games_selector_btn.setFixedSize(22, 22)
        self._games_selector_btn.setFlat(True)
        self._games_selector_btn.setIcon(icons.make_icon("search", MUTED,
                                                       disabled=LINE, size=16))
        self._games_selector_btn.setIconSize(QSize(16, 16))
        self._games_selector_btn.setToolTip("Games Selector")
        self._games_selector_btn.setAccessibleName("Open Games Selector")
        self._games_selector_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._games_selector_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 0; padding: 0; min-height: 0; }"
            "QPushButton:hover { background: transparent; border: 0; }"
            "QPushButton:focus { background: transparent; border: 0; outline: none; }"
        )
        self._games_selector_btn.clicked.connect(self._open_games_selector)
        self._games_selector_inset = _InsetButtonFilter(self._place_id_edit, self._games_selector_btn)
        lay.addWidget(self._place_id_edit)
        lay.addSpacing(4)

        # Private server
        priv_lbl = QLabel("Private Server Link (Optional)")
        priv_lbl.setObjectName("fieldLabel")
        lay.addWidget(priv_lbl)

        self._private_server_edit = _ActionComboBox("Private Server Manager")
        self._private_server_edit.setObjectName("bigField")
        self._private_server_edit.setEditable(True)
        self._private_server_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._private_server_edit.lineEdit().setPlaceholderText("VIP Link or Link Code")
        self._private_server_edit.currentTextChanged.connect(self._on_private_server_changed)
        self._private_server_edit.action_requested.connect(self._open_private_server_manager)
        lay.addWidget(self._private_server_edit)
        lay.addSpacing(8)

        # join button
        join_btn = QPushButton("Join Place ID")
        join_btn.setObjectName("primary")
        join_btn.setIcon(icons.make_icon("enter", PRIMARY_TEXT, size=18))
        join_btn.setIconSize(QSize(18, 18))
        join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        join_btn.clicked.connect(self._on_join_place)

        self._join_menu = QMenu(self)
        act_join_user = self._join_menu.addAction("Join User")
        act_job_id = self._join_menu.addAction("Job ID")
        act_small_srv = self._join_menu.addAction("Small Server")
        self._join_menu.addSeparator()
        act_save_fav = self._join_menu.addAction("Save Current Game")
        act_join_user.triggered.connect(self._on_join_user)
        act_job_id.triggered.connect(self._on_join_job_id)
        act_small_srv.triggered.connect(self._on_join_small_server)
        act_save_fav.triggered.connect(self._on_save_current_game)

        self._join_arrow = QToolButton()
        self._join_arrow.setObjectName("splitArrow")
        self._join_arrow.setText("V")
        self._join_arrow.setToolTip("More ways to join")
        self._join_arrow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._join_arrow.setMenu(self._join_menu)
        self._join_arrow.setFixedWidth(44)
        self._join_arrow.setFixedHeight(42)

        join_row = QHBoxLayout()
        join_row.setSpacing(10)
        join_row.addWidget(join_btn, 1)
        join_row.addWidget(self._join_arrow)
        lay.addLayout(join_row)
        lay.addSpacing(4)

        # Join User: follow a player into the server they are in right now
        target_lbl = QLabel("Join User")
        target_lbl.setObjectName("fieldLabel")
        target_lbl.setToolTip(
            "Join the server a player is in right now. Works without being friends\n"
            "when their Roblox privacy \"Who can join me?\" is set to Everyone."
        )
        lay.addWidget(target_lbl)
        target_row = QHBoxLayout()
        target_row.setSpacing(10)
        self._join_target_edit = QComboBox()
        self._join_target_edit.setObjectName("bigField")
        self._join_target_edit.setEditable(True)
        self._join_target_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._join_target_edit.lineEdit().setPlaceholderText("Target username")
        self._join_target_edit.lineEdit().setClearButtonEnabled(True)
        self._join_target_edit.setToolTip(target_lbl.toolTip())
        for name in self._recent_join_targets():
            self._join_target_edit.addItem(name)
        self._join_target_edit.setCurrentText(str(actions.get_ui_setting("last_join_target", "") or ""))
        self._join_target_edit.lineEdit().returnPressed.connect(self._on_join_target)
        target_row.addWidget(self._join_target_edit, 1)
        self._join_target_btn = QPushButton()
        self._join_target_btn.setObjectName("joinUserButton")
        self._join_target_btn.setToolTip("Join this user's server with the selected accounts")
        self._join_target_btn.setAccessibleName("Join user")
        self._join_target_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._join_target_btn.setIcon(icons.make_icon("user_join", PRIMARY_TEXT, size=22))
        self._join_target_btn.setIconSize(QSize(22, 22))
        self._join_target_btn.setFixedSize(44, 42)
        self._join_target_btn.setStyleSheet(PRIMARY_BUTTON_QSS.replace("min-height: 42px; padding: 0 14px;", "min-height: 0; padding: 0;"))
        self._join_target_btn.clicked.connect(self._on_join_target)
        target_row.addWidget(self._join_target_btn)
        lay.addLayout(target_row)

        # Recent games card
        recent_card = QFrame()
        recent_card.setObjectName("card")
        recent_lay = QVBoxLayout(recent_card)
        recent_lay.setContentsMargins(18, 14, 18, 18)
        recent_lay.setSpacing(10)
        column.addWidget(recent_card, 1)

        recent_lbl = QLabel("Recent games")
        recent_lbl.setObjectName("sectionTitle")
        recent_lay.addWidget(recent_lbl)

        self._recent_list = QListWidget()
        self._recent_list.setObjectName("recentList")
        self._recent_list.setIconSize(QSize(30, 30))
        self._recent_list.setItemDelegate(_RecentGameDelegate(self._recent_list))
        self._recent_list.setMinimumHeight(60)
        self._recent_list.setToolTip("Double-click to load a game. Right-click to delete it.")
        self._recent_list.itemDoubleClicked.connect(self._on_recent_game_double_click)
        self._recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._recent_list.customContextMenuRequested.connect(self._on_recent_game_context_menu)
        recent_lay.addWidget(self._recent_list, 1)

        # Hotkey and Performance live side by side in the space under the list.
        sheet_row = QHBoxLayout()
        sheet_row.setSpacing(10)
        for label, kind, icon_name, tip in (
            ("Hotkey", "hotkey", "keyboard", "Kill All and Minimize / Restore shortcut keys"),
            ("Performance", "performance", "gauge", "FPS cap and graphics quality for Roblox"),
        ):
            btn = QPushButton(f" {label}")
            btn.setObjectName("tileSmall")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            icons.set_accent_icon(btn, icon_name, _THEME, size=20)
            btn.clicked.connect(lambda _=False, k=kind: self._open_settings_sheet(k))
            sheet_row.addWidget(btn, 1)
            setattr(self, f"_{kind}_sheet_btn", btn)
        recent_lay.addLayout(sheet_row)

        # Quick action buttons, side by side like Hotkey and Performance
        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        for label, slot, icon_name, tip in [
            ("Refresh List", self._refresh_account_list, "refresh", "Reload the account list"),
            ("Roblox Home", self._on_launch_home, "home", "Launch Roblox Home with the selected accounts"),
        ]:
            btn = QPushButton(f" {label}")
            btn.setObjectName("tileSmall")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            icons.set_accent_icon(btn, icon_name, _THEME, size=20)
            btn.clicked.connect(slot)
            quick_row.addWidget(btn, 1)
        recent_lay.addLayout(quick_row)

        return panel

    def _show_current_game(self, text: str) -> None:
        name_lbl = getattr(self, "_current_game_name", None)
        icon_lbl = getattr(self, "_current_game_icon", None)
        if name_lbl is None or not isValid(name_lbl):
            return
        name = str(text or "").replace("Current: ", "", 1).strip()
        name_lbl.setText(name or "No game selected")
        name_lbl.setStyleSheet(
            f"font-size: 13px; background: transparent; color: {TEXT if name else MUTED};"
        )
        place_id = ""
        if name and hasattr(self, "_place_id_edit"):
            place_id = self._place_id_edit.currentText().strip()
        self._current_place_icon_id = place_id
        pixmap = self._place_icon_pixmap(place_id, 32) if place_id else None
        if pixmap is None:
            icon_lbl.setPixmap(icons.pixmap("gamepad", MUTED if not name else ACCENT_BRIGHT, 24))
            if place_id:
                self._request_place_icon(place_id)
        else:
            icon_lbl.setPixmap(pixmap)

    # Game icons (Current card and Recent games)
    _place_icon_bytes: dict[str, bytes] = {}
    _place_icon_pending: set[str] = set()

    @staticmethod
    def _rounded_pixmap(data: bytes, size: int, radius: int = 8) -> QPixmap | None:
        src = QPixmap()
        if not data or not src.loadFromData(data):
            return None
        src = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, src)
        painter.end()
        return result

    def _place_icon_pixmap(self, place_id: str, size: int) -> QPixmap | None:
        data = self._place_icon_bytes.get(str(place_id))
        return self._rounded_pixmap(data, size) if data else None

    def _request_place_icon(self, place_id: str) -> None:
        place_id = str(place_id or "").strip()
        if (not place_id.isdigit() or place_id in self._place_icon_bytes
                or place_id in self._place_icon_pending):
            return
        self._place_icon_pending.add(place_id)
        bridge = self._bridge

        def _worker():
            data = game_selector_mod.fetch_place_icon(place_id)
            try:
                bridge.place_icon_ready.emit(place_id, data or b"")
            except RuntimeError:
                pass  # window already closed

        threading.Thread(target=_worker, daemon=True, name="PlaceIcon").start()

    def _on_place_icon_ready(self, place_id: str, data: object) -> None:
        self._place_icon_pending.discard(place_id)
        self._place_icon_bytes[place_id] = bytes(data or b"")
        if not data:
            return
        if getattr(self, "_current_place_icon_id", "") == place_id:
            pixmap = self._place_icon_pixmap(place_id, 32)
            if pixmap is not None and isValid(self._current_game_icon):
                self._current_game_icon.setPixmap(pixmap)
        self._apply_recent_icons()

    def _apply_recent_icons(self) -> None:
        recent = getattr(self, "_recent_list", None)
        if recent is None or not isValid(recent):
            return
        placeholder = QIcon(icons.pixmap("gamepad", MUTED, 24))
        for row in range(recent.count()):
            item = recent.item(row)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            place_id = str(data.get("place_id", "") or "")
            pixmap = self._place_icon_pixmap(place_id, 30)
            if pixmap is not None:
                item.setIcon(QIcon(pixmap))
            else:
                item.setIcon(placeholder)
                self._request_place_icon(place_id)

    # Hotkey and Performance sheets (opened from the buttons under Recent games)
    def _settings_checkbox(self, key, label: str, tip: str, default=False, on_change=None) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(bool(actions.load_ui_settings().get(key, default)) if key is not None else False)
        cb.setToolTip(tip)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)

        def _h(state):
            val = (state == Qt.CheckState.Checked.value)
            if key is not None:
                actions.save_ui_setting(key, val)
            if on_change:
                on_change(val)
        cb.stateChanged.connect(_h)
        return cb

    def _sheet_group(self, body: QVBoxLayout, title: str, icon_name: str, hint: str = "") -> QVBoxLayout:
        group = QFrame()
        group.setObjectName("sheetGroup")
        group.setStyleSheet(
            f"QFrame#sheetGroup {{ background: {INPUT}; border: 1px solid {LINE}; border-radius: 14px; }}"
        )
        lay = QVBoxLayout(group)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(icons.pixmap(icon_name, ACCENT_BRIGHT, 20))
        icon.setStyleSheet("background: transparent;")
        head.addWidget(icon)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        head.addWidget(title_lbl)
        head.addStretch(1)
        lay.addLayout(head)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setWordWrap(True)
            hint_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
            lay.addWidget(hint_lbl)
        body.addWidget(group)
        return lay

    def _open_settings_sheet(self, kind: str) -> None:
        sheets = self.__dict__.setdefault("_settings_sheets", {})
        sheet = sheets.get(kind)
        if sheet is None or not isValid(sheet):
            if kind == "hotkey":
                sheet = _SettingsSheet(
                    self, "Hotkey", "keyboard",
                    "Keys that work everywhere, even while Roblox is focused.",
                    self._build_hotkey_settings,
                )
            else:
                sheet = _SettingsSheet(
                    self, "Performance", "gauge",
                    "Lower FPS and graphics to run more accounts.",
                    self._build_performance_settings,
                )
            sheets[kind] = sheet
        sheet.present(animate=self._animations_on)

    def _build_hotkey_settings(self, body: QVBoxLayout) -> None:
        S = actions.load_ui_settings()

        f = self._sheet_group(
            body, "Kill All Roblox shortcut", "power",
            "Press this key anywhere to instantly close every Roblox window, "
            "same as the Kill All Roblox button.",
        )
        self._sett_kill_key_chk = self._settings_checkbox(
            "kill_all_hotkey_enabled", "Enable Kill All Roblox Shortcut",
            "Press this key anywhere (even while Roblox is focused) to instantly close every\n"
            "Roblox window, same as the Kill All Roblox button.",
            on_change=self._on_sett_kill_hotkey_enabled,
        )
        f.addWidget(self._sett_kill_key_chk)
        kill_toggle_row = QHBoxLayout()
        kill_toggle_row.setSpacing(10)
        self._sett_kill_toggle_btn = QPushButton()
        self._sett_kill_toggle_btn.setCheckable(True)
        self._sett_kill_toggle_btn.hide()  # replaced by the checkbox above
        self._sett_kill_toggle_btn.setFixedSize(84, 30)
        # Mouse only: a focused button is otherwise "clicked" by Space/Enter,
        # which would flip it whenever the shortcut key is pressed.
        self._sett_kill_toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sett_kill_toggle_btn.clicked.connect(
            lambda checked: self._sett_kill_key_chk.setChecked(bool(checked))
        )
        self._sett_kill_key_chk.toggled.connect(self._sync_kill_toggle_btn)
        self._sett_kill_state_lbl = QLabel()
        self._sett_kill_state_lbl.setStyleSheet("background: transparent;")
        kill_toggle_row.addWidget(self._sett_kill_state_lbl)
        kill_toggle_row.addStretch(1)
        f.addLayout(kill_toggle_row)
        self._sync_kill_toggle_btn(self._sett_kill_key_chk.isChecked())
        kill_row = QHBoxLayout()
        kill_lbl = QLabel("Shortcut")
        kill_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
        kill_row.addWidget(kill_lbl)
        kill_row.addStretch(1)
        self._sett_kill_key_btn = _HotkeyCaptureButton(
            str(S.get("kill_all_hotkey", "Ctrl+Shift+K") or "Ctrl+Shift+K")
        )
        self._sett_kill_key_btn.setFixedWidth(170)
        self._sett_kill_key_btn.setEnabled(bool(S.get("kill_all_hotkey_enabled", False)))
        self._sett_kill_key_btn.setToolTip(
            "Click, then press the key you want (e.g. Space, F8, Ctrl+K). Escape cancels."
        )
        self._sett_kill_key_btn.recording_started.connect(self._unregister_kill_hotkey)
        self._sett_kill_key_btn.recording_canceled.connect(
            lambda: (self._apply_kill_hotkey(show_error=False), self._sett_kill_key_btn.clearFocus())
        )
        self._sett_kill_key_btn.sequence_changed.connect(self._on_sett_kill_hotkey_changed)
        kill_row.addWidget(self._sett_kill_key_btn)
        f.addLayout(kill_row)
        kill_note = QLabel(
            "Tip: a single key like Space is taken over everywhere while this is on, "
            "including jumping in Roblox. A key you don't use in-game (F8, Pause) or a "
            "combo like Ctrl+Shift+K is safer."
        )
        kill_note.setWordWrap(True)
        kill_note.setStyleSheet(f"color: {NOTE}; font-size: 11px; background: transparent;")
        f.addWidget(kill_note)

        f = self._sheet_group(
            body, "Window shortcuts", "windows",
            "Minimize or bring back every Roblox window at once, or minimize them one at a time.",
        )
        self._sett_winkeys_chk = self._settings_checkbox(
            "window_keys_enabled", "Enable Minimize / Restore shortcuts",
            "Press the keys below anywhere (even inside Roblox) to minimize or bring back\n"
            "every Roblox window at once.",
            on_change=self._on_sett_window_keys_enabled,
        )
        f.addWidget(self._sett_winkeys_chk)
        self._sett_winkey_btns = {}
        for setting_key, label, default in (
            ("minimize_all_hotkey", "Minimize all Roblox", window_grid_mod.DEFAULT_MINIMIZE_HOTKEY),
            ("restore_all_hotkey", "Restore all Roblox", window_grid_mod.DEFAULT_RESTORE_HOTKEY),
            ("minimize_one_hotkey", "Minimize one Roblox", window_grid_mod.DEFAULT_MINIMIZE_ONE_HOTKEY),
        ):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
            row.addWidget(lbl)
            row.addStretch(1)
            btn = _HotkeyCaptureButton(str(S.get(setting_key, default) or default))
            btn.setFixedWidth(170)
            btn.setEnabled(bool(S.get("window_keys_enabled", False)))
            btn.setToolTip("Click, then press the key you want. Escape cancels.")
            btn.recording_started.connect(self._unregister_window_keys)
            btn.recording_canceled.connect(
                lambda b=btn: (self._apply_window_keys(show_error=False), b.clearFocus())
            )
            btn.sequence_changed.connect(
                lambda seq, k=setting_key, b=btn: self._on_sett_window_key_changed(k, seq, b)
            )
            row.addWidget(btn)
            f.addLayout(row)
            self._sett_winkey_btns[setting_key] = btn
        winkeys_note = QLabel(
            "While on, these keys are taken over everywhere (e.g. F11 won't switch fullscreen). "
            "Windows hidden by the Headless Manager stay hidden. F12 can't be used: Windows reserves it."
        )
        winkeys_note.setWordWrap(True)
        winkeys_note.setStyleSheet(f"color: {NOTE}; font-size: 11px; background: transparent;")
        f.addWidget(winkeys_note)

        self._build_window_grid_group(body, S)

    # Roblox window size ------------------------------------------------------
    def _build_window_size_section(self, f: QVBoxLayout, _sec) -> None:
        choice = window_size_mod.load_choice()
        f.addWidget(_sec("WINDOW SIZE"))
        desc = QLabel(
            "Make every Roblox window the same size, e.g. small windows to fit many accounts on "
            "one screen. Smaller windows also use less GPU. Sizes include the title bar."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(desc)

        def _row(label: str, widget: QWidget) -> QWidget:
            holder = QWidget()
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(label)
            lbl.setMinimumWidth(90)
            lay.addWidget(lbl)
            lay.addWidget(widget)
            lay.addStretch(1)
            f.addWidget(holder)
            return holder

        self._ws_mode_combo = QComboBox()
        for key, label in WINDOW_SIZE_MODES:
            self._ws_mode_combo.addItem(label, key)
        self._ws_mode_combo.setCurrentIndex(max(0, self._ws_mode_combo.findData(choice.mode)))
        _row("Window size", self._ws_mode_combo)

        self._ws_size_combo = QComboBox()
        for width, height in window_size_mod.PRESET_SIZES:
            self._ws_size_combo.addItem(_window_size_label(width, height), [width, height])
        self._ws_size_combo.addItem("Custom…", None)
        index = self._ws_size_combo.findData(list(choice.size))
        self._ws_size_combo.setCurrentIndex(index if index >= 0 else self._ws_size_combo.count() - 1)
        self._ws_size_row = _row("Size", self._ws_size_combo)

        custom = QWidget()
        custom_lay = QHBoxLayout(custom)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        self._ws_w_spin, self._ws_h_spin = QSpinBox(), QSpinBox()
        for spin, value in ((self._ws_w_spin, choice.size[0]), (self._ws_h_spin, choice.size[1])):
            spin.setRange(window_size_mod.MIN_SIDE, window_size_mod.MAX_SIDE)
            spin.setValue(value)
            spin.setFixedWidth(70)
        custom_lay.addWidget(self._ws_w_spin)
        custom_lay.addWidget(QLabel("×"))
        custom_lay.addWidget(self._ws_h_spin)
        self._ws_custom_row = _row("Custom", custom)

        self._ws_layout_combo = QComboBox()
        for cols, rows in window_size_mod.LAYOUTS:
            self._ws_layout_combo.addItem(f"{cols} × {rows}  ({cols * rows} windows)", [cols, rows])
        index = self._ws_layout_combo.findData(list(choice.layout))
        self._ws_layout_combo.setCurrentIndex(max(0, index))
        self._ws_layout_row = _row("Layout", self._ws_layout_combo)

        self._ws_unlock_chk = QCheckBox("Allow smaller than Roblox's minimum (experimental)")
        self._ws_unlock_chk.setChecked(choice.allow_smaller)
        self._ws_unlock_chk.setToolTip(
            "Roblox won't let its windows get smaller than about 1020 × 797 on newer versions.\n"
            "Windows only enforces that limit on windows with a resize border, so this removes\n"
            "the border before resizing. Roblox may still push the size back, and its menus may\n"
            "look cramped when tiny. While on, you can't drag-resize those windows by hand;\n"
            "set Window size to Off to give the border back.")
        f.addWidget(self._ws_unlock_chk)
        self._ws_lock_chk = QCheckBox("Lock window size")
        self._ws_lock_chk.setChecked(choice.lock)
        self._ws_lock_chk.setToolTip(
            "Hold every window the app sized at that size (and its Window Grid spot).\n"
            "If Roblox or anything else changes it, it's put back within half a second.\n"
            "Minimized windows are left alone.")
        f.addWidget(self._ws_lock_chk)

        self._ws_preview = _WindowSizePreview()
        f.addWidget(self._ws_preview)
        self._ws_info_lbl = QLabel()
        self._ws_info_lbl.setWordWrap(True)
        self._ws_info_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        f.addWidget(self._ws_info_lbl)

        buttons = QHBoxLayout()
        self._ws_preview_btn = QPushButton("Preview")
        self._ws_preview_btn.setToolTip(
            "See the real size: one open Roblox window is resized for 15 seconds (Keep or Undo).\n"
            "With no Roblox open, an outline of that size is shown instead.")
        self._ws_preview_btn.clicked.connect(self._on_window_size_preview)
        self._ws_apply_btn = QPushButton("Apply to open windows now")
        self._ws_apply_btn.clicked.connect(self._on_window_size_apply)
        self._ws_test_btn = QPushButton("Test resize")
        self._ws_test_btn.setToolTip(
            "Tries every way of resizing on one open Roblox window (it's put back after each)\n"
            "and tells you which one Roblox accepts. If one works, the app uses it from then on.")
        self._ws_test_btn.clicked.connect(self._on_window_size_test)
        buttons.addWidget(self._ws_preview_btn)
        buttons.addWidget(self._ws_apply_btn)
        buttons.addWidget(self._ws_test_btn)
        buttons.addStretch(1)
        f.addLayout(buttons)
        self._ws_result_lbl = QLabel("")
        self._ws_result_lbl.setWordWrap(True)
        self._ws_result_lbl.setStyleSheet(f"color: {ACCENT_TEXT}; font-size: 11px;")
        f.addWidget(self._ws_result_lbl)
        self._bridge.window_size_done.connect(self._on_window_size_done)
        self._bridge.window_size_test_done.connect(self._on_window_size_test_done)
        # Changing the size applies it to open windows after a short pause.
        self._ws_apply_timer = QTimer(self)
        self._ws_apply_timer.setSingleShot(True)
        self._ws_apply_timer.setInterval(800)
        self._ws_apply_timer.timeout.connect(self._on_window_size_apply)

        for signal in (self._ws_mode_combo.currentIndexChanged, self._ws_size_combo.currentIndexChanged,
                       self._ws_layout_combo.currentIndexChanged, self._ws_w_spin.valueChanged,
                       self._ws_h_spin.valueChanged, self._ws_unlock_chk.toggled,
                       self._ws_lock_chk.toggled):
            signal.connect(self._on_window_size_changed)
        self._on_window_size_changed(apply=False)

    def _window_size_choice(self):
        mode = self._ws_mode_combo.currentData() or window_size_mod.MODE_OFF
        preset = self._ws_size_combo.currentData()
        size = tuple(preset) if preset else (self._ws_w_spin.value(), self._ws_h_spin.value())
        layout = tuple(self._ws_layout_combo.currentData() or window_size_mod.DEFAULT_LAYOUT)
        return window_size_mod.Choice(mode, size, layout, self._ws_unlock_chk.isChecked(),
                                      self._ws_lock_chk.isChecked())

    @staticmethod
    def _screen_work_area() -> tuple[int, int, int, int]:
        screen = QApplication.primaryScreen()
        if screen is None:
            return 0, 0, 1920, 1040
        geo, ratio = screen.availableGeometry(), screen.devicePixelRatio()
        return (round(geo.x() * ratio), round(geo.y() * ratio),
                round((geo.x() + geo.width()) * ratio), round((geo.y() + geo.height()) * ratio))

    def _on_window_size_changed(self, *_args, apply: bool = True) -> None:
        choice = self._window_size_choice()
        window_size_mod.save_choice(choice)
        mode = choice.mode
        self._ws_unlock_chk.setVisible(mode != window_size_mod.MODE_OFF)
        self._ws_lock_chk.setVisible(mode != window_size_mod.MODE_OFF)
        self._grey_out_refused_sizes(choice)
        if apply:
            self._ws_apply_timer.start()  # also gives borders back when turned Off
        self._ws_size_row.setVisible(mode == window_size_mod.MODE_SIZE)
        self._ws_custom_row.setVisible(mode == window_size_mod.MODE_SIZE and self._ws_size_combo.currentData() is None)
        self._ws_layout_row.setVisible(mode == window_size_mod.MODE_FIT)
        work = self._screen_work_area()
        size = window_size_mod.target_size(choice, work)
        if mode == window_size_mod.MODE_FIT:
            cols, rows = choice.layout
        else:
            cols, rows = window_size_mod.how_many_fit(work, size)
        self._ws_preview.set_layout(work, size, cols, rows, mode != window_size_mod.MODE_OFF)
        if mode == window_size_mod.MODE_OFF:
            text = "Off: Roblox picks its own window size."
        else:
            text = f"{cols} × {rows} = {cols * rows} windows fit on your screen · {size[0]} × {size[1]} each"
        refused = window_size_mod.refused_size()
        if mode != window_size_mod.MODE_OFF and window_size_mod.below_minimum(size, refused):
            text += f"\nRoblox wouldn't go smaller than {refused[0]} × {refused[1]} on this PC"
            text += " (trying anyway: experimental option on)." if choice.allow_smaller else \
                ". Try the experimental option above."
        self._ws_info_lbl.setText(text)
        enabled = mode != window_size_mod.MODE_OFF
        self._ws_preview_btn.setEnabled(enabled)
        self._ws_apply_btn.setEnabled(enabled)
        self._ws_test_btn.setEnabled(enabled)

    def _on_window_size_test(self) -> None:
        choice = self._window_size_choice()
        width, height = window_size_mod.target_size(choice, self._screen_work_area())
        self._ws_test_btn.setEnabled(False)
        self._ws_result_lbl.setText(f"Testing {width} × {height} on one Roblox window…")

        def _worker():
            try:
                windows = window_size_mod.roblox_windows()
                if not windows:
                    report, worked = "Open one Roblox window first, then press Test resize.", False
                else:
                    results = window_size_mod.test_methods(windows[0], width, height)
                    labels = dict(window_size_mod.METHODS)
                    lines = [f"{'✓' if ok else '✗'} {labels[m]}: {w} × {h}" for m, (w, h), ok in results]
                    worked = any(ok for _m, _s, ok in results)
                    head = ("Found a way that works. The experimental option is now on and uses it."
                            if worked else
                            "Roblox refused every method: it won't go below its minimum on this PC.")
                    report = head + "\n" + "\n".join(lines)
            except Exception as exc:
                report, worked = f"Test failed: {exc}", False
            try:
                self._bridge.window_size_test_done.emit(report, worked)
            except RuntimeError:
                pass
        threading.Thread(target=_worker, daemon=True, name="WindowSizeTest").start()

    def _on_window_size_test_done(self, report: str, worked: bool) -> None:
        if not isValid(self._ws_result_lbl):
            return
        self._ws_test_btn.setEnabled(True)
        if worked and not self._ws_unlock_chk.isChecked():
            self._ws_unlock_chk.setChecked(True)  # applies the size with the working method
        self._ws_result_lbl.setText(report)

    def _grey_out_refused_sizes(self, choice) -> None:
        """Sizes Roblox refused here are greyed out, unless the experimental option is on."""
        refused = None if choice.allow_smaller else window_size_mod.refused_size()
        model = self._ws_size_combo.model()
        for row in range(self._ws_size_combo.count()):
            data = self._ws_size_combo.itemData(row)
            item = model.item(row) if hasattr(model, "item") else None
            if item is None:
                continue
            item.setEnabled(not (data and window_size_mod.below_minimum(tuple(data), refused)))
        work = self._screen_work_area()
        layout_model = self._ws_layout_combo.model()
        for row in range(self._ws_layout_combo.count()):
            cols, rows = self._ws_layout_combo.itemData(row)
            item = layout_model.item(row) if hasattr(layout_model, "item") else None
            if item is not None:
                item.setEnabled(not window_size_mod.below_minimum(
                    window_size_mod.fit_size(work, (cols, rows)), refused))

    def _on_window_size_apply(self) -> None:
        choice = self._window_size_choice()
        if choice.mode != window_size_mod.MODE_OFF:
            self._ws_result_lbl.setText("Resizing…")

        def _worker():
            result = window_size_mod.apply(choice)
            text = "" if choice.mode == window_size_mod.MODE_OFF else result.summary()
            try:
                self._bridge.window_size_done.emit(text)
            except RuntimeError:
                pass
        threading.Thread(target=_worker, daemon=True, name="WindowSizeApply").start()

    def _on_window_size_done(self, text: str) -> None:
        label = getattr(self, "_ws_result_lbl", None)
        if label is not None and isValid(label):
            label.setText(text)
        if hasattr(self, "_ws_mode_combo") and isValid(self._ws_mode_combo):
            self._on_window_size_changed(apply=False)  # refresh the "won't go smaller" note

    def _on_window_size_preview(self) -> None:
        choice = self._window_size_choice()
        work = self._screen_work_area()
        width, height = window_size_mod.target_size(choice, work)
        try:
            windows = window_size_mod.roblox_windows()
        except Exception:
            windows = []
        if not windows:
            _SizeOutline.show_for(self, width, height)
            return
        hwnd = windows[0]
        window_size_mod._kept.pop(hwnd, None)  # a locked window would jump straight back
        import win32gui
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            left, top, right, bottom = work
            x = left + max(0, (right - left - width) // 2)
            y = top + max(0, (bottom - top - height) // 2)
            actual = window_size_mod.resize_window(hwnd, width, height, x, y, choice.allow_smaller)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
        except Exception as exc:
            print(f"[Window Size] Preview failed: {exc}")
            _SizeOutline.show_for(self, width, height)
            return
        refused = actual[0] > width + 4 or actual[1] > height + 4
        self._ws_result_lbl.setText(
            f"Roblox kept {actual[0]} × {actual[1]}: it won't go smaller than that on this PC."
            if refused else f"Preview: {actual[0]} × {actual[1]}.")
        self._on_window_size_changed(apply=False)

        def _undo():
            try:
                win32gui.SetWindowPlacement(hwnd, placement)
            except Exception:
                pass

        def _keep():
            if choice.mode == window_size_mod.MODE_OFF:
                return
            window_size_mod.save_choice(choice)
            self._ws_apply_timer.start()  # size (and lock) every open window
        _PreviewKeepBar.ask(self, f"Previewing {actual[0]} × {actual[1]} on a Roblox window", _keep, _undo)

    def _start_window_size_watcher(self) -> None:
        self._window_size_watcher = window_size_mod.WindowSizeWatcher()
        self._window_size_watcher.start()

    def _build_window_grid_group(self, body: QVBoxLayout, S: dict) -> None:
        f = self._sheet_group(
            body, "Window Grid shortcut", "windows",
            "Press this key anywhere to arrange every visible Roblox window into a grid, first "
            "opened top-left. With a Window Size set (Settings → Roblox), it uses that size and "
            "layout and locks the windows there. Anti AFK goes through the windows in this order.",
        )
        self._sett_window_grid_chk = self._settings_checkbox(
            "window_grid_enabled", "Enable Window Grid Shortcut",
            "Register a global keybind that arranges every visible Roblox window\n"
            "into an equal grid on the monitor under your cursor.",
            on_change=self._on_sett_window_grid,
        )
        f.addWidget(self._sett_window_grid_chk)
        row = QHBoxLayout()
        lbl = QLabel("Shortcut")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
        lbl.setToolTip("Click the button, press a shortcut, then release its main key.")
        row.addWidget(lbl)
        row.addStretch(1)
        self._sett_window_grid_key_btn = _HotkeyCaptureButton(
            str(S.get("window_grid_keybind", window_grid_mod.DEFAULT_HOTKEY) or window_grid_mod.DEFAULT_HOTKEY)
        )
        self._sett_window_grid_key_btn.setFixedWidth(170)
        self._sett_window_grid_key_btn.setEnabled(bool(S.get("window_grid_enabled", False)))
        self._sett_window_grid_key_btn.setToolTip(
            "Click to record a global keybind. Press Escape to cancel.\n"
            f"Default: {window_grid_mod.DEFAULT_HOTKEY}"
        )
        self._sett_window_grid_key_btn.recording_started.connect(self._unregister_window_grid_hotkey)
        self._sett_window_grid_key_btn.recording_canceled.connect(
            self._restore_window_grid_hotkey_after_recording)
        self._sett_window_grid_key_btn.sequence_changed.connect(self._on_sett_window_grid_keybind)
        row.addWidget(self._sett_window_grid_key_btn)
        f.addLayout(row)

    def _build_performance_settings(self, body: QVBoxLayout) -> None:
        S = actions.load_ui_settings()

        def _perf_slider_row(key: str, lo: int, hi: int, default: int, suffix: str):
            row = QHBoxLayout()
            row.setSpacing(12)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setCursor(Qt.CursorShape.PointingHandCursor)
            box = QLineEdit()
            box.setValidator(QIntValidator(lo, hi, box))
            box.setFixedWidth(64)
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.setStyleSheet("font-size: 14px; font-weight: 700;")
            unit = QLabel(suffix)
            unit.setFixedWidth(34)
            unit.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
            try:
                start = int(S.get(key, default))
            except (TypeError, ValueError):
                start = default
            start = max(lo, min(hi, start))
            slider.setValue(start)
            box.setText(str(start))

            def _from_slider(v):
                if box.text() != str(v):
                    box.setText(str(v))
                actions.save_ui_setting(key, int(v))
                self._update_perf_status()

            def _from_box(text):
                try:
                    v = int(text)
                except ValueError:
                    return
                if lo <= v <= hi and slider.value() != v:
                    slider.setValue(v)

            slider.valueChanged.connect(_from_slider)
            box.textEdited.connect(_from_box)
            box.editingFinished.connect(lambda: box.setText(str(slider.value())))
            row.addWidget(slider, 1)
            row.addWidget(box)
            row.addWidget(unit)
            return row, slider

        f = self._sheet_group(body, "Frame rate", "gauge", "Cap the FPS of Roblox windows (5 to 240).")
        self._perf_fps_chk = self._settings_checkbox(
            "perf_fps_enabled", "Limit FPS",
            "Cap the frame rate of Roblox windows (5 to 240 FPS).",
            on_change=lambda _on: self._update_perf_status(),
        )
        f.addWidget(self._perf_fps_chk)
        _fps_row, self._perf_fps_slider = _perf_slider_row(
            "perf_fps", roblox_settings_mod.PERFORMANCE_FPS_MIN,
            roblox_settings_mod.PERFORMANCE_FPS_MAX, 30, "FPS")
        f.addLayout(_fps_row)

        f = self._sheet_group(body, "Graphics quality", "sliders", "1 = lowest, 10 = highest.")
        self._perf_quality_chk = self._settings_checkbox(
            "perf_quality_enabled", "Set Graphics Quality",
            "Roblox graphics level: 1 = lowest, 10 = highest.",
            on_change=lambda _on: self._update_perf_status(),
        )
        f.addWidget(self._perf_quality_chk)
        _q_row, self._perf_quality_slider = _perf_slider_row(
            "perf_quality", roblox_settings_mod.PERFORMANCE_QUALITY_MIN,
            roblox_settings_mod.PERFORMANCE_QUALITY_MAX, 1, "level")
        f.addLayout(_q_row)

        f = self._sheet_group(
            body, "RAM usage", "gauge",
            "Show how much RAM (and CPU) each running Roblox account uses, next to its name, "
            "and the whole PC's RAM and CPU above the account list.",
        )
        self._sett_monitoring_chk = self._settings_checkbox(
            "presence_indicator", "Show RAM Usage of Running Accounts",
            "Show how much RAM (and CPU) each running Roblox account uses, next to its name.\n"
            "Updates every 5 seconds.",
            on_change=self._on_sett_presence_indicator,
        )
        f.addWidget(self._sett_monitoring_chk)
        sub = QVBoxLayout()
        sub.setContentsMargins(26, 0, 0, 0)
        sub.setSpacing(10)
        self._sett_ram_percent_chk = self._settings_checkbox(
            "ram_show_percent", "Also show RAM as % of total PC memory",
            "Example: 812 MB (5%). Uses your total installed memory.",
            on_change=lambda _on: self._update_activity_rows(),
        )
        sub.addWidget(self._sett_ram_percent_chk)
        self._sett_ram_total_chk = self._settings_checkbox(
            PC_USAGE_SETTING, "Show PC RAM and CPU usage",
            "Shows the whole computer's memory and CPU above the account list, e.g.\n"
            "\"4 running · RAM 11.2 / 16 GB (70%) · CPU 23%\", so you don't need Task Manager.\n"
            "Works even with the per-account RAM display off. Turns orange near the limit.",
            default=bool(actions.get_ui_setting("ram_show_total", True)),
            on_change=lambda _on: self._update_ram_total(),
        )
        f.addLayout(sub)
        f.addWidget(self._sett_ram_total_chk)

        f = self._sheet_group(
            body, "RAM optimization", "bolt",
            "Periodically trims Roblox memory to lower RAM use. May cause crashes, use with caution.",
        )
        self._sett_boost_ram_chk = self._settings_checkbox(
            "optimize_roblox_ram", "Boost Roblox RAM Limit (May Cause Crash)",
            "Periodically trim Roblox working-set memory to reduce RAM usage.\n"
            "May causes crash when using this feature, use with caution.",
            on_change=self._on_sett_boost_ram,
        )
        f.addWidget(self._sett_boost_ram_chk)
        ram_row = QHBoxLayout()
        ram_row.setContentsMargins(26, 0, 0, 0)
        ram_lbl = QLabel("Low RAM Limit (MB)")
        ram_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; background: transparent;")
        ram_lbl.setToolTip(
            "Target memory limit per Roblox process in megabytes.\n"
            "Processes using more than this will have their working set trimmed."
        )
        ram_row.addWidget(ram_lbl)
        ram_row.addStretch(1)
        self._sett_ram_spin = QSpinBox()
        self._sett_ram_spin.setRange(100, 8192)
        self._sett_ram_spin.setValue(int(S.get("optimize_roblox_ram_limit_mb", RAM_LIMIT_DEFAULT_MB)))
        self._sett_ram_spin.setSuffix(" MB")
        self._sett_ram_spin.setFixedWidth(110)
        self._sett_ram_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sett_ram_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._sett_ram_spin.setEnabled(bool(S.get("optimize_roblox_ram", False)))
        self._sett_ram_spin.valueChanged.connect(
            lambda v: actions.save_ui_setting("optimize_roblox_ram_limit_mb", v)
        )
        ram_row.addWidget(self._sett_ram_spin)
        f.addLayout(ram_row)

        f = self._sheet_group(
            body, "Apply", "enter",
            "Applies to every Roblox window that starts afterwards "
            "(all accounts share one Roblox settings file).",
        )
        self._perf_launch_chk = self._settings_checkbox(
            "perf_apply_on_launch", "Apply on every launch",
            "Write these values to Roblox's settings right before each account launches.",
            default=True,
            on_change=lambda _on: self._update_perf_status(),
        )
        f.addWidget(self._perf_launch_chk)

        self._perf_apply_btn = QPushButton("Apply now")
        self._perf_apply_btn.setObjectName("primary")
        self._perf_apply_btn.setAutoDefault(False)
        self._perf_apply_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self._perf_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._perf_apply_btn.setToolTip("Write the values now. Windows already open keep their settings until relaunched.")
        self._perf_apply_btn.clicked.connect(self._on_perf_apply_now)
        f.addWidget(self._perf_apply_btn)
        self._perf_status_lbl = QLabel("")
        self._perf_status_lbl.setWordWrap(True)
        self._perf_status_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        f.addWidget(self._perf_status_lbl)
        self._update_perf_status()

    @staticmethod
    def _make_circular_pixmap(data: bytes, size: int = avatars.AVATAR_SIZE) -> QPixmap: # Avatar helpers
        src = QPixmap()
        src.loadFromData(data)
        if src.isNull():
            return QPixmap()
        src = src.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, src)
        painter.end()
        return result

    def _make_placeholder_pixmap(self, size: int = avatars.AVATAR_SIZE) -> QPixmap: # Gray icon
        cached = self._placeholder_pixmaps.get(size)
        if cached is not None:
            return cached
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(SELECT))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.end()
        self._placeholder_pixmaps[size] = result
        return result

    @staticmethod
    def _create_invalid_badge(container: QWidget) -> QLabel:
        badge_size = 6
        ring = 1
        badge = QLabel(container)
        badge.setFixedSize(badge_size + ring * 2, badge_size + ring * 2)
        badge.move(0, 0)
        badge.setStyleSheet(f"""
            QLabel {{
                background: #E8A020;
                border-radius: {(badge_size + ring * 2) // 2}px;
                border: {ring}px solid {BG};
            }}
        """)
        badge.setToolTip(
            "Cookie validation received repeated unauthorized responses.\n"
            "You can still try launching this account."
        )
        return badge

    def _open_private_server_manager(self):
        dialog = getattr(self, '_private_server_manager', None)
        if dialog is not None and isValid(dialog):
            dialog.showNormal()
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = _PrivateServerManagerDialog(self.manager, self._get_selected_username(), self)
        dialog.apply_server.connect(self._apply_private_server_fields)
        self._private_server_manager = dialog
        dialog.show()

    def _apply_private_server_fields(self, place_id, private_server):
        self._place_id_edit.setCurrentText(str(place_id or ''))
        self._private_server_edit.setCurrentText(str(private_server or ''))

    def _get_selected_username(self) -> str | None:
        item = self._account_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _get_selected_usernames(self) -> list[str]:
        items = self._account_list.selectedItems()
        names = []
        for it in items:
            u = it.data(Qt.ItemDataRole.UserRole)
            if u:  # skip group-header rows which have no UserRole data
                names.append(u)
        return names

    def _confirm_launch(self, action_label: str, accounts: list[str]) -> bool:
        if not actions.load_ui_settings().get("confirm_before_launch", False):
            return True
        if len(accounts) == 1:
            msg = f"Launch {action_label} for {accounts[0]}?"
        else:
            names_preview = ", ".join(accounts[:5])
            if len(accounts) > 5:
                names_preview += f" (+{len(accounts)-5} more)"
            msg = f"Launch {action_label} for {len(accounts)} accounts:\n{names_preview}"
        reply = QMessageBox.question(
            self, "Confirm Launch", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _refresh_account_list(self): # Main account list population and refresh
        if hasattr(self, "_drag_filter"):
            self._drag_filter.abort()
        cur_item = self._account_list.currentItem()
        cur_username = cur_item.data(Qt.ItemDataRole.UserRole) if cur_item else None
        selected_usernames = {
            it.data(Qt.ItemDataRole.UserRole) for it in self._account_list.selectedItems()
        }
        scroll_value = self._account_list.verticalScrollBar().value()
        self._account_list.clear()
        self._avatar_labels.clear()
        self._presence_dots.clear()
        self._activity_widgets.clear()
        self._activity_labels.clear()
        self._invalid_badges.clear()
        self._account_avatar_containers.clear()
        self._account_name_labels.clear()
        self._account_rows.clear()
        self._status_labels.clear()
        self._lock_labels.clear()
        self._note_labels.clear()
        account_items = list(self.manager.accounts.items())
        activity_enabled = bool(
            actions.load_ui_settings().get("presence_indicator", False)
        )

        # Filter by groups
        if self._current_group is not None:
            assignments = groups.get_assignments()
            account_items = [
                (u, d) for u, d in account_items
                if assignments.get(u) == self._current_group
            ]

        search_query = getattr(self, "_account_query", "")
        if search_query:
            account_items = account_filter.filter_accounts(
                account_items, search_query, groups.get_assignments()
            )

        if not account_items:
            item = QListWidgetItem(
                "No accounts match your search."
                if search_query
                else "No accounts, use 'Add Account' to add one."
            )
            item.setForeground(QColor(MUTED))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._account_list.addItem(item)
            self._rebuild_group_bar()
            return

        AV = ROW_AVATAR_SIZE
        ITEM_H = AV + 16

        for username, data in account_items:
            note = data.get("note", "") if isinstance(data, dict) else ""

            item = QListWidgetItem("")
            item.setSizeHint(QSize(0, ITEM_H))
            item.setData(Qt.ItemDataRole.UserRole, username)

            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(8, 0, 8, 0)
            row_lay.setSpacing(12)

            av_container = QWidget()
            av_container.setFixedSize(AV, AV)

            av_lbl = QLabel(av_container)
            av_lbl.setFixedSize(AV, AV)
            av_lbl.setAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter
            )
            av_lbl.setPixmap(self._make_placeholder_pixmap(AV))

            DOT_SIZE = 9
            RING = 2
            dot_lbl = QLabel(av_container)
            dot_lbl.setFixedSize(DOT_SIZE + RING * 2, DOT_SIZE + RING * 2)
            dot_lbl.move(AV - DOT_SIZE - RING, AV - DOT_SIZE - RING)
            dot_lbl.setStyleSheet(f"""
                QLabel {{
                    background: {ONLINE_GREEN};
                    border-radius: {(DOT_SIZE + RING * 2) // 2}px;
                    border: {RING}px solid {LIST_BG};
                }}
            """)
            is_online = username in self._online_usernames
            dot_lbl.setVisible(is_online)
            self._presence_dots[username] = dot_lbl

            row_lay.addWidget(av_container)
            self._avatar_labels[username] = av_lbl
            self._account_avatar_containers[username] = av_container

            flagged = self._cv_mod.is_flagged(data) if isinstance(data, dict) else False

            if flagged:
                bad_lbl = self._create_invalid_badge(av_container)
                self._invalid_badges[username] = bad_lbl

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(0)
            text_col.addStretch(1)
            top_line = QHBoxLayout()
            top_line.setContentsMargins(0, 0, 0, 0)
            top_line.setSpacing(8)

            name_lbl = QLabel(username)
            name_lbl.setObjectName("accountName")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            if flagged:
                name_lbl.setStyleSheet("color: #E8A020; font-style: italic;")
                name_lbl.setToolTip(
                    "Cookie validation received repeated unauthorized responses.\n"
                    "You can still try launching this account."
                )
            top_line.addWidget(name_lbl)
            self._account_name_labels[username] = name_lbl

            # Note display (kept even when empty so a new note appears in place).
            # Created inside the row: a parentless label made visible would
            # flash on screen as its own little window.
            sep = QLabel("|", row)
            sep.setObjectName("noteSep")
            sep.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            note_lbl = QLabel(note, row)
            note_lbl.setObjectName("noteText")
            note_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            top_line.addWidget(sep)
            top_line.addWidget(note_lbl)
            sep.setVisible(bool(note))
            note_lbl.setVisible(bool(note))
            self._note_labels[username] = (sep, note_lbl)

            if activity_enabled:
                activity_container = QWidget(row)
                activity_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                activity_lay = QHBoxLayout(activity_container)
                activity_lay.setContentsMargins(0, 0, 0, 0)
                activity_lay.setSpacing(4)

                ram_sep = QLabel("|")
                ram_sep.setObjectName("performanceSep")
                ram_lbl = QLabel("0 MB")
                ram_lbl.setObjectName("ramUsage")
                ram_lbl.setAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )

                cpu_sep = QLabel("|")
                cpu_sep.setObjectName("performanceSep")
                cpu_lbl = QLabel("0.0%")
                cpu_lbl.setObjectName("cpuUsage")
                cpu_lbl.setAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )

                activity_lay.addWidget(ram_sep)
                activity_lay.addWidget(ram_lbl)
                activity_lay.addWidget(cpu_sep)
                activity_lay.addWidget(cpu_lbl)
                top_line.addWidget(activity_container)
                self._activity_widgets[username] = activity_container
                self._activity_labels[username] = (ram_lbl, cpu_lbl)
                activity_container.setVisible(username in self._activity_snapshot)
            top_line.addStretch(1)
            text_col.addLayout(top_line)

            status_line = QHBoxLayout()
            status_line.setContentsMargins(0, 0, 0, 0)
            status_line.setSpacing(6)
            status_dot = QLabel()
            status_dot.setFixedSize(8, 8)
            status_text = QLabel()
            status_text.setObjectName("accountStatus")
            self._apply_account_status(status_dot, status_text, is_online)
            status_line.addWidget(status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
            status_line.addWidget(status_text)
            status_line.addStretch(1)
            text_col.addLayout(status_line)
            text_col.addStretch(1)
            self._status_labels[username] = (status_dot, status_text)

            row_lay.addLayout(text_col, 1)

            lock_lbl = QLabel(row)
            lock_lbl.setPixmap(icons.pixmap("lock", ACCENT_BRIGHT, 18))
            lock_lbl.setToolTip("Locked: Kill All Roblox keeps this account's Roblox open")
            lock_lbl.setStyleSheet("background: transparent;")
            lock_lbl.setVisible(bool(isinstance(data, dict) and data.get(actions.LOCK_KEY)))
            row_lay.addWidget(lock_lbl)
            self._lock_labels[username] = lock_lbl

            menu_btn = QToolButton()
            menu_btn.setObjectName("rowMenu")
            menu_btn.setIcon(icons.make_icon("dots", MUTED, active=TEXT, size=20))
            menu_btn.setIconSize(QSize(20, 20))
            menu_btn.setToolTip("Account options")
            menu_btn.setAccessibleName(f"Options for {username}")
            menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            menu_btn.clicked.connect(
                lambda _=False, u=username, b=menu_btn: self._open_account_row_menu(u, b)
            )
            row_lay.addWidget(menu_btn)
            row.setFixedHeight(ITEM_H)

            if flagged:
                row.setStyleSheet(
                    "QWidget { background: rgba(200, 50, 50, 0.06); }"
                )

            self._account_list.addItem(item)
            self._account_list.setItemWidget(item, row)
            self._account_rows[username] = row

        restored_current = False
        for i in range(self._account_list.count()):
            it = self._account_list.item(i)
            username = it.data(Qt.ItemDataRole.UserRole)
            if username in selected_usernames:
                it.setSelected(True)
            if username == cur_username:
                self._account_list.setCurrentItem(it)
                restored_current = True

        if not restored_current and self._account_list.count() > 0:
            self._account_list.setCurrentRow(0)

        # setCurrentItem/setCurrentRow above auto-scroll to keep the current
        # item visible - restore the scroll position the user actually had.
        self._account_list.verticalScrollBar().setValue(scroll_value)

        self._rebuild_group_bar()
        self._load_avatars_async()
        self._update_activity_rows()


    _ALL_GROUPS = "\x00all"

    def _rebuild_group_bar(self):
        combo = getattr(self, "_group_combo", None)
        if combo is None or not isValid(combo):
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All", self._ALL_GROUPS)
        for gname in groups.get_group_names():
            combo.addItem(gname, gname)
        index = 0 if self._current_group is None else combo.findData(self._current_group)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _on_group_combo_changed(self, index: int) -> None:
        data = self._group_combo.itemData(index)
        group_name = None if data in (None, self._ALL_GROUPS) else str(data)
        if group_name != self._current_group:
            self._on_group_tab_clicked(group_name)

    def _on_group_combo_context(self, pos) -> None:
        data = self._group_combo.currentData()
        if data in (None, self._ALL_GROUPS):
            return
        self._on_group_tab_context(pos, str(data), self._group_combo)

    def _on_group_tab_clicked(self, group_name: str | None):
        self._current_group = group_name
        self._rebuild_group_bar()
        self._refresh_account_list()

    def _on_group_tab_context(self, pos, group_name: str, btn: QPushButton):
        # Context menu for group tabs
        # Rename: change the group's name
        # Delete: remove the group, accounts become ungrouped
        menu = QMenu(self)
        act_rename = menu.addAction("Rename")
        act_delete = menu.addAction("Delete")
        chosen = menu.exec(btn.mapToGlobal(pos))
        if chosen == act_rename:
            self._on_rename_group(group_name)
        elif chosen == act_delete:
            self._on_delete_group(group_name)

    def _save_group_change(self, operation, *args):
        try:
            return operation(*args) is not False
        except OSError as exc:
            self._show_operation_error(OperationResult.failure(
                "GROUP_SAVE_FAILED", "Group Changes Could Not Be Saved",
                "Check that the application data folder is writable and try again.",
                detail=str(exc),
            ))
            return None

    def _on_add_group(self):
        name, ok = QInputDialog.getText(self, "New Group", "Group name:")
        if not ok or not name.strip():
            return
        saved = self._save_group_change(groups.create_group, name.strip())
        if saved is None:
            return
        if not saved:
            _show_error(self, "Error", f"Group '{name.strip()}' already exists.")
            return
        self._rebuild_group_bar()

    def _on_rename_group(self, old_name: str):
        new_name, ok = QInputDialog.getText(
            self, "Rename Group", "New name:", text=old_name
        )
        if not ok or not new_name.strip():
            return
        saved = self._save_group_change(groups.rename_group, old_name, new_name.strip())
        if saved is None:
            return
        if not saved:
            _show_error(self, "Error", "Could not rename, name may already exist.")
            return
        if self._current_group == old_name:
            self._current_group = new_name.strip()
        self._rebuild_group_bar()
        self._refresh_account_list()

    def _on_delete_group(self, name: str):
        reply = QMessageBox.question(
            self, "Delete Group",
            f"Delete group '{name}'?\nAccounts in this group will become ungrouped.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._save_group_change(groups.delete_group, name):
            return
        if self._current_group == name:
            self._current_group = None
        self._rebuild_group_bar()
        self._refresh_account_list()

    def _on_assign_to_group(self, usernames, group_name: str | None):
        if isinstance(usernames, str):
            usernames = [usernames]
        if not self._save_group_change(groups.set_accounts_group, list(usernames), group_name):
            return
        self._after_group_move(usernames, group_name)

    def _on_remove_from_group(self, usernames):
        self._on_assign_to_group(usernames, None)

    def _after_group_move(self, usernames, group_name: str | None) -> None:
        if getattr(self, "_account_query", ""):
            self._refresh_account_list()  # the search may match group names
            return
        if self._current_group is None or group_name == self._current_group:
            return  # the visible list does not change
        # Viewing one group: only the moved accounts leave the list.
        moved = set(usernames)
        for row in range(self._account_list.count() - 1, -1, -1):
            item = self._account_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in moved:
                self._account_list.takeItem(row)
        if self._account_list.count() == 0:
            self._refresh_account_list()

    def _load_avatars_async(self):
        avatars.sync_missing_avatar_cache(
            self.manager.accounts,
            on_avatar_ready=lambda u, b: self._bridge.avatar_ready.emit(u, b),
            on_complete=lambda: self.manager.save_accounts(),
        )

    def _on_avatar_ready(self, username: str, img_bytes: object):
        # Convert byte to circular pixmap
        try:
            pix = self._make_circular_pixmap(bytes(img_bytes), avatars.AVATAR_SIZE)
            if pix.isNull():
                return
            lbl = self._avatar_labels.get(username)
            if lbl is not None:
                lbl.setPixmap(self._make_circular_pixmap(bytes(img_bytes), ROW_AVATAR_SIZE))
            ar_lbl = getattr(self, "_ar_avatar_labels", {}).get(username)
            if ar_lbl is not None:
                ar_lbl.setPixmap(pix)
            # Feed avatar into drag floating label if user is being dragged
            df = getattr(self, "_drag_filter", None)
            if df and df._dragging and df._username == username:
                df.update_float_avatar(pix)
        except Exception:
            pass

    def _open_games_selector(self):
        dialog = _GamesSelectorDialog(self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_place_id:
                self._place_id_edit.setCurrentText(dialog.selected_place_id)
        finally:
            dialog.deleteLater()

    # Recent games
    def _on_sett_max_recent_games(self, value: int) -> None:
        actions.save_ui_setting("max_recent_games", value)
        self._refresh_recent_games()

    def _refresh_recent_games(self):
        self._recent_list.clear()
        for entry in actions.load_recent_games():
            pid = entry.get("place_id", "")
            name = entry.get("name", pid)
            private_server = entry.get("private_server", "")
            is_private = entry.get("private", bool(private_server))

            label = name if (name and name != pid) else pid

            if is_private:
                label = f"[P] {label}"

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, {"place_id": pid, "private_server": private_server})
            self._recent_list.addItem(item)

        apply_icons = getattr(self, "_apply_recent_icons", None)
        if callable(apply_icons):
            apply_icons()

    def _on_recent_game_context_menu(self, pos):
        item = self._recent_list.itemAt(pos)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        place_id = data.get("place_id", "")
        private_server = data.get("private_server", "")
        if not place_id:
            return
        self._recent_list.setCurrentItem(item)
        menu = QMenu(self)
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(self._recent_list.viewport().mapToGlobal(pos))
        menu.deleteLater()
        if chosen != delete_action:
            return
        try:
            actions.remove_recent_game(place_id, private_server)
        except OSError as exc:
            _show_error(self, "Delete Recent Game", f"The recent game could not be deleted:\n{exc}")
            return
        self._refresh_recent_games()

    def _on_recent_game_double_click(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        pid = data.get("place_id", "")
        private_server = data.get("private_server", "")
        if pid:
            self._place_id_edit.setCurrentText(pid)
            self._private_server_edit.setCurrentText(private_server)

    def _update_encryption_badge(self):
        text, color = actions.get_encryption_status(self.manager)
        color = {"#90EE90": "#34D399", "#87CEEB": "#60A5FA", "#FFB6C1": "#F87171"}.get(color, color)
        color = _status_color(color) if color else color
        badge = getattr(self, "_enc_badge", None)
        if badge is None or not isValid(badge):
            return
        if not (text and color):
            badge.hide()
            return
        badge.setText(f" {text}")
        badge.setIcon(icons.make_icon(
            "shield_check" if text != actions.ENCRYPTION_NONE_LABEL else "shield", color, size=16))
        badge.setToolTip(ENCRYPTION_TIPS.get(text, "") + "\nClick to open the encryption settings.")
        badge.setAccessibleName(f"Account protection: {text}")
        badge.setStyleSheet(
            f"QPushButton#encBadge {{ color: {color}; background: {color_themes.mix(BG, color, 0.10)};"
            f" border: 1px solid {color_themes.mix(BG, color, 0.40)}; border-radius: 13px;"
            f" padding: 4px 12px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton#encBadge:hover {{ background: {color_themes.mix(BG, color, 0.18)}; }}"
        )
        badge.show()

    def _open_encryption_settings(self) -> None:
        self._show_page(4)
        switch = getattr(self, "_settings_switch_cat", None)
        if callable(switch):
            switch(SETTINGS_MISC_TAB)
        section = getattr(self, "_enc_section_lbl", None)
        area = getattr(self, "_misc_settings_scroll", None)
        if section is not None and area is not None and isValid(section) and isValid(area):
            QTimer.singleShot(0, lambda: isValid(area) and area.ensureWidgetVisible(section, 0, 40))

    def _on_place_id_changed(self, text: str):
        self._game_name_label.setText("")
        self._game_name_timer.start(350)

    def _on_private_server_changed(self, text: str):
        self._game_name_label.setText("")
        self._game_name_timer.start(350)

    def _schedule_game_name_fetch(self):
        self._game_name_timer.start(350)

    def _fetch_game_name_for(self, place_id: str):
        def _cb(name):
            if name:
                truncated = name if len(name) <= 28 else name[:26] + ".."
                display = f"Current: {truncated}"
            else:
                display = ""

            self._bridge.game_name_ready.emit(display) # emit to main thread

        actions.fetch_game_name_async(place_id, _cb)

    def _do_fetch_game_name(self):
        place_id = self._place_id_edit.currentText().strip()
        private = self._private_server_edit.currentText().strip()
        actions.save_ui_setting("last_place_id", place_id)
        actions.save_ui_setting("last_private_server", private)
        if place_id:
            if not place_id.isdigit():
                self._game_name_label.setText("")
                return
            self._fetch_game_name_for(place_id)
            return

        if not private:
            self._game_name_label.setText("")
            return

        # Place ID box is empty: resolve a place id from the Private Server
        # Link off the UI thread (the "now" share-link format needs a network call).
        usernames = self._get_selected_usernames()
        cookie = self.manager.accounts.get(usernames[0], {}).get("cookie", "") if usernames else ""

        def _resolve_worker():
            resolved_pid, _ = RobloxAPI.resolve_share_url(private, cookie=cookie)
            if resolved_pid and str(resolved_pid).isdigit():
                self._fetch_game_name_for(str(resolved_pid))
            else:
                self._bridge.game_name_ready.emit("")

        threading.Thread(target=_resolve_worker, daemon=True, name="resolve-current-place").start()

    def _refresh_favorites_dropdown(self):
        current = self._place_id_edit.currentText()
        self._place_id_edit.blockSignals(True)
        self._place_id_edit.clear()
        for fav in favorites_mod.load_favorites():
            place_id = str(fav.get("place_id", ""))
            name = fav.get("name") or place_id
            private_server = fav.get("private_server", "")
            self._place_id_edit.addItem(name, (place_id, private_server))
        self._place_id_edit.setCurrentText(current)
        self._place_id_edit.blockSignals(False)

    def _on_favorite_selected(self, index: int):
        data = self._place_id_edit.itemData(index)
        if not data:
            return
        place_id, private_server = data
        self._place_id_edit.setCurrentText(str(place_id))
        self._private_server_edit.setCurrentText(private_server or "")

    def _on_favorite_context_menu(self, pos):
        view = self._place_id_edit.view()
        index = view.indexAt(pos)
        if not index.isValid():
            return
        data = self._place_id_edit.itemData(index.row())
        if not data:
            return
        place_id, private_server = data

        menu = QMenu(self)
        act_remove = menu.addAction("Remove")
        chosen = menu.exec(view.viewport().mapToGlobal(pos))
        if chosen == act_remove:
            favorites_mod.remove_favorite(place_id, private_server)
            self._refresh_favorites_dropdown()
            self._place_id_edit.hidePopup()

    def _on_save_current_game(self):
        place_id = self._place_id_edit.currentText().strip()
        private = self._private_server_edit.currentText().strip()

        if not place_id and not private:
            _show_error(self, "Missing Place ID", "Enter a Place ID or a Private Server Link first.")
            return

        if place_id:
            self._prompt_save_favorite(place_id, private)
            return

        usernames = self._get_selected_usernames()
        cookie = self.manager.accounts.get(usernames[0], {}).get("cookie", "") if usernames else ""

        def _resolve_worker():
            resolved_pid, _ = RobloxAPI.resolve_share_url(private, cookie=cookie)
            self._bridge.favorite_place_resolved.emit({
                "private": private,
                "effective_place_id": str(resolved_pid) if resolved_pid else "",
            })

        threading.Thread(target=_resolve_worker, daemon=True, name="resolve-save-favorite").start()

    def _on_favorite_place_resolved(self, payload: dict):
        effective_place_id = payload.get("effective_place_id", "")
        if not effective_place_id:
            _show_error(
                self, "Invalid Private Server",
                "Could not resolve a Place ID from the Private Server Link.",
            )
            return
        self._prompt_save_favorite(effective_place_id, payload["private"])

    def _prompt_save_favorite(self, place_id: str, private: str):
        default_name = self._game_name_label.text().replace("Current: ", "").strip() or place_id
        name, ok = QInputDialog.getText(
            self, "Save Current Game", "Name for this favorite:", text=default_name,
        )
        if not ok or not name.strip():
            return

        favorites_mod.add_favorite(place_id, name.strip(), private)
        self._refresh_favorites_dropdown()
        print(f"[SUCCESS] Saved favorite: {name.strip()} (Place {place_id})")

    def _on_add_account_browser(self):
        actions.add_account_browser(
            self.manager,
            on_done=self._on_add_done,
        )

    def _on_add_account_quick(self):
        actions.add_account_quick_sign_in(
            self.manager,
            on_done=self._on_add_done,
        )

    def _on_import_cookie(self):
        dlg = _ImportCookieDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.cookie_value:
            actions.import_cookie(
                self.manager,
                dlg.cookie_value,
                on_done=self._on_add_done,
            )

    def _on_import_userpass(self):
        dlg = _ImportUserPassDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.pairs:
            actions.import_user_pass(
                self.manager,
                dlg.pairs,
                on_done=self._on_add_done,
            )

    def _on_account_creator(self):
        running = getattr(self, "_creator_progress", None)
        if running is not None and isValid(running) and not running._done:
            running.show()  # a run is still going: show its window instead of starting another
            running.raise_()
            running.activateWindow()
            return
        dlg = _AccountCreatorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            stop_event = threading.Event()
            self._creator_progress = _CreatorProgressDialog(self, dlg.amount, stop_event)
            self._creator_progress.show()
            account_creator_mod.create_accounts(
                self.manager,
                dlg.amount,
                options=dlg.options,
                on_done=(
                    lambda success, message, report=None:
                    self._bridge.account_creator_done.emit(success, message, report)
                ),
                stop_event=stop_event,
                on_progress=(
                    lambda created, finished, requested:
                    self._bridge.account_creator_progress.emit(created, finished, requested)
                ),
            )

    def _on_account_creator_progress(self, created: int, finished: int, requested: int) -> None:
        progress = getattr(self, "_creator_progress", None)
        if progress is not None and isValid(progress):
            progress.update_progress(created, finished, requested)

    def _on_account_creator_done(self, success: bool, message: str, report=None):
        progress = getattr(self, "_creator_progress", None)
        if progress is not None and isValid(progress):
            progress.finish()
        self._creator_progress = None
        if isinstance(report, dict):
            if report.get("created"):
                self._refresh_account_list()
            _CreatorResultsDialog(self, report, message).exec()
            return
        if success:
            self._refresh_account_list()
            _show_info(self, "Account Creator", message)
        else:
            _show_error(self, "Account Creator", message)

    def _on_add_javascript(self):
        dlg = _AddJavascriptDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            actions.add_account_browser(
                self.manager,
                on_done=self._on_add_done,
                javascript=dlg.js_value,
            )

    def _on_add_done(self, success: bool, result):
        if isinstance(result, OperationResult):
            operation_result = result
        elif success:
            operation_result = OperationResult.success(str(result or ""))
        else:
            operation_result = OperationResult.failure(
                "ACCOUNT_ADD_FAILED",
                "Account Could Not Be Added",
                str(result or "The account could not be added."),
            )
        self._bridge.account_added.emit(operation_result)

    def _on_add_done_main(self, result):
        operation_result = ensure_result(
            result,
            failure_code="ACCOUNT_ADD_FAILED",
            failure_title="Account Could Not Be Added",
            failure_message="The account could not be added.",
        )
        if operation_result:
            # Auto-refresh the account list
            self._refresh_account_list()
            if operation_result.message:
                _show_info(self, "Account Added", operation_result.message)
        else:
            self._show_operation_error(operation_result)

    def _focus_account_search(self) -> None:
        self._account_search.setFocus()
        self._account_search.selectAll()

    def _apply_account_search(self) -> None:
        query = self._account_search.text().strip()
        if query == self._account_query:
            return
        self._account_query = query
        self._refresh_account_list()

    def _row_username(self, row: int) -> str | None:
        item = self._account_list.item(row)
        if item is None:
            return None
        username = item.data(Qt.ItemDataRole.UserRole)
        return username if username in self.manager.accounts else None

    def _on_account_reorder(self, from_row: int, insert_before_row: int): # Reoder accounts
        moved = self._row_username(from_row)
        if moved is None:
            return
        before = self._row_username(insert_before_row)
        visible = [
            name for name in (
                self._row_username(row) for row in range(self._account_list.count())
            ) if name
        ]

        with self.manager._accounts_lock:
            order = list(self.manager.accounts)
            new_order = account_order.move_account(order, moved, before, visible)
            if new_order == order:
                return
            self.manager.accounts = {
                name: self.manager.accounts[name] for name in new_order
            }

            try:
                self.manager.save_accounts()
            except Exception as e:
                print(f"[WARNING] Could not save account order: {e}")

        self._refresh_account_list()
        print(f"[INFO] Moved '{moved}' to position {new_order.index(moved) + 1}.")

    # Remove account
    def _on_remove_account(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select an account to remove.")
            return
        if len(usernames) == 1:
            reply = QMessageBox.question(
                self, "Confirm Removal",
                f"Remove account '{usernames[0]}'? This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                ok, msg = actions.remove_account(self.manager, usernames[0])
                if ok:
                    self._refresh_account_list()
                else:
                    _show_error(self, "Error", msg)
        else:
            reply = QMessageBox.question(
                self, "Confirm Removal",
                f"Remove {len(usernames)} accounts? This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                for username in usernames:
                    ok, msg = actions.remove_account(self.manager, username)
                    if ok:
                        self._refresh_account_list()
                    else:
                        _show_error(self, "Error", msg)

    # Join Place ID
    def _on_join_place(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select at least one account first.")
            return
        if not self._guard_invalid(usernames):
            return

        place_id = self._place_id_edit.currentText().strip()
        private = self._private_server_edit.currentText().strip()

        # Place ID is only required when there's no Private Server Link to
        # resolve a Place ID from - the Place ID inputbox is never rewritten.
        if not place_id and not private:
            _show_error(self, "Missing Place ID", "Enter a Place ID or a Private Server Link.")
            return

        if not self._confirm_launch("Join Place ID", usernames):
            return

        if place_id:
            # Place ID inputbox takes priority over any place id embedded in the link
            self._dispatch_join_place(usernames, place_id, private, place_id)
            return

        cookie = self.manager.accounts.get(usernames[0], {}).get("cookie", "")

        def _resolve_worker():
            resolved_pid, _ = RobloxAPI.resolve_share_url(private, cookie=cookie)
            self._bridge.join_place_resolved.emit({
                "usernames": usernames,
                "private": private,
                "effective_place_id": str(resolved_pid) if resolved_pid else "",
            })

        threading.Thread(target=_resolve_worker, daemon=True, name="resolve-join-place").start()

    def _on_join_place_resolved(self, payload: dict):
        effective_place_id = payload.get("effective_place_id", "")
        if not effective_place_id:
            _show_error(
                self, "Invalid Private Server",
                "Could not resolve a Place ID from the Private Server Link.",
            )
            return
        self._dispatch_join_place(
            payload["usernames"], "", payload["private"], effective_place_id,
        )

    def _dispatch_join_place(self, usernames: list[str], place_id: str, private: str, effective_place_id: str):
        usernames = self._queue_or_all(
            usernames,
            lambda batch: self._send_join_place(batch, place_id, private, effective_place_id),
        )
        self._send_join_place(usernames, place_id, private, effective_place_id)

        # Fetch the game name fresh instead of trusting the "Current Place" label,
        # which is populated by an independent debounced fetch that may not have
        # finished yet (especially for share links, which need two network calls).
        def _save_recent_worker():
            name = actions.fetch_game_name(effective_place_id)
            actions.save_recent_game(effective_place_id, name, private)
            self._bridge.recent_game_saved.emit()

        threading.Thread(target=_save_recent_worker, daemon=True, name="save-recent-game").start()

    def _send_join_place(self, usernames: list[str], place_id: str, private: str, effective_place_id: str):
        if len(usernames) == 1:
            print(f"[INFO] Joining place {effective_place_id} for {usernames[0]}")
            actions.join_place(
                self.manager, usernames[0], place_id, private,
                on_done=self._launch_callback(usernames),
            )
        else:
            print(f"[INFO] Joining place {effective_place_id} for {len(usernames)} accounts")
            actions.join_place_all(
                self.manager, usernames, place_id, private,
                on_done=self._launch_callback(usernames),
            )

    def _send_with_queue(self, usernames: list[str], send) -> None:
        """Send now, or start a Batch Queue when it is on and the selection is bigger than a batch."""
        first = self._queue_or_all(list(usernames), send)
        send(first)

    _RECENT_TARGETS_SETTING = "recent_join_targets"

    def _recent_join_targets(self) -> list[str]:
        value = actions.get_ui_setting(self._RECENT_TARGETS_SETTING, [])
        return [str(v) for v in value if isinstance(v, str) and v.strip()][:10] if isinstance(value, list) else []

    def _remember_join_target(self, name: str) -> None:
        names = [name] + [n for n in self._recent_join_targets() if n.lower() != name.lower()]
        actions.save_ui_setting(self._RECENT_TARGETS_SETTING, names[:10])
        actions.save_ui_setting("last_join_target", name)
        combo = getattr(self, "_join_target_edit", None)
        if combo is not None and isValid(combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names[:10])
            combo.setCurrentText(name)
            combo.blockSignals(False)

    def _on_join_target(self):
        target = self._join_target_edit.currentText().strip()
        if not target:
            _show_error(self, "Join User", "Type the username of the player you want to join.")
            self._join_target_edit.setFocus()
            return
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select at least one account.")
            return
        if not self._guard_invalid(usernames):
            return
        if not self._confirm_launch(f"Join {target}", usernames):
            return
        self._remember_join_target(target)
        print(f"[INFO] Joining {target}'s server with {len(usernames)} account(s)")
        # Each batch looks up where the target is at that moment.
        self._send_with_queue(
            usernames,
            lambda batch: actions.join_user(
                self.manager, batch, target, on_done=self._launch_callback(batch),
            ),
        )

    def _on_join_user(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select at least one account.")
            return

        target_user, ok = QInputDialog.getText(self, "Join User", "Enter the target username to join:")
        if not ok or not target_user.strip():
            return

        if not self._guard_invalid(usernames):
            return

        if not self._confirm_launch("Join User", usernames):
            return

        target = target_user.strip()
        self._send_with_queue(
            usernames,
            lambda batch: actions.join_user(
                self.manager, batch, target, on_done=self._launch_callback(batch),
            ),
        )

    def _on_join_job_id(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select at least one account.")
            return

        place_id = self._place_id_edit.currentText().strip()
        if not place_id:
            _show_error(self, "Missing Info", "Please enter a Place ID first.")
            return

        job_id, ok = QInputDialog.getText(self, "Join by Job ID", "Enter the Job ID (Game ID):")
        if not ok or not job_id.strip():
            return

        if not self._guard_invalid(usernames):
            return

        if not self._confirm_launch("Join by Job ID", usernames):
            return

        job = job_id.strip()
        self._send_with_queue(
            usernames,
            lambda batch: actions.join_job_id(
                self.manager, batch, place_id, job, on_done=self._launch_callback(batch),
            ),
        )

    def _on_join_small_server(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select at least one account.")
            return

        place_id = self._place_id_edit.currentText().strip()
        if not place_id:
            _show_error(self, "Missing Info", "Please enter a Place ID first.")
            return

        if not self._guard_invalid(usernames):
            return

        if not self._confirm_launch("Join Small Server", usernames):
            return

        self._send_with_queue(
            usernames,
            lambda batch: actions.join_small_server(
                self.manager, batch, place_id, on_done=self._launch_callback(batch),
            ),
        )

    # Edit Note
    def _on_edit_note(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select at least one account first.")
            return
        # Use first selected username as the dialog reference
        first = usernames[0]
        current = actions.get_note(self.manager, first) if len(usernames) == 1 else ""
        dlg = _EditNoteDialog(first if len(usernames) == 1 else f"{len(usernames)} accounts", current, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            note = dlg.note_value
            actions.set_notes_in_memory(self.manager, usernames, note)
            print(f"[INFO] Note updated for {len(usernames)} account(s)")
            if getattr(self, "_account_query", ""):
                self._refresh_account_list()  # the search may match notes
            else:
                for u in usernames:
                    labels = self._note_labels.get(u)
                    if labels and all(isValid(w) for w in labels):
                        sep, note_lbl = labels
                        note_lbl.setText(note)
                        sep.setVisible(bool(note))
                        note_lbl.setVisible(bool(note))
            self._save_accounts_async()

    # Launch Roblox Home
    def _on_launch_home(self):
        usernames = self._get_selected_usernames()
        if not usernames:
            _show_error(self, "No selection", "Please select an account first.")
            return
        if not self._guard_invalid(usernames):
            return
        if not self._confirm_launch("Launch Roblox Home", usernames):
            return
        usernames = self._queue_or_all(
            usernames,
            lambda batch: actions.launch_home(self.manager, batch, on_done=self._launch_callback(batch)),
        )
        if len(usernames) == 1:
            print(f"[INFO] Launching Roblox Home for {usernames[0]}")
            launch_accounts = usernames[0]
        else:
            print(
                f"[INFO] Launching Roblox Home for {len(usernames)} accounts"
            )
            launch_accounts = usernames
        actions.launch_home(
            self.manager, launch_accounts,
            on_done=self._launch_callback(usernames),
        )

    def _on_kill_all_roblox(self):
        # Instant: no confirmation, runs off the UI thread, result shown on the button.
        if getattr(self, "_kill_in_progress", False):
            return
        self._kill_in_progress = True
        btn = self._kill_roblox_btn
        btn.setEnabled(False)
        btn.setText("Killing...")

        manager = self.manager
        # A second press soon after a run that kept unidentified windows
        # closes those too (locked accounts are still kept).
        close_unknown = time.monotonic() < getattr(self, "_kill_unknown_until", 0.0)
        self._kill_unknown_until = 0.0

        def _worker():
            try:
                locked, unknown = actions.locked_and_unknown_pids(manager)
            except Exception as exc:
                print(f"[WARNING] Could not check locked accounts: {exc}")
                locked, unknown = set(), set()
            if close_unknown:
                unknown = set()
            if locked:
                print(f"[INFO] Kill All Roblox: keeping {len(locked)} locked account window(s) open")
            if unknown:
                print(f"[WARNING] Kill All Roblox: kept {len(unknown)} window(s) that could not be matched "
                      "to an account (one may be locked). Press Kill All again within 5s to close them.")
            result = actions.kill_all_roblox_fast(keep_pids=locked | unknown)
            if result and isinstance(result.data, dict):
                result = replace(result, data={**result.data, "kept": len(locked), "unknown": len(unknown)})
            self._bridge.kill_roblox_done.emit(result)

        threading.Thread(target=_worker, daemon=True, name="KillAllRoblox").start()

    def _on_kill_roblox_done(self, result) -> None:
        self._kill_in_progress = False
        btn = self._kill_roblox_btn
        if not isValid(btn):
            return
        if result:
            data = (result.data or {}) if hasattr(result, "data") else {}
            games, kept = data.get("games", 0), data.get("kept", 0)
            unknown = data.get("unknown", 0)
            text = f"Killed {games}" if games else "None running"
            if kept:
                text = f"Killed {games} · {kept} locked"
            if unknown:
                text += f" · {unknown} unknown"
                btn.setToolTip(f"{unknown} Roblox window(s) couldn't be matched to an account, so they were "
                               "kept in case one is locked.\nPress Kill All again within 5 seconds to close them.")
                self._kill_unknown_until = time.monotonic() + 5.0
            btn.setText(text)
        else:
            btn.setText("Some left!")
            print(f"[WARNING] {result.message} {getattr(result, 'detail', '')}")
        btn.setEnabled(True)
        # Auto-Rejoin waits a minute so nothing reopens behind your back; a
        # running batch queue moves on to its next batch once the clients are gone.
        ar.pause_all()
        q = getattr(self, "_launch_queue", None)
        if q and not q.get("force"):  # Next Batch also uses Kill All and already moves on
            print("[INFO] Kill All Roblox: the batch queue moves on to the next batch.")
        delay = 5000 if getattr(self, "_kill_unknown_until", 0.0) > time.monotonic() else 1800

        def _reset():
            if isValid(btn):
                btn.setText("  Kill All Roblox")
                btn.setToolTip(KILL_ALL_TOOLTIP)
        QTimer.singleShot(delay, _reset)
        if getattr(self, "_launch_queue", None):
            self._poll_queue()  # the queue sees the closed clients and moves on

    def _set_accounts_locked(self, usernames: list[str], locked: bool) -> None:
        # Instant: change the accounts in memory, flip the 🔒 on just those
        # rows, and save in the background.
        actions.set_accounts_locked(self.manager, usernames, locked, save=False)
        for name in usernames:
            label = self._lock_labels.get(name)
            if label is not None and isValid(label):
                label.setVisible(bool(locked))
        word = "Locked" if locked else "Unlocked"
        print(f"[INFO] {word} {len(usernames)} account(s): {', '.join(usernames)}")
        self._save_accounts_async()

    def _save_accounts_async(self) -> None:
        """Save accounts off the UI thread. Several quick changes share one save."""
        guard = self.__dict__.setdefault("_save_guard", threading.Lock())
        with guard:
            if self.__dict__.get("_save_running"):
                self._save_pending = True
                return
            self._save_running = True
            self._save_pending = False
        manager = self.manager

        def _worker():
            while True:
                try:
                    manager.save_accounts()
                except Exception as exc:
                    print(f"[ERROR] Could not save accounts: {exc}")
                with guard:
                    if self._save_pending:
                        self._save_pending = False
                        continue
                    self._save_running = False
                    return

        threading.Thread(target=_worker, daemon=True, name="SaveAccounts").start()

    def _launch_callback(self, usernames: list[str]):
        batch = list(usernames)
        return lambda success, result: self._emit_launch_done(success, result, batch)

    def _emit_launch_done(self, success: bool, result, usernames: list[str] | None = None):
        if isinstance(result, OperationResult):
            operation_result = result
        elif success:
            operation_result = OperationResult.success(str(result or ""))
        else:
            operation_result = OperationResult.failure(
                "ROBLOX_LAUNCH_FAILED",
                "Roblox Could Not Start",
                str(result or actions.LAUNCH_NOT_OPEN_MESSAGE),
            )
        if not operation_result and usernames:
            # Remember exactly which accounts did not open, for the queue.
            data = dict(operation_result.data) if isinstance(operation_result.data, dict) else {}
            if "failed" not in data:
                data["failed"] = list(usernames) if len(usernames) == 1 else []
            data["batch"] = list(usernames)
            operation_result = replace(operation_result, data=data)
        self._bridge.launch_done.emit(operation_result)

    def _on_launch_and_refresh(self, result):
        self._refresh_recent_games()
        operation_result = ensure_result(
            result,
            failure_code="ROBLOX_LAUNCH_FAILED",
            failure_title="Roblox Could Not Start",
            failure_message=actions.LAUNCH_NOT_OPEN_MESSAGE,
        )
        if not operation_result:
            if self._note_queue_failures(operation_result):
                return  # collected for the end of the queue; no popup per batch
            self._show_operation_error(operation_result)

    # Batch queue: select the accounts that did not open (Settings > Roblox)

    QUEUE_RETRY_SETTING = "queue_retry_count"
    _QUEUE_COOLDOWN = 30.0

    def _queue_retry_limit(self) -> int:
        try:
            return max(0, min(5, int(actions.get_ui_setting(self.QUEUE_RETRY_SETTING, 2))))
        except (TypeError, ValueError):
            return 2

    def _note_queue_failures(self, result: OperationResult) -> bool:
        """Sort a batch's failures: retry later, needs a security check, or failed for good.

        While a queue runs, failures are collected (no popup per batch) and
        summed up when the queue finishes.
        """
        q = getattr(self, "_launch_queue", None)
        if not q:
            return False
        for key, default in (("failed", []), ("security", []), ("attempts", {}), ("ever_failed", set())):
            q.setdefault(key, default)
        data = result.data if isinstance(result.data, dict) else {}
        failed = [name for name in data.get("failed", []) if name]
        codes = data.get("failed_codes") or {}
        batch = data.get("batch") or []
        limit = self._queue_retry_limit()
        retry, security, gave_up = [], [], []
        for name in failed:
            code = codes.get(name) or result.code or ""
            q["ever_failed"].add(name)
            if code == actions.SECURITY_CHECK_CODE:
                if name not in q["security"]:
                    q["security"].append(name)
                security.append(name)
            elif code in actions.RETRYABLE_LAUNCH_CODES and q["attempts"].get(name, 0) < limit:
                q["attempts"][name] = q["attempts"].get(name, 0) + 1
                if name not in q["remaining"]:
                    q["remaining"].append(name)  # tried again at the end of the queue
                retry.append(name)
            else:
                if name not in q["failed"]:
                    q["failed"].append(name)
                gave_up.append(name)
        if retry:
            q["batches"] = q["batch"] + -(-len(q["remaining"]) // q["size"])
            print(f"[WARNING] Batch queue: {len(retry)} account(s) didn't open and will be retried at the end: "
                  f"{', '.join(retry)}")
        if security:
            print(f"[WARNING] Batch queue: {len(security)} account(s) need a Roblox security check (not retried): "
                  f"{', '.join(security)}")
        if gave_up:
            print(f"[WARNING] Batch queue: {len(gave_up)} account(s) failed: {', '.join(gave_up)}")
        if batch and set(batch) <= set(failed) and q["phase"] == "starting":
            now = time.monotonic()
            q["phase"], q["phase_since"], q["empty_polls"] = "waiting", now, 0
            if gave_up or security:
                # A retry won't fix these; they are listed above. The queue never pauses itself.
                print(f"[WARNING] Batch queue: batch {q['batch']} failed right away "
                      f"({result.message or result.code}). Moving on to the next batch.")
            else:
                # Roblox trouble (installer/update/network): give it a moment, then carry on.
                q["cooldown_until"] = now + self._QUEUE_COOLDOWN
                print(f"[INFO] Batch queue: nothing from batch {q['batch']} opened. "
                      f"Waiting {self._QUEUE_COOLDOWN:.0f}s before the next batch.")
            self._update_queue_status()
        return True

    def _show_queue_summary(self, q: dict) -> None:
        security = [n for n in q.get("security", []) if n in self.manager.accounts]
        failed = [n for n in q.get("failed", []) if n in self.manager.accounts and n not in security]
        leftovers = failed + security
        ever_failed = set(q.get("ever_failed", set()))
        retried_ok = len(ever_failed - set(leftovers))
        opened = max(0, q.get("total", 0) - len(leftovers))
        parts = [f"{opened} opened"]
        if retried_ok:
            parts.append(f"{retried_ok} of them after a retry")
        if security:
            parts.append(f"{len(security)} need a security check")
        if failed:
            parts.append(f"{len(failed)} failed")
        summary = " · ".join(parts)
        print(f"[INFO] Batch queue summary: {summary}")
        if leftovers:
            self._select_failed_queue_accounts(leftovers, summary=summary, security=security)
        else:
            QMessageBox.information(self, "Queue Finished", f"Batch queue finished.\n\n{summary}")

    def _select_failed_queue_accounts(self, failed: list[str], summary: str = "",
                                      security: list[str] | None = None) -> None:
        failed = [name for name in failed if name in self.manager.accounts]
        if not failed:
            QMessageBox.information(self, "Queue Finished", "Batch queue finished. Every account opened.")
            return
        security = list(security or [])
        settings = actions.load_ui_settings()
        if not settings.get("enable_multi_select", False) and len(failed) > 1:
            actions.save_ui_setting("enable_multi_select", True)
            check = getattr(self, "_sett_multisel_chk", None)
            if check is not None and isValid(check):
                check.blockSignals(True)
                check.setChecked(True)
                check.blockSignals(False)
            self._on_sett_multi_select(True)
        # Make sure every failed account is visible before selecting it.
        if self._current_group is not None:
            self._current_group = None
        if self._account_search.text():
            self._account_search.blockSignals(True)
            self._account_search.clear()
            self._account_search.blockSignals(False)
        self._account_query = ""
        self._refresh_account_list()
        self._show_page(0)
        self._account_list.clearSelection()
        first = None
        for row in range(self._account_list.count()):
            item = self._account_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in failed:
                item.setSelected(True)
                first = first or item
        if first is not None:
            self._account_list.setCurrentItem(first, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._account_list.scrollToItem(first)
        print(f"[INFO] Batch queue: selected {len(failed)} account(s) that did not open: {', '.join(failed)}")
        box = QMessageBox(self)
        box.setWindowTitle("Queue Finished")
        box.setIcon(QMessageBox.Icon.Warning)
        noun = "account" if len(failed) == 1 else "accounts"
        text = f"Batch queue finished. {summary}" if summary else "Batch queue finished."
        text += f"\n\n{len(failed)} {noun} didn't open and {'is' if len(failed) == 1 else 'are'} selected now."
        if security:
            text += ("\nAccounts that need a security check must be verified in Roblox first "
                     "(log in once and solve it), then join again.")
        box.setText(text)
        box.setInformativeText(", ".join(failed))
        join_btn = box.addButton("Join now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is join_btn:
            self._on_join_place()

    def _operation_error_message_text(self, result: OperationResult) -> str:
        lines = [
            result.title or "Operation Failed",
            result.message or "The operation could not be completed.",
        ]
        if result.code:
            lines.append(f"Error code: {result.code}")
        if result.detail:
            lines.extend([
                "",
                "Technical details:",
                diagnostics.redact(result.detail),
            ])
        return "\n".join(lines)

    @staticmethod
    def _session_log_copy_text() -> str:
        diagnostics.flush_session_log()
        session_log = diagnostics.get_session_log_path()
        if not session_log:
            return "The session log is unavailable."
        try:
            with open(session_log, "r", encoding="utf-8") as handle:
                return handle.read()
        except Exception as exc:
            return (
                f"Failed to read the session log.\n"
                f"Path: {session_log}\n"
                f"Error: {type(exc).__name__}: {exc}"
            )

    def _show_operation_error(self, result: OperationResult) -> None:
        now = time.monotonic()
        key = f"{result.code}|{result.title}|{result.message}"
        if self._last_operation_error[0] == key and now - self._last_operation_error[1] < 3:
            return
        self._last_operation_error = (key, now)

        dlg = QMessageBox(self)
        dlg.setWindowTitle(result.title or "Operation Failed")
        dlg.setText(result.message or "The operation could not be completed.")
        information = []
        if result.code:
            information.append(f"Error code: {result.code}")
        if result.code == "UNEXPECTED_ERROR":
            information.append(
                f"Session log: {diagnostics.get_session_log_path()}"
            )
        if information:
            dlg.setInformativeText("\n".join(information))
        if result.detail:
            dlg.setDetailedText(diagnostics.redact(result.detail))
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.setDefaultButton(QMessageBox.StandardButton.Ok)
        ok_button = dlg.button(QMessageBox.StandardButton.Ok)
        if ok_button:
            dlg.setEscapeButton(ok_button)

        error_text = self._operation_error_message_text(result)
        copy_error_button = dlg.addButton(
            "Copy Error Message",
            QMessageBox.ButtonRole.ActionRole,
        )
        copy_error_button.clicked.disconnect()
        copy_error_button.clicked.connect(
            lambda: QApplication.clipboard().setText(error_text)
        )

        copy_log_button = dlg.addButton(
            "Copy Full Log",
            QMessageBox.ButtonRole.ActionRole,
        )
        copy_log_button.clicked.disconnect()
        copy_log_button.clicked.connect(
            lambda: QApplication.clipboard().setText(
                self._session_log_copy_text()
            )
        )
        dlg.setStyleSheet(f"QMessageBox {{ background: {BG}; color: {TEXT}; }}")
        dlg.exec()

    # Right-click context menu on account list
    def _on_account_context_menu(self, pos):
        item = self._account_list.itemAt(pos)
        if item is None:
            return
        username = item.data(Qt.ItemDataRole.UserRole)
        if not username:
            return

        if username not in self._get_selected_usernames():
            self._account_list.setCurrentItem(item)

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {PANEL}; border: 1px solid {LINE};"
            f"  color: {TEXT}; font-size: 11px; }}"
            f"QMenu::item:selected {{ background: {SELECT}; }}"
            f"QMenu::item:disabled {{ color: {MUTED}; }}"
        )

        S = actions.load_ui_settings()
        multi_sel = self._get_selected_usernames()
        is_multi = len(multi_sel) > 1

        act_join = menu.addAction("Join Place ID")
        act_note = menu.addAction("Edit Note")
        lock_targets = multi_sel if username in multi_sel else [username]
        all_locked = all(
            isinstance(self.manager.accounts.get(u), dict) and self.manager.accounts[u].get(actions.LOCK_KEY)
            for u in lock_targets
        )
        lock_label = "Unlock" if all_locked else "Lock (Kill All keeps it open)"
        if len(lock_targets) > 1:
            lock_label += f" ({len(lock_targets)} accounts)"
        act_lock = menu.addAction(icons.make_icon("unlock" if all_locked else "lock", ACCENT_BRIGHT, size=16), lock_label)
        menu.addSeparator()

        # Copy Contents submenu
        copy_menu = menu.addMenu("Copy Contents")
        copy_menu.setStyleSheet(
            f"QMenu {{ background: {PANEL}; border: 1px solid {LINE};"
            f"  color: {TEXT}; font-size: 11px; }}"
            f"QMenu::item:selected {{ background: {SELECT}; }}"
        )
        act_copy_user = copy_menu.addAction("Copy Username")
        act_copy_pass = copy_menu.addAction("Copy Password")
        act_copy_up = copy_menu.addAction("Copy User:Pass")
        copy_cookie_enabled = S.get("enable_copy_cookie", False)
        act_copy_cookie = copy_menu.addAction("Copy Cookie")
        act_copy_cookie.setEnabled(copy_cookie_enabled)

        menu.addSeparator()

        move_menu = menu.addMenu("Move to Group")
        move_menu.setStyleSheet(
            f"QMenu {{ background: {PANEL}; border: 1px solid {LINE};"
            f"  color: {TEXT}; font-size: 11px; }}"
            f"QMenu::item:selected {{ background: {SELECT}; }}"
        )
        group_list = groups.get_group_names()
        usernames = self._get_selected_usernames()

        if group_list:
            for gname in group_list:
                act_grp = move_menu.addAction(gname)
                act_grp.triggered.connect(
                    lambda _=False, users=list(usernames), g=gname:
                        self._on_assign_to_group(users, g)
                )
        move_menu.addSeparator()
        act_ungrp = move_menu.addAction("Remove from Group")
        usernames = self._get_selected_usernames()

        act_ungrp.triggered.connect(
            lambda _=False, users=list(usernames): self._on_remove_from_group(users)
        )

        menu.addSeparator()
        act_remove = menu.addAction("Remove Account")

        chosen = menu.exec(self._account_list.mapToGlobal(pos))
        if chosen == act_lock:
            self._set_accounts_locked(lock_targets, not all_locked)
        elif chosen == act_join:
            self._on_join_place()
        elif chosen == act_note:
            self._on_edit_note()
        elif chosen == act_remove:
            self._on_remove_account()
        elif chosen in (act_copy_user, act_copy_pass, act_copy_up, act_copy_cookie):
            self._on_copy_contents(chosen, act_copy_user, act_copy_pass, act_copy_up, act_copy_cookie, username, is_multi, multi_sel)

    def _on_copy_contents(self, chosen, act_user, act_pass, act_up, act_cookie, username: str, is_multi: bool, multi_sel):
        def _get_data(u):
            d = self.manager.accounts.get(u, {})
            return {"username": u, "password": d.get("password", ""), "cookie": d.get("cookie", "")}
        targets = multi_sel if is_multi else [username]
        if chosen == act_user:
            field, fmt = "username", lambda d: d["username"]
        elif chosen == act_pass:
            field, fmt = "password", lambda d: d["password"]
        elif chosen == act_up:
            field, fmt = "user_pass", lambda d: d["username"] + ":" + d["password"]
        else:
            field, fmt = "cookie", lambda d: d["cookie"]
        lines = [fmt(_get_data(u)) for u in targets]
        if is_multi:
            filter_str = "Text Files (*.txt);;All Files (*)"
            default_name = "export_" + field + ".txt"
            path2, _ = QFileDialog.getSaveFileName(self, "Save Export", default_name, filter_str)
            if path2:
                try:
                    with open(path2, "w", encoding="utf-8") as fp:
                        fp.write("\n".join(lines))
                    print("[SUCCESS] Exported " + str(len(lines)) + " entries to " + os.path.basename(path2))
                    QMessageBox.information(self, "Export Done",
                        "Exported " + str(len(lines)) + " account(s) to:\n" + path2)
                except Exception as e:
                    print("[ERROR] Export failed: " + str(e))
        else:
            QApplication.clipboard().setText(lines[0] if lines else "")
            print("[INFO] Copied " + field + " for " + username + " to clipboard")

    def _drain_console_queue(self):
        q = getattr(self, "_console_queue", None)
        if not q:
            return
        widget = getattr(self, "_console_view", None)
        if widget is None:
            return
        batch: list[tuple[str, str | None]] = []
        for _ in range(50):
            try:
                batch.append(q.popleft())
            except IndexError:
                break
        if not batch:
            return
        cursor = widget.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        for text, color in batch:
            fmt = QTextCharFormat()
            if color:
                fmt.setForeground(QColor(color))
            else:
                fmt.clearForeground()
            cursor.setCharFormat(fmt)
            cursor.insertText(text + "\n")
        widget.setTextCursor(cursor)
        widget.ensureCursorVisible()
        if q:
            QTimer.singleShot(0, self._drain_console_queue)

    # Contact details shown on the Contact Us page. Leave a value empty to show
    # that channel as "Coming soon".
    DISCORD_SERVER_URL = "https://discord.gg/fg9RmZ65kn"
    CONTACT_DISCORD_NAME = "hex0759"
    CONTACT_DISCORD_URL = "https://discord.com/users/616978543318007808"
    CONTACT_TELEGRAM_NAME = ""
    CONTACT_TELEGRAM_URL = ""

    def _build_donations_panel(self) -> QFrame:  # Contact Us page (kept name for page wiring)
        panel = QFrame()
        panel.setObjectName("settingsPanel")

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("contactCard")
        card.setStyleSheet(f"""
            #contactCard {{
                background: {PANEL};
                border: 1px solid {LINE};
                border-radius: 10px;
            }}
        """)
        card.setMaximumWidth(420)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(32, 32, 32, 32)
        card_lay.setSpacing(14)
        card_lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        title_lbl = QLabel("Contact Us")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT}; background: transparent;")
        card_lay.addWidget(title_lbl)

        desc_lbl = QLabel("Need help, have a question, or found a bug? Message us.")
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"font-size: 11px; color: {MUTED}; background: transparent;")
        card_lay.addWidget(desc_lbl)

        primary_style = f"""
            QPushButton {{
                background: {FG_ACCENT}; color: {ON_ACCENT}; border: none;
                border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 12px;
            }}
            QPushButton:hover {{ background: {ACCENT_HOVER}; }}
            QPushButton:pressed {{ background: {ACCENT_PRESSED}; }}
        """
        disabled_style = f"""
            QPushButton {{
                background: {INPUT}; color: {MUTED}; border: 1px solid {LINE};
                border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 12px;
            }}
        """

        note_lbl = QLabel("")
        note_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet(f"font-size: 10px; color: {MUTED}; background: transparent;")

        def _channel(label: str, name: str, url: str) -> QPushButton:
            # Only the channel name is shown. Click opens it; press and hold
            # copies the username.
            btn = QPushButton(label if (name or url) else f"{label}  ·  Coming soon")
            btn.setFixedHeight(36)
            if not (name or url):
                btn.setEnabled(False)
                btn.setStyleSheet(disabled_style)
                return btn
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(primary_style)
            btn.setToolTip("Click to open · Hold to copy")

            hold = QTimer(btn)
            hold.setSingleShot(True)
            hold.setInterval(550)
            state = {"held": False}

            def _on_hold():
                state["held"] = True
                if name:
                    QApplication.clipboard().setText(name)
                    note_lbl.setText(f"Copied {name}. Contact us on Discord or Telegram.")
                    btn.setText("Copied!")
                    QTimer.singleShot(1500, lambda: isValid(btn) and btn.setText(label))

            def _on_pressed():
                state["held"] = False
                hold.start()

            def _on_clicked():
                hold.stop()
                if state["held"]:
                    return  # it was a hold, already copied
                if url:
                    webbrowser.open(url)
                    note_lbl.setText(f"Opening {label}…")

            hold.timeout.connect(_on_hold)
            btn.pressed.connect(_on_pressed)
            btn.released.connect(hold.stop)
            btn.clicked.connect(_on_clicked)
            return btn

        card_lay.addWidget(_channel("Discord", self.CONTACT_DISCORD_NAME, self.CONTACT_DISCORD_URL))
        card_lay.addWidget(_channel("Telegram", self.CONTACT_TELEGRAM_NAME, self.CONTACT_TELEGRAM_URL))
        hint_lbl = QLabel("Click to open · Hold to copy")
        hint_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint_lbl.setStyleSheet(f"font-size: 10px; color: {MUTED}; background: transparent;")
        card_lay.addWidget(hint_lbl)
        card_lay.addWidget(note_lbl)

        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(card)
        h.addStretch(1)
        outer.addLayout(h)

        outer.addStretch(1)
        return panel

    def _build_console_panel(self) -> QFrame: # Console panel
        panel = QFrame()
        panel.setObjectName("centerPanel")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        ttl = QLabel("Console")
        ttl.setObjectName("sectionTitle")
        hdr.addWidget(ttl)
        hdr.addStretch(1)
        lay.addLayout(hdr)

        self._console_view = QTextEdit()
        self._console_view.setReadOnly(True)
        self._console_view.document().setMaximumBlockCount(3000)
        # Read-only log: keeping undo history for every line only costs memory and time.
        self._console_view.document().setUndoRedoEnabled(False)
        self._console_view.setStyleSheet(
            f"QTextEdit {{"
            f"  background: {INPUT};"
            f"  border: 1px solid {LINE};"
            f"  color: {TEXT};"
            f"  font-family: Consolas, 'Courier New', monospace;"
            f"  font-size: 11px;"
            f"  padding: 4px;"
            f"}}"
        )
        lay.addWidget(self._console_view, 1)

        _BTN_STYLE = (
            f"QPushButton {{"
            f"  background: {INPUT}; border: 1px solid {LINE};"
            f"  color: {TEXT}; font-size: 11px; min-height: 26px; padding: 2px 8px;"
            f"}}"
            f"QPushButton:hover   {{ background: {SELECT}; }}"
            f"QPushButton:pressed {{ background: {SELECT}; }}"
        )
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.setContentsMargins(0, 0, 0, 0)

        copy_btn = QPushButton("Copy")
        copy_btn.setStyleSheet(_BTN_STYLE)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(
            self._console_view.toPlainText()
        ))
        btn_row.addWidget(copy_btn)

        clr_btn = QPushButton("Clear")
        clr_btn.setStyleSheet(_BTN_STYLE)
        clr_btn.clicked.connect(self._console_view.clear)
        btn_row.addWidget(clr_btn)

        lay.addLayout(btn_row)

        return panel

# Dialogs
_DLG_STYLE = f"""
    QDialog   {{ background: {BG}; }}
    QLabel    {{ color: {TEXT}; font-size: 11px; }}
    QLineEdit {{ background: {INPUT}; border: 1px solid {LINE};
                color: {TEXT}; padding: 4px 6px; min-height: 24px; }}
    QTextEdit {{ background: {INPUT}; border: 1px solid {LINE};
                color: {TEXT}; font-family: Consolas, monospace; font-size: 11px; }}
    QPushButton {{
        background: {INPUT}; border: 1px solid {LINE};
        color: {TEXT}; min-height: 26px; padding: 2px 12px; font-size: 11px;
    }}
    QPushButton:hover   {{ background: {SELECT}; }}
    QPushButton:pressed {{ background: {SELECT}; }}
"""


class _GamesSelectorBridge(QObject):
    loaded = Signal(int, object)
    thumbnail = Signal(int, str, object)


class _GameItemDelegate(QStyledItemDelegate):
    """Draw cards without a separate widget and layout for every result."""

    def __init__(self, dialog):
        super().__init__(dialog.games)
        self._dialog = dialog

    def paint(self, painter, option, index):
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        background.icon = QIcon()
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
        game = index.data(Qt.ItemDataRole.UserRole)
        is_list = self._dialog.list_view.isChecked()
        image_rect = option.rect.adjusted(8, 8, -8, -8)
        image_rect.setWidth(72 if is_list else 128)
        image_rect.setHeight(image_rect.width())
        painter.save()
        painter.setClipRect(option.rect)
        painter.fillRect(image_rect, QColor(INPUT))
        pixmap = self._dialog._images.get(game.universe_id)
        if pixmap is not None:
            painter.drawPixmap(image_rect, pixmap, pixmap.rect())
        else:
            placeholder = icons.pixmap("home", MUTED, 24)
            painter.drawPixmap(image_rect.center() - QPoint(12, 12), placeholder)
        title_rect = option.rect.adjusted(92, 12, -8, -30) if is_list else option.rect.adjusted(8, 142, -8, -8)
        font = QFont(option.font)
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(TEXT))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop |
                         Qt.TextFlag.TextWordWrap, game.title)
        if is_list:
            font.setPixelSize(10)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(MUTED))
            painter.drawText(option.rect.adjusted(92, 58, -8, -8), Qt.AlignmentFlag.AlignLeft |
                             Qt.AlignmentFlag.AlignTop, f"Place ID: {game.place_id}")
        painter.restore()


class _GamesSelectorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_place_id = ""
        self._generation = 0
        self._requests = []
        self._pages = []
        self._page_index = 0
        self._requested_page = 0
        self._query = ""
        self._loading = False
        self._closed = False
        self._items = {}
        self._images = {}
        self._session_id = str(uuid.uuid4())
        self._bridge = _GamesSelectorBridge()
        self._bridge.loaded.connect(self._on_loaded, Qt.ConnectionType.QueuedConnection)
        self._bridge.thumbnail.connect(self._on_thumbnail, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("Games Selector")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.resize(660, 560)
        self.setMinimumSize(440, 360)
        self.setStyleSheet(_DLG_STYLE + f"""
            QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; color: {TEXT}; }}
            QListWidget::item {{ border: 1px solid transparent; }}
            QListWidget::item:hover {{ background: {HOVER}; }}
            QListWidget::item:selected {{ background: {SELECT}; border-color: {FG_ACCENT}; }}
            QPushButton:disabled {{ color: {MUTED}; }}
            QPushButton:focus, QLineEdit:focus {{ border-color: {FG_ACCENT}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Search games"))
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Roblox games...")
        self.search.setAccessibleName("Search Roblox games")
        self.search.setMaxLength(100)
        self.search.setClearButtonEnabled(True)
        self.search_button = QPushButton("Search")
        self.list_view = QCheckBox("List View")
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.list_view)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        self.status = QLabel("Loading popular games...")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(self.status)
        self.games = QListWidget()
        self.games.setAccessibleName("Game search results")
        self.games.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.games.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.games.setSpacing(4)
        self.games.setMovement(QListWidget.Movement.Static)
        self.games.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.games.setUniformItemSizes(True)
        self.games.setItemDelegate(_GameItemDelegate(self))
        layout.addWidget(self.games, 1)
        buttons = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.page_label = QLabel("Page 1")
        self.page_label.setStyleSheet(f"color: {MUTED};")
        buttons.addWidget(self.previous)
        buttons.addWidget(self.page_label)
        buttons.addWidget(self.next)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.apply = QPushButton("Apply to Field")
        self.apply.setEnabled(False)
        self.apply.setStyleSheet(f"QPushButton {{ background: {SELECT}; font-weight: 600; }}")
        buttons.addWidget(self.apply)
        layout.addLayout(buttons)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(self._load_games)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._load_games)
        self.search_button.clicked.connect(lambda: self._load_games())
        self.previous.clicked.connect(lambda: self._go_to_page(self._page_index - 1))
        self.next.clicked.connect(lambda: self._go_to_page(self._page_index + 1))
        self.list_view.toggled.connect(self._render_page)
        self.games.itemSelectionChanged.connect(self._selection_changed)
        self.games.itemDoubleClicked.connect(self._apply_item)
        self.apply.clicked.connect(self._apply)
        # Enter in the search box always searches rather than accepting a game.
        for button in (self.search_button, self.previous, self.next, close, self.apply):
            button.setAutoDefault(False)
        self._render_page()
        self._update_navigation()
        self.search.setFocus()
        QTimer.singleShot(0, self, self._load_games)

    def _cancel_requests(self):
        self._generation += 1
        for request in self._requests:
            request.set()
        self._requests.clear()

    def _clear_results(self):
        self._cancel_requests()
        self._pages.clear()
        self._images.clear()
        self._page_index = 0
        self.games.clear()
        self._items.clear()
        self.apply.setEnabled(False)

    def _search_changed(self):
        self._clear_results()
        self._loading = False
        self.search_button.setEnabled(True)
        self._update_navigation()
        self.status.setText("Searching..." if self.search.text().strip() else "Loading popular games...")
        self._search_timer.start()

    def _load_games(self):
        if self._closed:
            return
        self._search_timer.stop()
        self._clear_results()
        self._query = self.search.text().strip()
        self._request_page(0, "")

    def _update_navigation(self):
        available = len(self._pages)
        if self._pages and self._pages[-1]["next_page_token"]:
            available += 1
        self.previous.setEnabled(not self._loading and self._page_index > 0)
        self.next.setEnabled(not self._loading and self._page_index + 1 < available)
        self.page_label.setText(f"Page {self._page_index + 1}")

    def _go_to_page(self, index):
        if self._closed or self._loading or index < 0 or index == self._page_index:
            return
        if index < len(self._pages):
            self._cancel_requests()
            self._page_index = index
            self.games.clearSelection()
            self._render_page()
            self._update_navigation()
            self._show_result_status()
            generation, bridge = self._generation, self._bridge
            missing = [game for game in self._pages[index]["games"]
                       if game.universe_id not in self._images]
            self._requests.append(game_selector_mod.start_thumbnails(
                missing, lambda universe, image: bridge.thumbnail.emit(generation, universe, image)))
        elif index == len(self._pages) and self._pages and self._pages[-1]["next_page_token"]:
            self._cancel_requests()
            self._request_page(index, self._pages[-1]["next_page_token"])

    def _request_page(self, index, page_token):
        self._requested_page = index
        self._loading = True
        self.search_button.setEnabled(False)
        self.games.setEnabled(False)
        self.apply.setEnabled(False)
        self._update_navigation()
        self.status.setText(f"Loading page {index + 1}..." if self._query else "Loading popular games...")
        generation = self._generation
        bridge = self._bridge
        self._requests.append(game_selector_mod.start_load(
            self._query, self._session_id, page_token,
            lambda result: bridge.loaded.emit(generation, result),
            lambda universe, image: bridge.thumbnail.emit(generation, universe, image),
        ))

    def _on_loaded(self, generation, result):
        if self._closed or generation != self._generation:
            return
        self._loading = False
        self.search_button.setEnabled(True)
        self.games.setEnabled(True)
        if not result:
            self.status.setText(result.message)
            self._update_navigation()
            self._selection_changed()
            return
        self._pages.append(result.data)
        self._pages.extend({"games": games, "next_page_token": ""}
                           for games in result.data.get("remaining_pages", []))
        self._page_index = self._requested_page
        self.games.clearSelection()
        self._render_page()
        self._update_navigation()
        self._show_result_status()

    def _render_page(self):
        selected = self.games.selectedItems()
        selected_id = selected[0].data(Qt.ItemDataRole.UserRole).universe_id if selected else ""
        self.games.clear()
        self._items.clear()
        is_list = self.list_view.isChecked()
        self.games.setViewMode(QListWidget.ViewMode.ListMode if is_list else QListWidget.ViewMode.IconMode)
        self.games.setWrapping(not is_list)
        self.games.setGridSize(QSize() if is_list else QSize(146, 190))
        self.games.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.games.verticalScrollBar().setSingleStep(40)
        games = self._pages[self._page_index]["games"] if self._pages else []
        for game in games:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, game)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{game.title}, Place ID {game.place_id}")
            item.setSizeHint(QSize(0, 88) if is_list else QSize(146, 190))
            item.setToolTip(f"{game.title}\nPlace ID: {game.place_id}")
            self.games.addItem(item)
            self._items[game.universe_id] = item
            if game.universe_id == selected_id:
                self.games.setCurrentItem(item)
        self._selection_changed()

    def _show_result_status(self):
        count = self.games.count()
        self.status.setText(
            f"{count} {'games found' if self._query else 'popular games'}. Select a game to apply its Place ID."
            if count else "No games found. Try a different search."
        )

    def _on_thumbnail(self, generation, universe_id, image):
        if self._closed or generation != self._generation:
            return
        item = self._items.get(universe_id)
        pixmap = QPixmap()
        if item is not None and pixmap.loadFromData(image):
            self._images[universe_id] = pixmap
            self.games.viewport().update(self.games.visualItemRect(item))

    def _selection_changed(self):
        self.apply.setEnabled(not self._loading and bool(self.games.selectedItems()))

    def _apply(self):
        selected = self.games.selectedItems()
        if selected:
            self._apply_item(selected[0])

    def _apply_item(self, item):
        if not self._loading and not self._closed:
            self.selected_place_id = item.data(Qt.ItemDataRole.UserRole).place_id
            self.accept()

    def done(self, result):
        self._closed = True
        self._search_timer.stop()
        self._cancel_requests()
        super().done(result)


class _ImportCookieDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.cookie_value = ""
        self.setWindowTitle("Import Cookie")
        self.setFixedSize(480, 200)
        self.setStyleSheet(_DLG_STYLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Import Account from Cookie"))
        lay.addWidget(QLabel("Paste one or more .ROBLOSECURITY cookie(s) below:"))

        self._text = QTextEdit()
        self._text.setPlaceholderText("_|WARNING:-Cookie1 _|WARNING:-Cookie2")
        self._text.setFixedHeight(70)
        lay.addWidget(self._text)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Import")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        cn_btn = QPushButton("Cancel")
        cn_btn.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cn_btn)
        lay.addLayout(btn_row)

    def _accept(self):
        self.cookie_value = self._text.toPlainText().strip()
        if self.cookie_value:
            self.accept()

class _AccountCreatorDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.amount = 1
        self.options: dict = {}
        self.setWindowTitle("Account Creator")
        self.setFixedSize(560, 420)
        self.setStyleSheet(_DLG_STYLE + f"""
            QTabWidget::pane {{
                background: {PANEL};
                border: 1px solid {LINE};
                border-radius: 0;
            }}
            QTabBar::tab {{
                background: {INPUT};
                color: {MUTED};
                border: 1px solid {LINE};
                border-bottom: none;
                border-radius: 0;
                min-width: 80px;
                min-height: 24px;
                padding: 2px 10px;
            }}
            QTabBar::tab:selected {{
                background: {SELECT};
                color: {TEXT};
            }}
            QSpinBox {{
                background: {INPUT};
                color: {TEXT};
                border: 1px solid {LINE};
                border-radius: 0;
                min-height: 24px;
                padding: 2px 6px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        title = QLabel("Create Roblox Accounts")
        title.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        lay.addWidget(title)

        warning = QLabel(
            "CAPTCHAs must be completed manually. This program does not "
            "bypass Roblox CAPTCHA protection."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {NOTE}; font-size: 10px;")
        lay.addWidget(warning)

        tabs = QTabWidget()

        basic_page = QWidget()
        basic_lay = QVBoxLayout(basic_page)
        basic_lay.setContentsMargins(12, 12, 12, 12)
        basic_lay.setSpacing(8)

        amount_row = QHBoxLayout()
        amount_label = QLabel("Amount of Accounts")
        amount_label.setToolTip(
            "Create between 1 and 100 accounts.\n"
            "No more than 5 browsers are open at the same time."
        )
        amount_row.addWidget(amount_label)
        amount_row.addStretch(1)
        _spin_style = (
            f"QSpinBox {{ background: {INPUT}; color: {TEXT}; border: 1px solid {LINE};"
            f" border-radius: 0; padding: 2px 6px; font-size: 12px; }}"
        )
        self._amount_spin = QSpinBox()
        self._amount_spin.setRange(1, account_creator_mod.MAX_CREATOR_ACCOUNTS)
        self._amount_spin.setValue(int(actions.get_ui_setting("creator_amount", 1) or 1))
        self._amount_spin.setFixedSize(90, 28)
        self._amount_spin.setStyleSheet(_spin_style)
        self._amount_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        amount_row.addWidget(self._amount_spin)
        basic_lay.addLayout(amount_row)

        interval_row = QHBoxLayout()
        interval_label = QLabel("Wait Between Accounts")
        interval_label.setToolTip(
            "Seconds to wait before starting each next account.\n"
            "0 = start them as fast as possible."
        )
        interval_row.addWidget(interval_label)
        interval_row.addStretch(1)
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(0, account_creator_mod.MAX_INTERVAL_SECONDS)
        self._interval_spin.setValue(int(actions.get_ui_setting("creator_interval_seconds", 0) or 0))
        self._interval_spin.setSuffix(" sec")
        self._interval_spin.setFixedSize(90, 28)
        self._interval_spin.setStyleSheet(_spin_style)
        self._interval_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        interval_row.addWidget(self._interval_spin)
        basic_lay.addLayout(interval_row)

        basic_hint = QLabel(
            "Up to 5 browsers open at once. With a wait set, each new account "
            "starts that many seconds after the previous one."
        )
        basic_hint.setWordWrap(True)
        basic_hint.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        basic_lay.addWidget(basic_hint)
        basic_lay.addStretch(1)
        tabs.addTab(basic_page, "Basic")

        advanced_page = QWidget()
        advanced_lay = QVBoxLayout(advanced_page)
        advanced_lay.setContentsMargins(12, 12, 12, 12)
        advanced_lay.setSpacing(8)

        advanced_desc = QLabel("Optionally add your own prefix and set one password for every created account. Max 14 characters.")
        advanced_desc.setWordWrap(True)
        advanced_desc.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        advanced_desc.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        advanced_desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        advanced_lay.addWidget(advanced_desc)

        self._enable_prefix_chk = QCheckBox("Enable Custom Prefix")
        self._enable_prefix_chk.setChecked(False)
        advanced_lay.addWidget(self._enable_prefix_chk)

        prefix_row = QHBoxLayout()
        prefix_label = QLabel("Custom Prefix")
        prefix_label.setFixedWidth(105)
        prefix_row.addWidget(prefix_label)
        self._username_prefix_edit = QLineEdit()
        self._username_prefix_edit.setPlaceholderText("Example: alts_")
        self._username_prefix_edit.setMaxLength(
            account_creator_mod.MAX_PREFIX_LENGTH
        )
        self._username_prefix_edit.setEnabled(False)
        prefix_row.addWidget(self._username_prefix_edit, 1)
        advanced_lay.addLayout(prefix_row)

        password_row = QHBoxLayout()
        password_label = QLabel("Set Password")
        password_label.setFixedWidth(105)
        password_row.addWidget(password_label)
        self._creator_password_edit = QLineEdit()
        self._creator_password_edit.setPlaceholderText(
            "Leave blank to generate a password for each account"
        )
        self._creator_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        password_row.addWidget(self._creator_password_edit, 1)
        advanced_lay.addLayout(password_row)

        suffix_row = QHBoxLayout()
        suffix_label = QLabel("After Prefix Add")
        suffix_label.setFixedWidth(105)
        suffix_label.setToolTip("What the 6 random characters after your prefix are made of")
        suffix_row.addWidget(suffix_label)
        self._suffix_combo = QComboBox()
        self._suffix_combo.addItem("Numbers (alts_482913)", "numbers")
        self._suffix_combo.addItem("Letters (alts_QmTzkA)", "letters")
        self._suffix_combo.addItem("Numbers + Letters (alts_a8Kz3Q)", "mixed")
        saved_suffix = str(actions.get_ui_setting("creator_suffix_type", "numbers") or "numbers")
        self._suffix_combo.setCurrentIndex(max(0, self._suffix_combo.findData(saved_suffix)))
        self._suffix_combo.setEnabled(False)
        suffix_row.addWidget(self._suffix_combo, 1)
        advanced_lay.addLayout(suffix_row)

        # Amount and wait are mirrored here so they can be set without
        # switching back to Basic; both tabs stay in sync.
        adv_amount_row = QHBoxLayout()
        adv_amount_lbl = QLabel("Amount")
        adv_amount_lbl.setFixedWidth(105)
        adv_amount_row.addWidget(adv_amount_lbl)
        self._adv_amount_spin = QSpinBox()
        self._adv_amount_spin.setRange(1, account_creator_mod.MAX_CREATOR_ACCOUNTS)
        self._adv_amount_spin.setValue(self._amount_spin.value())
        self._adv_amount_spin.setFixedSize(90, 28)
        self._adv_amount_spin.setStyleSheet(_spin_style)
        self._adv_amount_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        adv_amount_row.addWidget(self._adv_amount_spin)
        adv_amount_row.addSpacing(16)
        adv_wait_lbl = QLabel("Wait")
        adv_amount_row.addWidget(adv_wait_lbl)
        self._adv_interval_spin = QSpinBox()
        self._adv_interval_spin.setRange(0, account_creator_mod.MAX_INTERVAL_SECONDS)
        self._adv_interval_spin.setSuffix(" sec")
        self._adv_interval_spin.setValue(self._interval_spin.value())
        self._adv_interval_spin.setFixedSize(90, 28)
        self._adv_interval_spin.setStyleSheet(_spin_style)
        self._adv_interval_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        adv_amount_row.addWidget(self._adv_interval_spin)
        adv_amount_row.addStretch(1)
        advanced_lay.addLayout(adv_amount_row)
        advanced_lay.addStretch(1)

        for a, b in ((self._amount_spin, self._adv_amount_spin),
                     (self._interval_spin, self._adv_interval_spin)):
            a.valueChanged.connect(lambda v, other=b: other.value() != v and other.setValue(v))
            b.valueChanged.connect(lambda v, other=a: other.value() != v and other.setValue(v))

        self._enable_prefix_chk.toggled.connect(
            self._username_prefix_edit.setEnabled
        )
        self._enable_prefix_chk.toggled.connect(self._suffix_combo.setEnabled)
        tabs.addTab(advanced_page, "Advanced")

        lay.addWidget(tabs, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        create_btn = QPushButton("Create Accounts")
        create_btn.setDefault(True)
        create_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(create_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _accept(self):
        enable_custom_prefix = self._enable_prefix_chk.isChecked()
        prefix = self._username_prefix_edit.text().strip()
        if (
            enable_custom_prefix
            and not account_creator_mod.is_valid_prefix(prefix)
        ):
            _show_error(
                self,
                "Invalid Prefix",
                "The prefix must only contain letters, numbers, and _. "
                "It cannot start with _.\n\n"
                "Roblox account creation was not launched.",
            )
            return

        password = self._creator_password_edit.text()
        if password and len(password) < 8:
            _show_error(
                self,
                "Invalid Password",
                "The custom password must contain at least 8 characters.",
            )
            return

        self.amount = self._amount_spin.value()
        actions.save_ui_setting("creator_amount", self.amount)
        actions.save_ui_setting("creator_interval_seconds", self._interval_spin.value())
        self.options = {
            "enable_custom_prefix": enable_custom_prefix,
            "prefix": prefix,
            "password": password,
            "interval_seconds": self._interval_spin.value(),
            "suffix_type": self._suffix_combo.currentData() or "numbers",
        }
        actions.save_ui_setting("creator_suffix_type", self.options["suffix_type"])
        self.accept()


class _RestoreBackupDialog(QDialog):
    """Pick an automatic backup by date, or browse for any .rambak file."""

    def __init__(self, parent, backups: list[dict]):
        super().__init__(parent)
        self.selected_path = ""
        self.setWindowTitle("Restore from Backup")
        self.setMinimumSize(460, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        title = QLabel("Choose a backup to restore")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        lay.addWidget(title)
        self._list = QListWidget()
        for backup in backups:
            item = QListWidgetItem(f"{backup['when']:%Y-%m-%d  %H:%M}    ·    {backup['count']} accounts")
            item.setData(Qt.ItemDataRole.UserRole, backup["path"])
            self._list.addItem(item)
        if not backups:
            empty = QListWidgetItem("No automatic backups yet. Use Browse to pick a .rambak file.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty)
        else:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _item: self._choose())
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        restore = QPushButton("Restore")
        restore.setObjectName("primary")
        restore.setStyleSheet(PRIMARY_BUTTON_QSS)
        restore.setMinimumWidth(120)
        restore.clicked.connect(self._choose)
        row.addWidget(restore)
        lay.addLayout(row)

    def _choose(self) -> None:
        item = self._list.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else ""
        if path:
            self.selected_path = path
            self.accept()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Restore from Backup", "",
                                              "Account backup (*.rambak);;All files (*)")
        if path:
            self.selected_path = path
            self.accept()


class _CreatorProgressDialog(QDialog):
    """Small live window while accounts are being created, with a Stop button."""

    def __init__(self, parent, requested: int, stop_event: threading.Event):
        super().__init__(parent)
        self._stop_event = stop_event
        self._done = False
        self.setWindowTitle("Account Creator")
        self.setModal(False)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        title = QLabel("Creating accounts…")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        lay.addWidget(title)
        self._status = QLabel(f"0 of {requested} created")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lay.addWidget(self._status)
        hint = QLabel("Complete any CAPTCHA in the browser windows. Stop closes the signup windows "
                      "and keeps every account already created.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        lay.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("primary")
        self._stop_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self._stop_btn.setAutoDefault(False)
        self._stop_btn.setMinimumWidth(120)
        self._stop_btn.clicked.connect(self._stop)
        row.addWidget(self._stop_btn)
        lay.addLayout(row)

    def _stop(self) -> None:
        self._stop_event.set()
        self._stop_btn.setEnabled(False)
        self._stop_btn.setText("Stopping…")
        self._status.setText(self._status.text() + " · stopping: closing the signup windows…")
        self._status.setWordWrap(True)
        print("[Account Creator] Stop pressed: closing the signup windows, no new accounts will start.")

    def update_progress(self, created: int, finished: int, requested: int) -> None:
        if not self._stop_event.is_set():
            self._status.setText(f"{created} of {requested} created · {finished} attempted")

    def finish(self) -> None:
        self._done = True
        self.close()

    def closeEvent(self, event):
        if not self._done and not self._stop_event.is_set():
            # Closing the window keeps the run going; only Stop ends it.
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)


class _CreatorResultsDialog(QDialog):
    """Clear summary of an Account Creator run: counts, every created account, every failure."""

    def __init__(self, parent, report: dict, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Account Creator — Results")
        self.setMinimumSize(520, 460)
        created = list(report.get("created") or [])
        failures = list(report.get("failures") or [])
        requested = int(report.get("requested") or 0)
        stopped = bool(report.get("stopped"))
        stopped_open = int(report.get("stopped_in_progress") or 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        not_started = max(0, requested - len(created) - len(failures) - stopped_open)
        head = QLabel(("Stopped. " if stopped else "Done. ")
                      + f"{len(created)} created" + (f" · {len(failures)} failed" if failures else "")
                      + (f" · {stopped_open} stopped" if stopped_open else "")
                      + (f" · {not_started} not started" if stopped and not_started else ""))
        head.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(head)
        sub = QLabel(f"{len(created)} of {requested} requested accounts were added to your Account List.")
        sub.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lay.addWidget(sub)

        created_lbl = QLabel(f"Created accounts ({len(created)})")
        created_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        lay.addWidget(created_lbl)
        self._created = QTextEdit()
        self._created.setReadOnly(True)
        self._created.setPlainText("\n".join(created) if created else "None")
        lay.addWidget(self._created, 2)

        if failures:
            failed_lbl = QLabel(f"Failed ({len(failures)})")
            failed_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
            lay.addWidget(failed_lbl)
            grouped: dict[str, int] = {}
            for failure in failures:
                reason = failure.get("message") or failure.get("code") or "Unknown error"
                grouped[reason] = grouped.get(reason, 0) + 1
            failed_box = QTextEdit()
            failed_box.setReadOnly(True)
            failed_box.setPlainText("\n".join(f"{count}× {reason}" for reason, count in grouped.items()))
            lay.addWidget(failed_box, 1)
        elif not created and message:
            note = QLabel(message)
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
            lay.addWidget(note)

        row = QHBoxLayout()
        copy_btn = QPushButton("Copy all usernames")
        copy_btn.setEnabled(bool(created))
        copy_btn.setAutoDefault(False)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText("\n".join(created)))
        row.addWidget(copy_btn)
        row.addStretch(1)
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("primary")
        ok_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        ok_btn.setMinimumWidth(120)
        ok_btn.clicked.connect(self.accept)
        row.addWidget(ok_btn)
        lay.addLayout(row)


class _ImportUserPassDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.pairs: list[tuple[str, str]] = []
        self._row_widgets: list[tuple[QLineEdit, QLineEdit, QWidget]] = []
        self.setWindowTitle("Import User:Pass")
        self.setFixedSize(460, 380)
        self.setStyleSheet(_DLG_STYLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Import Accounts from Username:Password"))
        desc = QLabel("Enter one account per row, or import a .txt file of Username:Password lines.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(desc)

        self._rows_scroll = QScrollArea()
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        rows_widget = QWidget()
        self._rows_lay = QVBoxLayout(rows_widget)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(6)
        self._rows_lay.addStretch(1)
        self._rows_scroll.setWidget(rows_widget)
        lay.addWidget(self._rows_scroll, 1)

        self._add_row()

        btn_row = QHBoxLayout()
        file_btn = QPushButton("Import from .txt")
        file_btn.clicked.connect(self._on_import_file)
        btn_row.addWidget(file_btn)
        btn_row.addStretch(1)
        ok_btn = QPushButton("Import")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        cn_btn = QPushButton("Cancel")
        cn_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cn_btn)
        lay.addLayout(btn_row)

    def _add_row(self, username: str = "", password: str = ""):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        user_edit = QLineEdit()
        user_edit.setPlaceholderText("Username")
        user_edit.setText(username)
        pass_edit = QLineEdit()
        pass_edit.setPlaceholderText("Password")
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_edit.setText(password)

        row.addWidget(user_edit, 1)
        row.addWidget(pass_edit, 1)

        row_widget = QWidget()
        row_widget.setLayout(row)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, row_widget)
        self._row_widgets.append((user_edit, pass_edit, row_widget))
        user_edit.textChanged.connect(lambda text, ue=user_edit: self._on_row_username_typed(ue, text))

    def _remove_row(self, index: int):
        _, _, row_widget = self._row_widgets.pop(index)
        self._rows_lay.removeWidget(row_widget)
        row_widget.setParent(None)
        row_widget.deleteLater()

    def _on_row_username_typed(self, user_edit: QLineEdit, text: str):
        idx = next((i for i, (u, _, _) in enumerate(self._row_widgets) if u is user_edit), None)
        if idx is None:
            return
        is_last = idx == len(self._row_widgets) - 1

        if text.strip():
            if is_last:
                self._add_row()
        else:
            if not is_last and idx == len(self._row_widgets) - 2:
                last_user, last_pass, _ = self._row_widgets[-1]
                if not last_user.text().strip() and not last_pass.text().strip():
                    self._remove_row(len(self._row_widgets) - 1)

    def _on_import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import User:Pass File", "", "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        pairs = actions.parse_user_pass_file(path)
        if not pairs:
            _show_error(self, "No accounts found", "No valid Username:Password lines were found in that file.")
            return
        for username, password in pairs:
            self._add_row(username, password)
        self._add_row()

    def _accept(self):
        pairs: list[tuple[str, str]] = []
        for user_edit, pass_edit, _ in self._row_widgets:
            username = user_edit.text().strip()
            password = pass_edit.text()
            if username and password:
                pairs.append((username, password))
        if not pairs:
            _show_error(self, "Missing Info", "Enter at least one Username:Password pair.")
            return
        self.pairs = pairs
        self.accept()


class _AddJavascriptDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.js_value = ""
        self.setWindowTitle("Add via Javascript")
        self.setFixedSize(480, 260)
        self.setStyleSheet(_DLG_STYLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel(
            "A browser will open for login.\n"
            "Optionally paste Javascript to execute after the page loads:"
        ))

        self._edit = QTextEdit()
        self._edit.setPlaceholderText("// optional JS\nconsole.log('hello');")
        lay.addWidget(self._edit, 1)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Open Browser")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        cn_btn = QPushButton("Cancel")
        cn_btn.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cn_btn)
        lay.addLayout(btn_row)

    def _accept(self):
        self.js_value = self._edit.toPlainText()
        self.accept()


class _EditNoteDialog(QDialog):
    def __init__(self, username: str, current_note: str, parent):
        super().__init__(parent)
        self.note_value = current_note
        self.setWindowTitle(f"Edit Note - {username}")
        self.setFixedSize(380, 150)
        self.setStyleSheet(_DLG_STYLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel(f"Note for {username}:"))

        self._entry = QLineEdit(current_note)
        self._entry.setPlaceholderText("Enter a note…")
        lay.addWidget(self._entry)

        btn_row = QHBoxLayout()
        clr_btn = QPushButton("Clear")
        clr_btn.clicked.connect(self._entry.clear)
        ok_btn = QPushButton("Save")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        cn_btn = QPushButton("Cancel")
        cn_btn.clicked.connect(self.reject)
        btn_row.addWidget(clr_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cn_btn)
        lay.addLayout(btn_row)

    def _accept(self):
        self.note_value = self._entry.text()
        self.accept()


class _AutoRejoinAddWindow(QDialog):
    # Panel for adding accounts to auto rejoin and edit accounts
    # Left side: List of accounts
    # Right side: config form
    def __init__(self, manager, parent=None, edit_account=None, edit_config=None):
        super().__init__(parent)
        self.manager = manager
        self.result_configs: dict = {}
        self._edit_mode = edit_account is not None
        self._edit_account = edit_account
        self._edit_config = edit_config or {}
        self._validation_rows: dict[str, tuple[QWidget, QLabel, QLabel]] = {}

        self.setWindowTitle("Edit Auto-Rejoin" if self._edit_mode else "Add Account to Auto-Rejoin")
        self.setMinimumSize(420 if self._edit_mode else 740, 480)
        self.resize(440 if self._edit_mode else 800, 600)
        self.setStyleSheet(_DLG_STYLE + f"""
            QListWidget {{
                background: {INPUT}; border: 1px solid {LINE};
                font-size: 11px; color: {TEXT};
            }}
            QListWidget::item:selected {{ background: {SELECT}; }}
            QLabel#accountName {{ color: {TEXT}; font-size: 11px; }}
            QLabel#noteSep {{ color: #7A7A7A; font-size: 11px; }}
            QLabel#noteText {{ color: {NOTE}; font-size: 11px; font-weight: 600; }}
            QPushButton#groupTab {{
                background: transparent; border: 1px solid {LINE};
                color: {MUTED}; font-size: 10px; padding: 2px 8px;
                min-height: 20px;
            }}
            QPushButton#groupTab:checked {{ background: {SELECT}; color: {TEXT}; }}
            QPushButton#groupTab:hover   {{ background: {SELECT}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        # Title bar row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Edit Auto-Rejoin" if self._edit_mode else "Add Account to Auto-Rejoin")
        lbl.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: bold;")
        title_row.addWidget(lbl)
        title_row.addStretch(1)
        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {MUTED}; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {TEXT}; }}"
        )
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn)
        root.addLayout(title_row)

        # Sub-title hint directly below the title
        self._hint_label = QLabel("Ctrl / Shift to select multiple accounts")
        self._hint_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        # Left side
        self._left_widget = QWidget()
        left = QVBoxLayout(self._left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        accounts_title = QLabel("Accounts")
        accounts_title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600;")
        left.addWidget(accounts_title)
        left.addWidget(self._hint_label)

        self._current_group: str | None = None

        # Group scroll bar
        self._gscroll = QScrollArea()
        self._gscroll.setWidgetResizable(True)
        self._gscroll.setFixedHeight(28)
        self._gscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._gscroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._gscroll.setStyleSheet("background: transparent; border: none;")

        _gbar_widget = QWidget()
        _gbar_widget.setStyleSheet("background: transparent;")
        self._gbar_lay = QHBoxLayout(_gbar_widget)
        self._gbar_lay.setContentsMargins(0, 0, 0, 0)
        self._gbar_lay.setSpacing(4)
        self._gscroll.setWidget(_gbar_widget)
        left.addWidget(self._gscroll)

        self._acc_list = QListWidget()
        self._acc_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        left.addWidget(self._acc_list, 1)

        body.addWidget(self._left_widget, 1)

        # Right side
        form = QWidget()
        right = QVBoxLayout(form)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)

        _LBL = f"color: {MUTED}; font-size: 11px;"
        _INP = (
            f"QLineEdit {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 6px; min-height: 24px; font-size: 11px; }}"
        )
        _SPN = (
            f"QSpinBox {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 6px; min-height: 24px; font-size: 11px; }}"
        )
        _CHK = (
            f"QCheckBox {{ color: {TEXT}; font-size: 11px; }}"
            f"QCheckBox::indicator {{ width: 13px; height: 13px; }}"
        )

        # Account label for edit mode
        self._account_lbl = QLabel(f"Account: {edit_account}" if self._edit_mode else "")
        self._account_lbl.setWordWrap(True)
        self._account_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._account_lbl.setVisible(self._edit_mode)
        right.addWidget(self._account_lbl)

        cfg_hdr = QLabel("Destination")
        cfg_hdr.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600;")
        right.addWidget(cfg_hdr)
        self._join_mode = QComboBox()
        self._join_mode.addItem("Join a place", "place")
        self._join_mode.addItem("Join a user", "user")
        self._join_mode.setAccessibleName("Auto-Rejoin destination type")
        self._join_mode.setStyleSheet(
            f"QComboBox {{ background: {INPUT}; border: 1px solid {LINE};"
            f" color: {TEXT}; padding: 4px 6px; min-height: 24px; font-size: 11px; }}"
        )
        right.addWidget(self._join_mode)

        self._place_fields = QWidget()
        place_fields = QVBoxLayout(self._place_fields)
        place_fields.setContentsMargins(0, 0, 0, 0)
        place_fields.setSpacing(6)
        place_label = QLabel("Place ID", styleSheet=_LBL)
        place_fields.addWidget(place_label)
        self._place = QLineEdit()
        self._place.setPlaceholderText("e.g. 8562822414")
        self._place.setStyleSheet(_INP)
        place_label.setBuddy(self._place)
        place_fields.addWidget(self._place)

        private_label = QLabel("Private server link or code (optional)", styleSheet=_LBL)
        place_fields.addWidget(private_label)
        self._ps = QLineEdit()
        self._ps.setPlaceholderText("optional")
        self._ps.setStyleSheet(_INP)
        private_label.setBuddy(self._ps)
        place_fields.addWidget(self._ps)

        job_label = QLabel("Job ID (optional)", styleSheet=_LBL)
        place_fields.addWidget(job_label)
        self._job = QLineEdit()
        self._job.setPlaceholderText("optional")
        self._job.setStyleSheet(_INP)
        job_label.setBuddy(self._job)
        place_fields.addWidget(self._job)
        right.addWidget(self._place_fields)

        self._user_fields = QWidget()
        user_fields = QVBoxLayout(self._user_fields)
        user_fields.setContentsMargins(0, 0, 0, 0)
        user_fields.setSpacing(6)
        user_label = QLabel("Roblox username or user ID", styleSheet=_LBL)
        user_fields.addWidget(user_label)
        self._target_user = QLineEdit()
        self._target_user.setMaxLength(20)
        self._target_user.setPlaceholderText("Username or user ID")
        self._target_user.setStyleSheet(_INP)
        user_label.setBuddy(self._target_user)
        user_fields.addWidget(self._target_user)
        right.addWidget(self._user_fields)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {LINE}; border: none;")
        right.addWidget(divider)
        monitoring_title = QLabel("Monitoring")
        monitoring_title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600;")
        right.addWidget(monitoring_title)

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        interval_column = QVBoxLayout()
        interval_label = QLabel("Check every", styleSheet=_LBL)
        interval_column.addWidget(interval_label)
        self._interval = QSpinBox()
        self._interval.setRange(5, 300)
        self._interval.setValue(10)
        self._interval.setSingleStep(5)
        self._interval.setSuffix(" s")
        self._interval.setStyleSheet(_SPN)
        self._interval.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        interval_label.setBuddy(self._interval)
        interval_column.addWidget(self._interval)
        row1.addLayout(interval_column, 1)

        retries_column = QVBoxLayout()
        retries_label = QLabel("Maximum retries", styleSheet=_LBL)
        retries_column.addWidget(retries_label)
        self._retries = QSpinBox()
        self._retries.setRange(1, 50)
        self._retries.setValue(5)
        self._retries.setStyleSheet(_SPN)
        self._retries.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        retries_label.setBuddy(self._retries)
        retries_column.addWidget(self._retries)
        row1.addLayout(retries_column, 1)
        right.addLayout(row1)

        self._presence_chk = QCheckBox("Check account presence")
        self._presence_chk.setChecked(True)
        self._presence_chk.setStyleSheet(_CHK)
        self._presence_chk.toggled.connect(self._update_presence_place_controls)
        right.addWidget(self._presence_chk)

        self._presence_place_chk = QCheckBox("Also check the joined game")
        self._presence_place_chk.setToolTip("Check whether the account is still in the game it last joined.")
        self._presence_place_chk.setChecked(True)
        self._presence_place_chk.setStyleSheet(_CHK)
        right.addWidget(self._presence_place_chk)
        self._update_presence_place_controls()

        self._internet_chk = QCheckBox("Check internet before launch")
        self._internet_chk.setChecked(True)
        self._internet_chk.setStyleSheet(_CHK)
        right.addWidget(self._internet_chk)

        right.addStretch(1)

        self._join_mode.currentIndexChanged.connect(self._update_join_mode)
        self._update_join_mode()
        self._form_scroll = QScrollArea()
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._form_scroll.setWidget(form)
        body.addWidget(self._form_scroll, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        # Save changes if edit mode
        # Add account if add mode
        self._add_btn = QPushButton("Save Changes" if self._edit_mode else "Add Account")
        self._add_btn.setDefault(True)
        self._add_btn.setStyleSheet(
            f"QPushButton {{ background: {SELECT}; border: 1px solid {LINE};"
            f"  min-height: 30px; font-weight: 700; text-align: center; color: {TEXT}; }}"
            f"QPushButton:hover   {{ background: {HOVER}; }}"
            f"QPushButton:pressed {{ background: {PRESSED}; }}"
        )
        self._add_btn.clicked.connect(self._on_add)
        footer.addWidget(self._add_btn)
        root.addLayout(footer)

        if self._edit_mode:
            self._apply_edit_mode()
        else:
            self._rebuild_group_bar()
            self._populate_list()

        bridge = getattr(parent, "_bridge", None)
        if bridge is not None:
            bridge.cookie_validated.connect(self._on_cookie_validated)
            self.finished.connect(
                lambda _result: bridge.cookie_validated.disconnect(self._on_cookie_validated)
            )

        self._resize_handle = _ResizeHandle(self)
        self._position_resize_handle()

    def _position_resize_handle(self) -> None:
        handle = getattr(self, "_resize_handle", None)
        if handle is not None:
            handle.move(self.width() - handle.width(), self.height() - handle.height())
            handle.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_resize_handle()

    def _apply_edit_mode(self):
        # Hide left panel for single account editing
        self._left_widget.hide()
        self._hint_label.hide()

        # Pre-fill config values for the account being edited
        cfg = self._edit_config
        self._place.setText(cfg.get("place_id", ""))
        self._ps.setText(cfg.get("private_server", ""))
        self._job.setText(cfg.get("job_id", ""))
        self._target_user.setText(str(cfg.get("target_user", "")))
        self._join_mode.setCurrentIndex(1 if cfg.get("join_mode") == "user" else 0)
        self._interval.setValue(int(cfg.get("check_interval", 10)))
        self._retries.setValue(int(cfg.get("max_retries", 5)))
        self._presence_chk.setChecked(bool(cfg.get("check_presence", True)))
        self._presence_place_chk.setChecked(bool(cfg.get("check_place_id", True)))
        self._update_presence_place_controls()
        self._internet_chk.setChecked(bool(cfg.get("check_internet", True)))

    def _update_presence_place_controls(self):
        self._presence_place_chk.setEnabled(self._presence_chk.isChecked())

    def _update_join_mode(self):
        join_user = self._join_mode.currentData() == "user"
        self._place_fields.setVisible(not join_user)
        self._user_fields.setVisible(join_user)

    def _rebuild_group_bar(self):
        while self._gbar_lay.count():
            child = self._gbar_lay.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for gval, glabel in [(None, "All")] + [(g, g) for g in groups.get_group_names()]:
            btn = QPushButton(glabel)
            btn.setObjectName("groupTab")
            btn.setCheckable(True)
            btn.setChecked(self._current_group == gval)
            btn.clicked.connect(lambda _=False, g=gval: self._set_group(g))
            self._gbar_lay.addWidget(btn)
        self._gbar_lay.addStretch(1)

    def _set_group(self, group_name):
        self._current_group = group_name
        self._rebuild_group_bar()
        self._populate_list()
    
    # Account list population
    def _populate_list(self):
        self._acc_list.clear()
        self._validation_rows.clear()
        AV = avatars.AVATAR_SIZE
        ITEM_H = AV + 6

        items = list(self.manager.accounts.items())
        if self._current_group is not None:
            items = [(u, d) for u, d in items if groups.get_account_group(u) == self._current_group]

        for username, data in items:
            note = data.get("note", "") if isinstance(data, dict) else ""
            item = QListWidgetItem("")
            item.setSizeHint(QSize(0, ITEM_H))
            item.setData(Qt.ItemDataRole.UserRole, username)

            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 0, 6, 0)
            rl.setSpacing(6)

            # Avatar
            av_container = QWidget()
            av_container.setFixedSize(AV, AV)
            av = QLabel(av_container)
            av.setFixedSize(AV, AV)
            av.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)

            # Use cached pixmap from main account list
            par = self.parent()
            cached_pix = None
            if par and hasattr(par, "_avatar_labels"):
                src_lbl = par._avatar_labels.get(username)
                if src_lbl:
                    cached_pix = src_lbl.pixmap()
            if cached_pix and not cached_pix.isNull():
                av.setPixmap(cached_pix.scaled(
                    AV, AV, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
                ))
            else:
                if par and hasattr(par, "_make_placeholder_pixmap"):
                    av.setPixmap(par._make_placeholder_pixmap(AV))
            rl.addWidget(av_container)
            badge = AccountManagerUIQt._create_invalid_badge(av_container)

            name_lbl = QLabel(username)
            name_lbl.setObjectName("accountName")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            rl.addWidget(name_lbl)

            if note:
                sep = QLabel("|")
                sep.setObjectName("noteSep")
                sep.setAlignment(Qt.AlignmentFlag.AlignVCenter)
                rl.addWidget(sep)
                note_lbl = QLabel(note)
                note_lbl.setObjectName("noteText")
                note_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
                rl.addWidget(note_lbl)

            rl.addStretch(1)
            row.setFixedHeight(ITEM_H)

            self._acc_list.addItem(item)
            self._acc_list.setItemWidget(item, row)
            self._validation_rows[username] = (row, name_lbl, badge)
            flagged = cookie_validator_mod.is_flagged(data) if isinstance(data, dict) else False
            self._apply_cookie_status_to_row(username, flagged)

    def _on_cookie_validated(self, username: str, status: str) -> None:
        self._apply_cookie_status_to_row(username, status == cookie_validator_mod.INVALID)

    def _apply_cookie_status_to_row(self, username: str, flagged: bool) -> None:
        widgets = self._validation_rows.get(username)
        if widgets is None:
            return
        row, name_label, badge = widgets
        badge.setVisible(flagged)
        name_label.setStyleSheet("color: #E8A020; font-style: italic;" if flagged else "")
        name_label.setToolTip(badge.toolTip() if flagged else "")
        row.setStyleSheet(
            "QWidget { background: rgba(200, 50, 50, 0.06); }" if flagged else ""
        )

    def _on_add(self):
        join_user = self._join_mode.currentData() == "user"
        target_user = self._target_user.text().strip()
        place_id = self._place.text().strip()
        if join_user and not re.fullmatch(r"[A-Za-z0-9_]{1,20}", target_user):
            QMessageBox.warning(self, "Invalid User", "Enter a Roblox username or user ID.")
            self._target_user.setFocus()
            return
        if join_user and target_user.isdigit() and int(target_user) <= 0:
            QMessageBox.warning(self, "Invalid User ID", "User ID must be a positive number.")
            self._target_user.setFocus()
            return
        if not join_user and not place_id:
            QMessageBox.warning(self, "Missing Place ID", "Enter a Place ID to monitor.")
            return
        if not join_user and (not place_id.isdigit() or int(place_id) <= 0):
            QMessageBox.critical(self, "Invalid Place ID", "Place ID must be a number.")
            return

        cfg = {
            "join_mode": "user" if join_user else "place",
            "target_user": target_user if join_user else "",
            "place_id": "" if join_user else place_id,
            "private_server": "" if join_user else self._ps.text().strip(),
            "job_id": "" if join_user else self._job.text().strip(),
            "check_interval": self._interval.value(),
            "max_retries": self._retries.value(),
            "check_presence": self._presence_chk.isChecked(),
            "check_place_id": self._presence_place_chk.isChecked(),
            "check_internet": self._internet_chk.isChecked(),
        }

        if self._edit_mode:
            self.result_configs[self._edit_account] = cfg
        else:
            # In add mode, require account selection
            selected = [
                self._acc_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._acc_list.count())
                if self._acc_list.item(i).isSelected()
            ]
            if not selected:
                QMessageBox.warning(self, "No Selection", "Select at least one account from the list.")
                return
            for account in selected:
                self.result_configs[account] = cfg.copy()
        self.accept()

# Tiny message helpers
def _show_error(parent, title: str, msg: str):
    if not msg:
        return
    splash.dismiss()
    dlg = QMessageBox(parent)
    dlg.setWindowTitle(title)
    dlg.setText(msg)
    dlg.setIcon(QMessageBox.Icon.Critical)
    dlg.setStyleSheet(f"QMessageBox {{ background: {BG}; color: {TEXT}; }}")
    dlg.exec()


def _show_info(parent, title: str, msg: str):
    dlg = QMessageBox(parent)
    dlg.setWindowTitle(title)
    dlg.setText(msg)
    dlg.setIcon(QMessageBox.Icon.Information)
    dlg.setStyleSheet(f"QMessageBox {{ background: {BG}; color: {TEXT}; }}")
    dlg.exec()


# Palette
def apply_palette(app: QApplication) -> None:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(BG))
    p.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Base, QColor(INPUT))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    p.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Button, QColor(INPUT))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Highlight, QColor(SELECT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT))
    app.setPalette(p)


class _PasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.password_value = ""
        self.setWindowTitle("Password Required")
        self.setFixedSize(360, 130)
        self.setStyleSheet(_DLG_STYLE)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.MSWindowsFixedSizeDialogHint)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Enter your password to unlock:"))

        self._entry = QLineEdit()
        self._entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._entry.setPlaceholderText("Password")
        self._entry.returnPressed.connect(self._on_accept)
        lay.addWidget(self._entry)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton("Unlock")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        cn_btn = QPushButton("Cancel")
        cn_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cn_btn)
        lay.addLayout(btn_row)

    def _on_accept(self):
        self.password_value = self._entry.text()
        self.accept()


def _show_account_recovery_warning(manager):
    source = manager.accounts_recovery_source
    if source:
        QMessageBox.warning(
            None,
            "Accounts Recovered",
            "The latest saved accounts could not be opened. Your previous "
            "saved copy was loaded instead. Recent changes may be missing.\n\n"
            f"Backup:\n{source}",
        )


def main(icon_path: str | None = None, *, smoke_test: bool = False) -> int:
    # QApplication MUST exist before any QWidget / QDialog is created.
    # Create it first, before setup_encryption() and before _PasswordDialog.
    diagnostics.set_startup_stage("creating QApplication")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Roblox Account Manager")
    _app_font = QFont("Segoe UI", 10)
    _app_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    _app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(_app_font)
    apply_palette(app)
    motion.install(app, TEXT, actions.load_ui_settings(), glow=FG_ACCENT)
    diagnostics.set_startup_stage("QApplication ready")
    splash.status("Starting", 0.2)

    password = None
    try:
        diagnostics.set_startup_stage("loading encryption settings")
        data_folder = get_data_dir()
        os.makedirs(data_folder, exist_ok=True)
        enc_cfg = EncryptionConfig(os.path.join(data_folder, "encryption_config.json"))

        if (enc_cfg.is_encryption_enabled()
                and enc_cfg.get_encryption_method() == "password"):
            splash.pause()
            dlg = _PasswordDialog()
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.password_value:
                _show_error(None, "Error", "Password is required.")
                return 1
            password = dlg.password_value
            splash.resume()
    except Exception as exc:
        crash_path = diagnostics.report_exception(
            "Loading encryption settings",
            exc,
            fatal=True,
        )
        _show_error(
            None,
            "Encryption Settings Error",
            "The encryption settings could not be loaded.\n\n"
            f"Details were saved to:\n{crash_path}",
        )
        return 1

    try:
        diagnostics.set_startup_stage("initializing account manager")
        splash.status("Opening your accounts", 0.5)
        manager = RobloxAccountManager(password=password)
    except AccountPasswordError as exc:
        print(f"[ERROR] ACCOUNT_PASSWORD_INVALID: {exc}")
        _show_error(
            None,
            "Invalid Password",
            "The password could not unlock the encrypted account data. "
            "Please try again.\n\n"
            "Error code: ACCOUNT_PASSWORD_INVALID",
        )
        return 1
    except PasswordRequiredError as exc:
        print(f"[ERROR] ACCOUNT_PASSWORD_REQUIRED: {exc}")
        _show_error(
            None,
            "Password Required",
            "A password is required to open the encrypted account data.\n\n"
            "Error code: ACCOUNT_PASSWORD_REQUIRED",
        )
        return 1
    except HardwareAccountDecryptionError as exc:
        crash_path = diagnostics.report_exception(
            "Opening hardware-encrypted account data",
            exc,
            fatal=True,
        )
        _show_error(
            None,
            "Hardware Encryption Error",
            "These accounts are Device Locked: they only open on the PC they were saved on. "
            "The original file was not changed.\n\n"
            "To move them: on that PC, use Export Accounts (with a password) and import the file "
            "here, or switch that PC to Password Locked (Settings → Misc → Encryption) and copy "
            "the data folder again.\n\n"
            "Error code: ACCOUNT_HARDWARE_DECRYPTION_FAILED\n\n"
            f"Details were saved to:\n{crash_path}",
        )
        return 1
    except AccountDataError as exc:
        crash_path = diagnostics.report_exception(
            "Loading saved account data",
            exc,
            fatal=True,
        )
        _show_error(
            None,
            "Account Data Error",
            "The saved account data could not be loaded. The original "
            "file was not changed.\n\n"
            "Error code: ACCOUNT_DATA_INVALID\n\n"
            f"Details were saved to:\n{crash_path}",
        )
        return 1
    except Exception as exc:
        crash_path = diagnostics.report_exception(
            "Initializing account manager",
            exc,
            fatal=True,
        )
        _show_error(
            None,
            "Account Manager Could Not Start",
            "The account data could not be loaded.\n\n"
            f"Details were saved to:\n{crash_path}",
        )
        return 1

    _show_account_recovery_warning(manager)

    if not icon_path or not os.path.exists(icon_path):
        icon_path = os.path.join(get_data_dir(), "icon.ico")
        if not os.path.exists(icon_path):
            _alt = get_resource_path("assets", "icon.ico")
            icon_path = _alt if os.path.exists(_alt) else None

    try:
        app_icon = _app_icon(icon_path)
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)
            if hasattr(app, "setApplicationIcon"):
                app.setApplicationIcon(app_icon)
    except Exception:
        pass

    try:
        diagnostics.set_startup_stage("creating main window")
        splash.status("Building the interface", 0.8)
        window = AccountManagerUIQt(manager, icon_path=icon_path)
        app.aboutToQuit.connect(window._perform_shutdown_cleanup)
    except Exception as exc:
        crash_path = diagnostics.report_exception(
            "Creating the main window",
            exc,
            fatal=True,
        )
        _show_error(
            None,
            "ANAne RAM Could Not Start",
            "The main window could not be created.\n\n"
            f"Details were saved to:\n{crash_path}",
        )
        return 1

    if smoke_test:
        splash.dismiss()
        window.show()
        QTimer.singleShot(0, app.quit)
    else:
        splash.status("Ready", 1.0)
        splash.reveal(window, on_ready=window._restore_multi_roblox)
    diagnostics.mark_ui_ready()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
