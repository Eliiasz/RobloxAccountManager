"""
Roblox Account Manager
Main entry point for the application.
"""

# if you find this tool helpful, consider starring the repo!

import ctypes
import os
import sys

# A windowed frozen helper cannot use stdout. Dispatch before importing Qt or
# installing diagnostics, so probing never starts another application instance.
if len(sys.argv) > 1 and sys.argv[1] == "--window-log-probe":
    from features.window_log_probe import helper_main

    raise SystemExit(helper_main(sys.argv[2:]))

from utils.app_paths import apply_data_dir_argument

sys.argv[:] = apply_data_dir_argument(sys.argv)
SMOKE_TEST = "--smoke-test" in sys.argv[1:]
if SMOKE_TEST:
    sys.argv.remove("--smoke-test")

from features import diagnostics
import features.settings_store as settings_store
from utils.app_paths import get_data_dir, get_resource_path
from utils.version import APP_VERSION

_FRESH_INSTALL = not settings_store.load()

diagnostics.install(APP_VERSION)

# 2.9.0 redesign: move everyone to the ANAne Orange theme and the new default
# window size once. Later theme choices in Settings are kept.
try:
    if not settings_store.get("anane_theme_applied"):
        settings_store.save("color_theme", "default")
        settings_store.save("accent_color", "")
        settings_store.remove("main_window_size")
        settings_store.save("anane_theme_applied", True)
except Exception as _exc:
    print(f"[WARNING] Could not apply the new theme: {_exc}")

# 2.11.0, once: new users start on Mono Glass (existing users keep the theme
# they have), cookies are only checked from Account Health, and 10 log files
# are kept. Later changes in Settings are kept.
try:
    if not settings_store.get("v2_11_defaults_applied"):
        from features import color_themes

        if _FRESH_INSTALL:
            settings_store.save(color_themes.THEME_SETTING, color_themes.DEFAULT_THEME)
        settings_store.save("validate_cookies_on_startup", False)
        settings_store.save("log_retention_count", diagnostics.DEFAULT_LOG_RETENTION)
        settings_store.save("v2_11_defaults_applied", True)
except Exception as _exc:
    print(f"[WARNING] Could not apply the 2.11.0 defaults: {_exc}")

from utils import splash

splash.show(
    APP_VERSION,
    [os.path.join(get_data_dir(), "icon.ico"), get_resource_path("assets", "icon.ico")],
)

from utils.ui import main as _ui_main

DATA_FOLDER = get_data_dir()

def _ensure_data_folder():
    os.makedirs(DATA_FOLDER, exist_ok=True)


def resolve_icon_path() -> str | None:
    icon_path = os.path.join(DATA_FOLDER, "icon.ico")
    if os.path.exists(icon_path):
        return icon_path
    root_icon = get_resource_path("assets", "icon.ico")
    if os.path.exists(root_icon):
        return root_icon
    return None


def _set_app_user_model_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "anane.robloxaccountmanager.ram"
        )
    except Exception:
        pass


def main():
    diagnostics.set_startup_stage("main startup")
    try:
        settings_store.remove("discord_webhook")
    except Exception as exc:
        print(f"[WARNING] Could not clear old webhook settings: {exc}")
    _set_app_user_model_id()
    diagnostics.set_startup_stage("console capture installed")
    _ensure_data_folder()
    diagnostics.set_startup_stage("data folder ready")
    from features import temp_cleanup

    temp_cleanup.start_in_background()
    try:
        from features.proxy_manager import manager as proxy_manager

        proxy_manager.apply()
    except Exception as exc:
        print(f"[WARNING] Could not apply proxy settings: {exc}")

    icon_path = resolve_icon_path()

    try:
        exit_code = _ui_main(icon_path=icon_path, smoke_test=SMOKE_TEST)
    except Exception as exc:
        splash.dismiss()
        crash_path = diagnostics.report_exception(
            "Application startup or UI runtime",
            exc,
            fatal=True,
        )
        diagnostics.show_native_error(
            "ANAne RAM Could Not Start",
            "The application encountered an unexpected error.\n\n"
            f"Crash report:\n{crash_path or diagnostics.get_session_log_path()}",
        )
        exit_code = 1

    diagnostics.shutdown(exit_code)
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())
