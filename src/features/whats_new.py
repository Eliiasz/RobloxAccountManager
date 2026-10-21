"""
features/whats_new.py
"What's new" after an update.

The release notes (release-notes/vX.Y.Z.md) are built into the exe, so the
popup works offline. It shows once per version, only after a real update
(never on a fresh install), and covers every version that was skipped.
"""

from __future__ import annotations

import os
import re

import features.settings_store as settings_store
from utils.app_paths import get_resource_path

LAST_SEEN_SETTING = "last_seen_version"
_NAME = re.compile(r"^v(\d+(?:\.\d+)*)\.md$")
MAX_VERSIONS = 5


def _parts(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.lstrip("v").split("."))
    except ValueError:
        return ()


def notes_folder() -> str:
    return get_resource_path("release-notes")


def available_notes(folder: str | None = None) -> dict[str, str]:
    folder = folder or notes_folder()
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    notes = {}
    for name in names:
        match = _NAME.match(name)
        if not match:
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                notes[match.group(1)] = handle.read().strip()
        except OSError:
            continue
    return notes


def notes_since(previous: str, current: str, folder: str | None = None) -> list[tuple[str, str]]:
    """Notes for versions after `previous` up to `current`, newest first."""
    old, new = _parts(previous), _parts(current)
    found = [
        (version, text) for version, text in available_notes(folder).items()
        if old < _parts(version) <= new and text
    ]
    found.sort(key=lambda item: _parts(item[0]), reverse=True)
    return found[:MAX_VERSIONS]


def pending(current: str, used_before: bool, folder: str | None = None) -> list[tuple[str, str]]:
    """What to show now (possibly nothing). Marks `current` as seen.

    used_before: the app was already in use here (e.g. accounts are saved),
    which tells an update from an older version apart from a fresh install.
    """
    settings = settings_store.load()
    last = str(settings.get(LAST_SEEN_SETTING, "") or "")
    if last and _parts(last) >= _parts(current):
        return []
    settings_store.save(LAST_SEEN_SETTING, current)
    if not last:
        # Fresh install: nothing to announce. Updated from a version before
        # this feature existed: show this version's notes.
        if not used_before:
            return []
        text = available_notes(folder).get(".".join(str(p) for p in _parts(current)), "")
        return [(current, text)] if text else []
    return notes_since(last, current, folder)
