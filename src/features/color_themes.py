"""
features/color_themes.py
Colour presets for the application.

A new theme is one more entry in PRESETS: the Settings gallery lists every
entry, in order, with a preview built from its colours.
"""

from __future__ import annotations

import re

DEFAULT_THEME = "mono_glass"
THEME_SETTING = "color_theme"

BASE_KEYS = ("BG", "PANEL", "INPUT", "TEXT", "MUTED", "LINE", "SELECT", "NOTE", "ACCENT")
DERIVED_KEYS = (
    "HOVER", "PRESSED", "RAISED",
    "ACCENT_HOVER", "ACCENT_PRESSED", "ACCENT_BRIGHT", "ACCENT_SOFT",
    "ACCENT_TEXT", "ACCENT_DEEP", "ON_ACCENT",
)
KEYS = BASE_KEYS + DERIVED_KEYS

_HEX = re.compile(r"#[0-9a-fA-F]{6}")

PRESETS: dict[str, dict] = {
    "mono_glass": {
        "label": "Mono Glass",
        "BG": "#050506", "PANEL": "#0C0C0E", "INPUT": "#141417",
        "TEXT": "#F4F4F5", "MUTED": "#9C9CA3", "LINE": "#27272C",
        "SELECT": "#1E1E23", "NOTE": "#D4D4D8", "ACCENT": "#E4E6EB",
        "explicit": {
            "HOVER": "#2A2A30", "PRESSED": "#0E0E11", "RAISED": "#1C1C21",
            "ACCENT_HOVER": "#F4F5F7", "ACCENT_PRESSED": "#C4C7CE",
            "ACCENT_BRIGHT": "#FFFFFF", "ACCENT_SOFT": "#2E2F34",
            "ACCENT_TEXT": "#E9EAEE", "ACCENT_DEEP": "#121215",
        },
    },
    "violet_glow": {
        "label": "Violet Glow",
        "BG": "#07060F", "PANEL": "#0E0C1C", "INPUT": "#151229",
        "TEXT": "#EEEAFB", "MUTED": "#A39DC0", "LINE": "#2A2346",
        "SELECT": "#221C40", "NOTE": "#C9A7FF", "ACCENT": "#8B5CF6",
        "explicit": {
            "HOVER": "#2B2450", "PRESSED": "#0F0C1E", "RAISED": "#1D1838",
            "ACCENT_HOVER": "#A07BFA", "ACCENT_PRESSED": "#7340E8",
            "ACCENT_BRIGHT": "#9B6BFF", "ACCENT_SOFT": "#2F2160",
            "ACCENT_TEXT": "#B79BFF", "ACCENT_DEEP": "#140F2A",
        },
    },
    "sky_paper": {
        "label": "Sky Paper",
        "light": True,
        "BG": "#EEF5FC", "PANEL": "#FFFFFF", "INPUT": "#F5F9FE",
        "TEXT": "#10233A", "MUTED": "#526277", "LINE": "#D3E1F0",
        "SELECT": "#DDEBFA", "NOTE": "#8A5200", "ACCENT": "#1E88E5",
        "explicit": {
            "HOVER": "#E3EEF9", "PRESSED": "#D5E4F4", "RAISED": "#FFFFFF",
            "ACCENT_HOVER": "#3B9BEC", "ACCENT_PRESSED": "#1670C2",
            "ACCENT_BRIGHT": "#2B92EA", "ACCENT_SOFT": "#CFE5FA",
            "ACCENT_TEXT": "#1565C0", "ACCENT_DEEP": "#E6F1FC",
        },
    },
    "default": {
        "label": "ANAne Orange",
        "BG": "#0A0D13", "PANEL": "#10141C", "INPUT": "#151A24",
        "TEXT": "#ECEFF4", "MUTED": "#98A2B3", "LINE": "#232A37",
        "SELECT": "#1B2130", "NOTE": "#F5A524", "ACCENT": "#FF7A1A",
        "explicit": {
            "HOVER": "#262E3D", "PRESSED": "#11151D", "RAISED": "#1D2433",
            "ACCENT_HOVER": "#FF8D3B", "ACCENT_PRESSED": "#E2620B",
            "ACCENT_BRIGHT": "#FF8A2A", "ACCENT_SOFT": "#3A2312",
            "ACCENT_TEXT": "#FF9A4A", "ACCENT_DEEP": "#1E140D",
        },
    },
    "classic": {
        "label": "Classic Blue",
        "BG": "#0E0E0E", "PANEL": "#151515", "INPUT": "#1A1A1A",
        "TEXT": "#EDEDED", "MUTED": "#AAAAAA", "LINE": "#242424",
        "SELECT": "#2A2A2A", "NOTE": "#D6BB7D", "ACCENT": "#0078D7",
        "explicit": {
            "HOVER": "#3A3A3A", "PRESSED": "#1E1E1E", "RAISED": "#2E2E2E",
            "ACCENT_HOVER": "#1A8FE0", "ACCENT_PRESSED": "#006DC4",
            "ACCENT_BRIGHT": "#3A7BD5", "ACCENT_SOFT": "#3A5A9A",
            "ACCENT_TEXT": "#5DBBFF", "ACCENT_DEEP": "#0A1A2A",
        },
    },
    "midnight": {
        "label": "Midnight",
        "BG": "#0B0F1A", "PANEL": "#111827", "INPUT": "#172033",
        "TEXT": "#E6EDF7", "MUTED": "#9AA7BD", "LINE": "#223049",
        "SELECT": "#1F2C47", "NOTE": "#E3C98B", "ACCENT": "#4C8DFF",
    },
    "amoled": {
        "label": "AMOLED Black",
        "BG": "#000000", "PANEL": "#080808", "INPUT": "#101010",
        "TEXT": "#F2F2F2", "MUTED": "#A0A0A0", "LINE": "#1E1E1E",
        "SELECT": "#1A1A1A", "NOTE": "#D6BB7D", "ACCENT": "#00A3FF",
    },
    "nord": {
        "label": "Nord",
        "BG": "#2E3440", "PANEL": "#343B49", "INPUT": "#3B4252",
        "TEXT": "#ECEFF4", "MUTED": "#B4BED0", "LINE": "#4C566A",
        "SELECT": "#434C5E", "NOTE": "#EBCB8B", "ACCENT": "#88C0D0",
    },
    "dracula": {
        "label": "Dracula",
        "BG": "#1E1F29", "PANEL": "#282A36", "INPUT": "#2F3142",
        "TEXT": "#F8F8F2", "MUTED": "#B4B8CC", "LINE": "#3B3E52",
        "SELECT": "#44475A", "NOTE": "#F1FA8C", "ACCENT": "#BD93F9",
    },
    "solarized": {
        "label": "Solarized Dark",
        "BG": "#002B36", "PANEL": "#073642", "INPUT": "#0B3E4B",
        "TEXT": "#EEE8D5", "MUTED": "#9CAAAA", "LINE": "#14515F",
        "SELECT": "#18505E", "NOTE": "#D9A520", "ACCENT": "#268BD2",
    },
    "forest": {
        "label": "Forest",
        "BG": "#0D1411", "PANEL": "#131D18", "INPUT": "#19261F",
        "TEXT": "#E4EFE8", "MUTED": "#9DB3A6", "LINE": "#223229",
        "SELECT": "#263A2F", "NOTE": "#D8C48A", "ACCENT": "#3FB68B",
    },
    "rose": {
        "label": "Rose",
        "BG": "#160F13", "PANEL": "#1E151A", "INPUT": "#271B22",
        "TEXT": "#F3E8EE", "MUTED": "#B79AA8", "LINE": "#36242E",
        "SELECT": "#3A2733", "NOTE": "#E6C98F", "ACCENT": "#E5557F",
    },
}


def is_hex_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX.fullmatch(value))


def _to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _to_hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c))) for c in rgb))


def mix(first: str, second: str, amount: float) -> str:
    a, b = _to_rgb(first), _to_rgb(second)
    return _to_hex(tuple(x + (y - x) * amount for x, y in zip(a, b)))


def luminance(color: str) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _to_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _readable_on(color: str, background: str, minimum: float = 4.5) -> str:
    amount = 0.45
    candidate = mix(color, "#FFFFFF", amount)
    while contrast_ratio(candidate, background) < minimum and amount < 1:
        amount = min(1.0, amount + 0.05)
        candidate = mix(color, "#FFFFFF", amount)
    return candidate


def accent_variants(accent: str, background: str) -> dict[str, str]:
    return {
        "ACCENT_HOVER": mix(accent, "#FFFFFF", 0.15),
        "ACCENT_PRESSED": mix(accent, "#000000", 0.15),
        "ACCENT_BRIGHT": mix(accent, "#FFFFFF", 0.10),
        "ACCENT_SOFT": mix(background, accent, 0.55),
        "ACCENT_TEXT": _readable_on(accent, background),
        "ACCENT_DEEP": mix(background, accent, 0.14),
        "ON_ACCENT": "#FFFFFF" if contrast_ratio(accent, "#FFFFFF") >= contrast_ratio(accent, "#101010") else "#101010",
    }


def neutral_variants(colors: dict) -> dict[str, str]:
    return {
        "HOVER": mix(colors["INPUT"], colors["TEXT"], 0.14),
        "PRESSED": mix(colors["INPUT"], colors["BG"], 0.55),
        "RAISED": mix(colors["INPUT"], colors["TEXT"], 0.07),
    }


def theme_choices() -> list[tuple[str, str]]:
    return [(key, preset["label"]) for key, preset in PRESETS.items()]


def theme_name(settings: dict | None = None) -> str:
    settings = settings if isinstance(settings, dict) else {}
    name = settings.get(THEME_SETTING)
    return name if isinstance(name, str) and name in PRESETS else DEFAULT_THEME


def is_light(name: str) -> bool:
    return bool(PRESETS.get(name, {}).get("light"))


def resolve_colors(settings: dict | None = None) -> dict[str, str]:
    preset = PRESETS[theme_name(settings)]
    colors = {key: preset[key] for key in BASE_KEYS}
    colors.update(neutral_variants(colors))
    colors.update(accent_variants(colors["ACCENT"], colors["BG"]))
    colors.update(preset.get("explicit") or {})
    return colors
