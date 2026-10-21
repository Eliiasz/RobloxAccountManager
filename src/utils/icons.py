"""
Small stroke icons drawn from inline SVG, colored from the active theme.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_SIZE = 16

PATHS: dict[str, str] = {
    "accounts": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20 4v5h-5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "windows": (
        '<rect x="4" y="8" width="12" height="12" rx="2"/>'
        '<path d="M8 8V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2"/>'
    ),
    "sliders": (
        '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'
    ),
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
    "heart": '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    "pulse": (
        '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>'
        '<path d="M7 12h3l1.5-2.5 2 4.5 1.5-2h2"/>'
    ),
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    "pencil": '<path d="M4 20l4-1 11-11-3-3L5 16z"/><path d="M14 6l3 3"/>',
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    "power": '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    "enter": '<path d="M5 12h12M13 6l6 6-6 6"/>',
    "resize": '<path d="M8 20L20 8M14 20l6-6M20 20h.01"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M16 16l5 5"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 10h8M8 13h5"/>',
    "ban": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    "bolt": '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
    "keyboard": (
        '<rect x="3" y="6" width="18" height="12" rx="2"/>'
        '<path d="M7 10h.01M11 10h.01M15 10h.01M17 10h.01M7 14h10"/>'
    ),
    "gauge": '<path d="M4.5 17a8.5 8.5 0 1 1 15 0"/><path d="M12 13l4-4"/><circle cx="12" cy="13" r="1"/>',
    "dots": '<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M4 7l8 6 8-6"/>',
    "shield_check": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "chevron_right": '<path d="M10 6l6 6-6 6"/>',
    "minimize": '<rect x="6" y="11" width="12" height="2" rx="1"/>',
    "maximize": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "restore": '<rect x="5" y="8" width="11" height="11" rx="1.5"/><path d="M8 8V5h11v11h-3"/>',
    "close": '<path d="M7 7l10 10M17 7L7 17"/>',
    "gamepad": (
        '<path d="M7 8h10a4 4 0 0 1 4 4v1a3 3 0 0 1-5.4 1.8L14.5 14h-5l-1.1.8A3 3 0 0 1 3 13v-1a4 4 0 0 1 4-4z"/>'
        '<path d="M8 10.5v3M6.5 12h3M15.5 11h.01M17 13h.01"/>'
    ),
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "unlock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
    "user_join": (
        '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.6 2.7-6 6-6 1.6 0 3 .5 4 1.4"/>'
        '<path d="M15 17h6M18.5 14.5L21 17l-2.5 2.5"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>'
        '<path d="M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9s1.3-6.4 3.8-9z"/>'
    ),
}

_cache: dict[tuple, QPixmap] = {}


def _svg(name: str, color: str) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{PATHS[name]}</svg>"
    ).encode("utf-8")


def pixmap(name: str, color: str, size: int = ICON_SIZE) -> QPixmap:
    if name not in PATHS:
        raise KeyError(f"Unknown icon: {name}")
    screen = QGuiApplication.primaryScreen()
    ratio = max(1.0, screen.devicePixelRatio()) if screen else 1.0
    key = (name, color.upper(), size, ratio)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    pixels = round(size * ratio)
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(_svg(name, color))).render(painter)
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(ratio)
    _cache[key] = result
    return result


def make_icon(
    name: str,
    normal: str,
    active: str | None = None,
    checked: str | None = None,
    disabled: str | None = None,
    size: int = ICON_SIZE,
) -> QIcon:
    icon = QIcon()
    icon.addPixmap(pixmap(name, normal, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(pixmap(name, active or normal, size), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(pixmap(name, checked or active or normal, size), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(pixmap(name, checked or active or normal, size), QIcon.Mode.Active, QIcon.State.On)
    icon.addPixmap(pixmap(name, disabled or normal, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def set_accent_icon(button, name: str, colors: dict[str, str], size: int = ICON_SIZE) -> None:
    """Icon drawn in the accent color in every state (the orange icons of the redesign)."""
    button.setIcon(make_icon(
        name,
        normal=colors["ACCENT_BRIGHT"],
        active=colors["ACCENT_HOVER"],
        disabled=colors["LINE"],
        size=size,
    ))
    button.setIconSize(QSize(size, size))


def set_button_icon(button, name: str, colors: dict[str, str], size: int = ICON_SIZE) -> None:
    button.setIcon(make_icon(
        name,
        normal=colors["MUTED"],
        active=colors["TEXT"],
        checked=colors["ACCENT_TEXT"],
        disabled=colors["LINE"],
        size=size,
    ))
    button.setIconSize(QSize(size, size))
