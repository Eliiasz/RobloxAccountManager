"""Render screenshots of the main window and its settings windows.

Used by the "UI Preview" workflow so changes to the interface can be checked
without running the app by hand. Nothing touches the network or real data:
a throwaway data folder and sample accounts are used.

    uv run python scripts/ui_preview.py out_folder
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ["RAM_DATA_DIR"] = tempfile.mkdtemp(prefix="ram-preview-")
for folder in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    sys.path.insert(0, str(folder))

from PySide6.QtWidgets import QApplication  # noqa: E402

from features import settings_store  # noqa: E402

SAMPLE_ACCOUNTS = {
    "P4kz002": {}, "P4kz003": {"locked": True}, "P4kz004": {}, "P4kz005": {}, "P4kz006": {},
    "CBT0777": {}, "markydancel2006": {"note": "GRADE MAKER"}, "ejcristobal25": {},
    "Johnfreecss_28": {"note": "TRAIT MAKER"}, "P4kz0010": {},
}


def wait(app: QApplication, ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main(out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    settings_store.save("last_place_id", "113290951185459")
    settings_store.save("anane_theme_applied", True)
    settings_store.save("roblox_window_mode", "fit")  # show the Window Size drawing

    app = QApplication.instance() or QApplication(sys.argv[:1])
    from utils import ui
    ui.actions.save_recent_game("113290951185459", "Anime Dice")

    manager = MagicMock()
    manager.accounts = {name: dict(data) for name, data in SAMPLE_ACCOUNTS.items()}
    manager.get_encryption_method.return_value = "hardware"
    manager.get_secure_setting.return_value = ""
    manager.secure_settings_unavailable = False
    manager.encryption_config.is_encryption_enabled.return_value = True
    manager.encryption_config.get_encryption_method.return_value = "hardware"
    with patch.object(ui.threading.Thread, "start"), \
            patch.object(ui.AccountManagerUIQt, "_setup_system_tray"), \
            patch.object(ui.EncryptionConfig, "is_setup_complete", return_value=True), \
            patch.object(ui.actions, "fetch_game_name_async",
                         side_effect=lambda _pid, done: done("Anime Dice")):
        window = ui.AccountManagerUIQt(manager)
        window.resize(1240, 820)
        window.show()
        wait(app, 900)
        window._pc_usage_timer.stop()
        window._pc_usage_reader = None
        window._pc_usage = (11.2 * 1024, 16 * 1024, 23.0)
        window._on_running_count(4, from_poll=False)
        window._account_list.setCurrentRow(2)
        wait(app, 300)
        window.grab().save(str(out / "main.png"))

        window._show_page(4)
        wait(app, 600)
        window.grab().save(str(out / "settings.png"))
        from PySide6.QtWidgets import QPushButton
        for button in window.findChildren(QPushButton):
            if button.objectName() == "navTab" and button.text() in ("Roblox", "Themes", "Misc"):
                button.click()
                wait(app, 500)
                window.grab().save(str(out / f"settings_{button.text().lower()}.png"))
        for index, name in ((1, "auto_rejoin"), (2, "anti_afk"), (3, "multi_roblox"), (8, "ban_check"),
                            (5, "console"), (6, "contact")):
            window._show_page(index)
            wait(app, 500)
            window.grab().save(str(out / f"page_{name}.png"))
        window._show_page(0)
        wait(app, 400)

        for kind in ("hotkey", "performance"):
            window._open_settings_sheet(kind)
            wait(app, 600)
            sheet = window._settings_sheets[kind]
            sheet.grab().save(str(out / f"{kind}.png"))
            from PySide6.QtWidgets import QScrollArea
            scroll = sheet.findChild(QScrollArea)
            if scroll is not None and scroll.verticalScrollBar().maximum() > 0:
                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                wait(app, 200)
                sheet.grab().save(str(out / f"{kind}_bottom.png"))
            sheet.hide()

        window.resize(ui.MIN_WINDOW_SIZE)
        wait(app, 500)
        window.grab().save(str(out / "main_min.png"))
        window.close()
    print(f"Saved previews to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "ui-preview"))
