"""
Small, optional animations: button hover and press feedback, page fades and
dialogs that fade in.

Nothing here touches a widget's stylesheet. Buttons get a transparent child
overlay that is tinted with the theme's text color, so every existing button
style keeps working.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QDialog, QGraphicsOpacityEffect, QPushButton, QToolButton, QWidget

ANIMATIONS_SETTING = "ui_animations"
HOVER_ALPHA = 14
PRESS_ALPHA = 22
RIM_ALPHA = 60
HOVER_MS = 170
PRESS_MS = 90
FADE_MS = 150
DIALOG_FADE_MS = 140
RADIUS = 10.0
# Flat buttons (no frame of their own) get the tint but not the glow rim.
FLAT_BUTTONS = frozenset({
    "sideNav", "navTab", "groupTab", "titleButton", "closeButton", "rowMenu", "discordRound",
})
_MARKER = "_motion_attached"
_SPI_GETCLIENTAREAANIMATION = 0x1042


def system_animations_enabled() -> bool:
    try:
        flag = wintypes.BOOL(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0,
        )
        return bool(flag.value) if ok else True
    except (AttributeError, OSError):
        return True


def animations_enabled(settings: dict | None = None) -> bool:
    settings = settings if isinstance(settings, dict) else {}
    if not bool(settings.get(ANIMATIONS_SETTING, True)):
        return False
    return system_animations_enabled()


class _Overlay(QWidget):
    def __init__(self, motion: "ButtonMotion", parent: QWidget):
        super().__init__(parent)
        self._motion = motion
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, _event):
        alpha = self._motion.current_alpha()
        if alpha <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(RADIUS, rect.height() / 2)
        color = QColor(self._motion.tint)
        color.setAlpha(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, radius, radius)
        rim = self._motion.rim_alpha()
        if rim > 0:
            glow = QColor(self._motion.glow)
            glow.setAlpha(rim)
            painter.setPen(QPen(glow, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)


class ButtonMotion(QObject):
    def __init__(self, button: QAbstractButton, tint: str, animate: bool = True, glow: str | None = None):
        super().__init__(button)
        self.button = button
        self.tint = tint
        self.glow = glow or tint
        self.flat = button.objectName() in FLAT_BUTTONS
        self.animate = animate
        self.hover = 0.0
        self.press = 0.0
        self._hover_animation = self._make_animation(HOVER_MS, "hover")
        self._press_animation = self._make_animation(PRESS_MS, "press")
        self.overlay = _Overlay(self, button)
        self.overlay.setGeometry(button.rect())
        self.overlay.raise_()
        self.overlay.show()
        button.installEventFilter(self)

    def _make_animation(self, duration: int, attribute: str) -> QVariantAnimation:
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value, name=attribute: self._set(name, float(value)))
        return animation

    def _set(self, attribute: str, value: float) -> None:
        setattr(self, attribute, value)
        self.overlay.update()

    def current_alpha(self) -> int:
        if not self.button.isEnabled():
            return 0
        return min(255, round(HOVER_ALPHA * self.hover + PRESS_ALPHA * self.press))

    def rim_alpha(self) -> int:
        if self.flat or not self.button.isEnabled():
            return 0
        return min(255, round(RIM_ALPHA * self.hover))

    def _go(self, attribute: str, target: float) -> None:
        animation = self._hover_animation if attribute == "hover" else self._press_animation
        animation.stop()
        if not self.animate:
            self._set(attribute, target)
            return
        animation.setStartValue(getattr(self, attribute))
        animation.setEndValue(target)
        animation.start()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.Enter:
            self._go("hover", 1.0)
        elif kind in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._go("hover", 0.0)
            self._go("press", 0.0)
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._go("press", 1.0)
        elif kind == QEvent.Type.MouseButtonRelease:
            self._go("press", 0.0)
        elif kind == QEvent.Type.Resize:
            self.overlay.setGeometry(self.button.rect())
            self.overlay.raise_()
        elif kind == QEvent.Type.EnabledChange:
            self.overlay.update()
        return False


class MotionFilter(QObject):
    def __init__(self, tint: str, animate: bool = True, parent: QObject | None = None, glow: str | None = None):
        super().__init__(parent)
        self.tint = tint
        self.glow = glow or tint
        self.animate = animate

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Show or watched.property(_MARKER):
            return False
        if isinstance(watched, (QPushButton, QToolButton)):
            watched.setProperty(_MARKER, True)
            ButtonMotion(watched, self.tint, self.animate, self.glow)
        elif isinstance(watched, QDialog) and self.animate:
            watched.setProperty(_MARKER, True)
            fade_window_in(watched)
        return False


def fade_window_in(window: QWidget, duration: int = DIALOG_FADE_MS) -> QVariantAnimation:
    """Fade a top-level window (a dialog) in from transparent."""
    window.setWindowOpacity(0.0)
    animation = QVariantAnimation(window)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.valueChanged.connect(lambda value: window.setWindowOpacity(float(value)))
    animation.finished.connect(lambda: window.setWindowOpacity(1.0))
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def install(app, tint: str, settings: dict | None = None, glow: str | None = None) -> MotionFilter | None:
    if not bool((settings or {}).get(ANIMATIONS_SETTING, True)):
        return None
    motion_filter = MotionFilter(tint, animate=animations_enabled(settings), parent=app, glow=glow)
    app.installEventFilter(motion_filter)
    return motion_filter


def fade_in(widget: QWidget, duration: int = FADE_MS, enabled: bool = True) -> QVariantAnimation | None:
    if not enabled or widget is None or not widget.isVisible():
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    animation = QVariantAnimation(widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def finish() -> None:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    animation.valueChanged.connect(lambda value: effect.setOpacity(float(value)))
    animation.finished.connect(finish)
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


SLIDE_MS = 220
SLIDE_OFFSET = 14


def slide_fade_in(
    widget: QWidget,
    offset: int = SLIDE_OFFSET,
    duration: int = SLIDE_MS,
    enabled: bool = True,
) -> QVariantAnimation | None:
    """Fade a page in while it glides up into place (used when switching pages)."""
    if not enabled or widget is None or not widget.isVisible():
        return None
    home = widget.pos()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    widget.move(home.x(), home.y() + offset)
    animation = QVariantAnimation(widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def step(value) -> None:
        progress = float(value)
        effect.setOpacity(progress)
        widget.move(home.x(), home.y() + round(offset * (1.0 - progress)))

    def finish() -> None:
        widget.move(home)
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    animation.valueChanged.connect(step)
    animation.finished.connect(finish)
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation
