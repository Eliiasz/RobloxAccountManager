"""
Shared filesystem paths for source runs and PyInstaller builds.
"""

from __future__ import annotations

import os
import sys


def get_project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    source_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.basename(source_root).casefold() == "src":
        return os.path.dirname(source_root)
    return source_root


def get_app_dir() -> str:
    return get_project_root()


def get_bundle_root() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", get_project_root())
    return get_project_root()


def get_resource_path(*parts: str) -> str:
    return os.path.join(get_bundle_root(), *parts)


DATA_DIR_ENV = "RAM_DATA_DIR"
DATA_DIR_OPTION = "--data-dir"


def resolve_data_dir() -> str:
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    return os.path.join(get_project_root(), "AccountManagerData")


def get_data_dir() -> str:
    return resolve_data_dir()


def apply_data_dir_argument(argv: list[str]) -> list[str]:
    remaining = []
    index = 1
    remaining.extend(argv[:1])
    while index < len(argv):
        argument = argv[index]
        if argument == DATA_DIR_OPTION:
            value = argv[index + 1].strip() if index + 1 < len(argv) else ""
            index += 2
        elif argument.startswith(DATA_DIR_OPTION + "="):
            value = argument[len(DATA_DIR_OPTION) + 1:].strip()
            index += 1
        else:
            remaining.append(argument)
            index += 1
            continue
        if value:
            os.environ[DATA_DIR_ENV] = value
        else:
            print(f"[WARNING] {DATA_DIR_OPTION} needs a folder path and was ignored.", file=sys.stderr)
    return remaining


_BUNDLE_ENV_PREFIXES = ("_PYI_", "_MEIPASS", "PYINSTALLER_")


def clean_child_environment() -> dict[str, str]:
    """Environment for starting this app (or a helper copy of it) again.

    The one-file build exports variables (_PYI_*, _MEIPASS2) pointing at this
    copy's temporary unpack folder. A second copy that inherited them would
    look for its files there and fail with a "Failed to load Python DLL"
    style error box. Drop them and anything pointing inside that folder.
    """
    bundle_dir = getattr(sys, "_MEIPASS", "") or ""
    bundle_dir = os.path.normcase(os.path.abspath(bundle_dir)) if bundle_dir else ""
    environment: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith(_BUNDLE_ENV_PREFIXES):
            continue
        if bundle_dir and bundle_dir in os.path.normcase(value):
            if upper == "PATH":
                value = os.pathsep.join(
                    part for part in value.split(os.pathsep)
                    if bundle_dir not in os.path.normcase(part)
                )
            else:
                continue
        environment[key] = value
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment
