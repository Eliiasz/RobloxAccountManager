"""
features/auto_backup.py
Automatic account backups.

Backups use the same password-protected .rambak format as Export Accounts,
so they can be restored with Import Accounts too. With a backup password set
by the user they open on any PC; otherwise a random key kept in the app's
own (hardware/password encrypted) secure settings is used, so they open on
this PC only. The newest few are kept in <data folder>/backups and,
optionally, copied to an extra folder of the user's choice.
"""

from __future__ import annotations

from datetime import datetime
import os
import re
import secrets
import shutil

import features.account_backup as account_backup
import features.settings_store as settings_store
from classes.operation_result import OperationResult
from utils.app_paths import get_data_dir

ENABLED_SETTING = "auto_backup_enabled"
EXTRA_FOLDER_SETTING = "auto_backup_extra_folder"
LAST_BACKUP_SETTING = "auto_backup_last"
PASSWORD_KEY = "backup_password"      # secure setting: the user's own backup password
AUTO_KEY = "auto_backup_key"          # secure setting: random key for this-PC-only backups
KEEP = 10
CHANGE_INTERVAL_SECONDS = 3600
_NAME = re.compile(r"^accounts-(\d{8}-\d{6})-(\d+)\.rambak$")


def backup_folder() -> str:
    return os.path.join(get_data_dir(), "backups")


def is_enabled() -> bool:
    return bool(settings_store.get(ENABLED_SETTING, True))


def has_user_password(manager) -> bool:
    return bool(manager.get_secure_setting(PASSWORD_KEY, ""))


def _password(manager) -> str:
    user_password = str(manager.get_secure_setting(PASSWORD_KEY, "") or "")
    if user_password:
        return user_password
    key = str(manager.get_secure_setting(AUTO_KEY, "") or "")
    if not key:
        key = secrets.token_urlsafe(24)
        saved = manager.set_secure_setting(AUTO_KEY, key)
        if saved is not None and not saved:
            raise RuntimeError(getattr(saved, "message", "The backup key could not be saved."))
    return key


def candidate_passwords(manager) -> list[str]:
    """Passwords to try, newest first, when restoring a backup made on this PC."""
    found = []
    for key in (PASSWORD_KEY, AUTO_KEY):
        value = str(manager.get_secure_setting(key, "") or "")
        if value and value not in found:
            found.append(value)
    return found


def _prune(folder: str, keep: int = KEEP) -> None:
    backups = sorted(name for name in os.listdir(folder) if _NAME.match(name))
    for name in backups[:-keep] if len(backups) > keep else []:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def make_backup(manager, reason: str = "manual") -> OperationResult:
    lock = getattr(manager, "_accounts_lock", None)
    if lock is not None:
        with lock:
            accounts = dict(manager.accounts)
    else:
        accounts = dict(manager.accounts)
    if not accounts:
        return OperationResult.failure("BACKUP_NO_ACCOUNTS", "Nothing To Back Up", "There are no saved accounts yet.")
    try:
        password = _password(manager)
    except Exception as exc:
        return OperationResult.failure("BACKUP_KEY_FAILED", "Backup Failed", str(exc))
    folder = backup_folder()
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"accounts-{stamp}-{len(accounts)}.rambak"
    path = os.path.join(folder, name)
    result = account_backup.export_accounts(accounts, path, password)
    if not result:
        return result
    _prune(folder)
    extra = str(settings_store.get(EXTRA_FOLDER_SETTING, "") or "")
    copied = ""
    if extra:
        try:
            os.makedirs(extra, exist_ok=True)
            shutil.copy2(path, os.path.join(extra, name))
            _prune(extra)
            copied = extra
        except OSError as exc:
            print(f"[WARNING] Auto backup: could not copy to {extra}: {exc}")
    settings_store.save(LAST_BACKUP_SETTING, datetime.now().timestamp())
    print(f"[INFO] Accounts backup saved ({reason}): {name}" + (f", copy in {copied}" if copied else ""))
    return OperationResult.success(f"Backed up {len(accounts)} account(s).", data={"path": path, "copy": copied})


def backup_due(fingerprint: str, last_fingerprint: str, now: float | None = None) -> bool:
    """A change-triggered backup: only when accounts changed and the last one is an hour old."""
    if not fingerprint or fingerprint == last_fingerprint:
        return False
    now = now if now is not None else datetime.now().timestamp()
    try:
        last = float(settings_store.get(LAST_BACKUP_SETTING, 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    return now - last >= CHANGE_INTERVAL_SECONDS


def list_backups(folder: str | None = None) -> list[dict]:
    folder = folder or backup_folder()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        match = _NAME.match(name)
        if not match:
            continue
        try:
            when = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        found.append({"path": os.path.join(folder, name), "when": when, "count": int(match.group(2))})
    return sorted(found, key=lambda item: item["when"], reverse=True)
