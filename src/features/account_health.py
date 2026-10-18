"""
Checks saved accounts for bans, terminations and locked / signed-out cookies.

For each account:
  1. users/authenticated with the saved cookie.
     - 401/403 twice: the cookie no longer works. Roblox does this when an
       account is security-locked, its password was reset, or it was signed out
       everywhere, so it is reported as LOCKED ("Locked / signed out").
  2. With a working cookie, usermoderation not-approved: a punishment in the
     reply means the account is under a moderation action (BANNED).
  3. Public users/{id}: isBanned means the account is TERMINATED. This works
     even when the cookie is dead.

Anything that cannot be decided (network errors, rate limits) is UNKNOWN and
is never selected for deletion by default.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

OK = "ok"
BANNED = "banned"
TERMINATED = "terminated"
LOCKED = "locked"
UNKNOWN = "unknown"

LABELS = {
    OK: "OK",
    BANNED: "Banned",
    TERMINATED: "Terminated",
    LOCKED: "Locked / signed out",
    UNKNOWN: "Unknown",
}
PROBLEM_STATUSES = (BANNED, TERMINATED, LOCKED)

_AUTH_URL = "https://users.roblox.com/v1/users/authenticated"
_MODERATION_URL = "https://usermoderation.roblox.com/v1/not-approved"
_USER_URL = "https://users.roblox.com/v1/users/{user_id}"
_ATTEMPTS = 2
_RETRY_DELAY = 0.75
DEFAULT_DELAY = 1.5


@dataclass
class HealthResult:
    username: str
    status: str
    detail: str = ""
    user_id: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LABELS.get(self.status, self.status)


class _RateLimited(Exception):
    pass


def _get(session: requests.Session, url: str, cookie: str | None = None) -> requests.Response:
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = f".ROBLOSECURITY={cookie}"
    response = session.get(url, headers=headers, timeout=10)
    if response.status_code == 429:
        raise _RateLimited()
    return response


def _authenticated(session, cookie) -> tuple[str, int, str]:
    """Returns ("ok"|"dead"|"unknown", user_id, detail)."""
    failures = 0
    detail = "No response from Roblox."
    for attempt in range(_ATTEMPTS):
        try:
            response = _get(session, _AUTH_URL, cookie)
            if response.status_code == 200:
                try:
                    return "ok", int(response.json().get("id") or 0), ""
                except (ValueError, TypeError):
                    return "ok", 0, ""
            if response.status_code in (401, 403):
                failures += 1
                detail = f"Cookie rejected (HTTP {response.status_code})."
            else:
                detail = f"Roblox returned HTTP {response.status_code}."
        except _RateLimited:
            raise
        except requests.RequestException as exc:
            detail = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < _ATTEMPTS:
            time.sleep(_RETRY_DELAY)
    return ("dead" if failures == _ATTEMPTS else "unknown"), 0, detail


def _moderation(session, cookie) -> dict | None:
    """Punishment info for a working cookie, {} when none, None when unknown."""
    try:
        response = _get(session, _MODERATION_URL, cookie)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return {}
    if data.get("punishmentTypeDescription") or data.get("punishmentId"):
        return data
    return {}


def _is_terminated(session, user_id: int) -> bool | None:
    if not user_id:
        return None
    try:
        response = _get(session, _USER_URL.format(user_id=int(user_id)))
    except requests.RequestException:
        return None
    if response.status_code == 404:
        return True  # deleted accounts no longer resolve
    if response.status_code != 200:
        return None
    try:
        return bool(response.json().get("isBanned"))
    except (ValueError, AttributeError):
        return None


def _describe_punishment(data: dict) -> str:
    kind = str(data.get("punishmentTypeDescription") or "Moderation action").strip()
    end = str(data.get("endDate") or "").strip()
    if end:
        return f"{kind} (until {end[:16].replace('T', ' ')} UTC)"
    return kind


def check_account(username: str, data: dict, session: requests.Session) -> HealthResult:
    cookie = (data or {}).get("cookie", "")
    stored_id = 0
    try:
        stored_id = int((data or {}).get("user_id") or 0)
    except (TypeError, ValueError):
        pass

    if not cookie:
        terminated = _is_terminated(session, stored_id)
        if terminated:
            return HealthResult(username, TERMINATED, "Account is terminated.", stored_id)
        return HealthResult(username, UNKNOWN, "No saved cookie.", stored_id)

    auth, user_id, detail = _authenticated(session, cookie)
    user_id = user_id or stored_id

    if auth == "ok":
        punishment = _moderation(session, cookie)
        if punishment:
            return HealthResult(username, BANNED, _describe_punishment(punishment), user_id, punishment)
        if _is_terminated(session, user_id):
            return HealthResult(username, TERMINATED, "Account is terminated.", user_id)
        if punishment is None:
            return HealthResult(username, OK, "Signed in (ban status could not be read).", user_id)
        return HealthResult(username, OK, "Signed in, no moderation.", user_id)

    if auth == "dead":
        if _is_terminated(session, user_id):
            return HealthResult(username, TERMINATED, "Account is terminated.", user_id)
        return HealthResult(
            username, LOCKED,
            "Cookie no longer works (account locked, password changed, or signed out).",
            user_id,
        )

    return HealthResult(username, UNKNOWN, detail, user_id)


class AccountHealthChecker:
    def __init__(
        self,
        manager,
        on_result: Callable[[HealthResult, int, int], None],
        on_done: Callable[[dict], None],
        delay_sec: float = DEFAULT_DELAY,
        usernames: list[str] | None = None,
    ):
        self._manager = manager
        self._on_result = on_result
        self._on_done = on_done
        self._delay = max(0.5, float(delay_sec))
        self._usernames = usernames
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="AccountHealth")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _snapshot(self) -> list[tuple[str, dict]]:
        lock = getattr(self._manager, "_accounts_lock", None)
        if lock is None:
            items = list(self._manager.accounts.items())
        else:
            with lock:
                items = [(k, dict(v) if isinstance(v, dict) else v) for k, v in self._manager.accounts.items()]
        if self._usernames is not None:
            wanted = set(self._usernames)
            items = [(k, v) for k, v in items if k in wanted]
        return [(k, v) for k, v in items if isinstance(v, dict)]

    def _run(self) -> None:
        accounts = self._snapshot()
        total = len(accounts)
        counts = {key: 0 for key in LABELS}
        stopped_reason = ""
        session = requests.Session()
        try:
            for index, (username, data) in enumerate(accounts, start=1):
                if self._stop.is_set():
                    stopped_reason = "Stopped."
                    break
                try:
                    result = check_account(username, data, session)
                except _RateLimited:
                    result = HealthResult(username, UNKNOWN, "Rate limited by Roblox (HTTP 429).")
                    stopped_reason = "Roblox rate limited the scan. Wait a minute and scan again."
                    self._stop.set()
                except Exception as exc:  # never let one account kill the scan
                    result = HealthResult(username, UNKNOWN, f"{type(exc).__name__}: {exc}")
                counts[result.status] = counts.get(result.status, 0) + 1
                if result.status in PROBLEM_STATUSES:
                    print(f"[WARNING] Ban check: {username} is {result.label}. {result.detail}")
                try:
                    self._on_result(result, index, total)
                except Exception as exc:
                    print(f"[WARNING] account_health on_result error: {exc}")
                if self._stop.is_set():
                    break
                self._stop.wait(timeout=self._delay)
        finally:
            session.close()
        summary = {"total": total, "counts": counts, "stopped": stopped_reason}
        print(
            "[INFO] Ban check complete: "
            + ", ".join(f"{LABELS[k]} {v}" for k, v in counts.items() if v)
        )
        try:
            self._on_done(summary)
        except Exception:
            pass
