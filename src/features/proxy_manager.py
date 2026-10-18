"""
Proxy support for the account manager's own web requests.

Every Roblox web call in this app goes through ``requests`` with the default
``trust_env=True``, so the active proxy is applied through the standard
HTTP_PROXY / HTTPS_PROXY / ALL_PROXY environment variables. ``requests`` reads
them on every new request, which means a rotation takes effect immediately for
all features without touching each call site.

Note: this covers the manager's web requests (login, auth tickets, cookie
checks, server lists, avatars). The Roblox game client's own UDP game traffic
is not carried by these variables.
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from urllib.parse import quote, unquote, urlsplit

import requests

import features.settings_store as settings_store

SETTINGS_KEY = "proxy"

MODE_GATEWAY = "gateway"  # one endpoint, the provider rotates the IP
MODE_LIST = "list"        # several proxies, this app rotates between them

SCHEMES = ("socks5h", "socks5", "socks4a", "socks4", "http", "https")
_SCHEME_ALIASES = {
    "socks": "socks5h", "socks5h": "socks5h", "socks5": "socks5",
    "socks4a": "socks4a", "socks4": "socks4", "http": "http", "https": "https",
}
_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_NO_PROXY_HOSTS = ("localhost", "127.0.0.1", "::1")
_IP_CHECK_URL = "https://api.ipify.org?format=json"

DEFAULTS: dict = {
    "enabled": False,
    "mode": MODE_GATEWAY,
    "default_scheme": "socks5h",
    "gateway": "",
    "proxies": [],
    "rotate_minutes": 5,
    "randomize": False,
}

# Environment as it was before this module touched it, restored when disabled.
_ORIGINAL_ENV = {key: os.environ.get(key) for key in (*_ENV_KEYS, "NO_PROXY", "no_proxy")}


def socks_available() -> bool:
    try:
        import socks  # noqa: F401  (PySocks, needed by requests for socks5://)
        return True
    except ImportError:
        return False


def _valid_port(value: str) -> bool:
    return value.isdigit() and 0 < int(value) < 65536


def _looks_like_host(value: str) -> bool:
    value = value.strip("[]")
    if not value or " " in value:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9.\-_:]+", value)) and ("." in value or ":" in value or value == "localhost")


def _split_host_port(value: str) -> tuple[str, str] | None:
    value = value.strip().rstrip("/")
    if value.startswith("["):  # [IPv6]:port
        host, sep, rest = value[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        return (f"[{host}]", port) if sep and _valid_port(port) else None
    host, sep, port = value.rpartition(":")
    if sep and host and _valid_port(port) and _looks_like_host(host):
        return host, port
    return None


def normalize_proxy(text: str, default_scheme: str = "socks5h") -> str:
    """Turn any common proxy notation into a URL requests understands.

    Accepted (scheme optional on all of them; socks/socks5/socks5h/socks4/
    socks4a/http/https):
        scheme://user:pass@host:port     user:pass@host:port
        host:port@user:pass               host:port:user:pass
        user:pass:host:port               host:port
        host port user pass               host:port user:pass   (spaces/tabs/commas)
        [IPv6]:port forms of the above
    Passwords may contain ":" and "@" in the host:port:user:pass form.
    Lines without a scheme use ``default_scheme``. Returns "" when unreadable.
    """
    text = (text or "").strip().strip("\ufeff").strip()
    if not text or text.startswith("#") or text.startswith("//"):
        return ""
    default_scheme = _SCHEME_ALIASES.get((default_scheme or "").lower(), "socks5h")

    scheme = default_scheme
    match = re.match(r"^([a-zA-Z0-9]+)://(.*)$", text)
    if match:
        scheme = _SCHEME_ALIASES.get(match.group(1).lower(), "")
        if not scheme:
            return ""
        text = match.group(2).strip()
    text = text.rstrip("/")

    # Whitespace / comma / semicolon / pipe separated fields.
    fields = [f for f in re.split(r"[\s,;|]+", text) if f]
    if len(fields) > 1:
        if len(fields) == 4 and _valid_port(fields[1]):            # host port user pass
            text = f"{fields[0]}:{fields[1]}:{fields[2]}:{fields[3]}"
        elif len(fields) == 2 and _valid_port(fields[1]):          # host port
            text = f"{fields[0]}:{fields[1]}"
        elif len(fields) == 2:                                    # host:port user:pass (either order)
            first, second = fields
            if _split_host_port(first):
                text = f"{second}@{first}"
            elif _split_host_port(second):
                text = f"{first}@{second}"

    user = password = None
    hostport = None

    if "@" in text:
        left, _, right = text.rpartition("@")
        if _split_host_port(right):                               # user:pass@host:port
            hostport = _split_host_port(right)
            user, _, password = left.partition(":")
        else:
            left2, _, right2 = text.partition("@")
            if _split_host_port(left2):                           # host:port@user:pass
                hostport = _split_host_port(left2)
                user, _, password = right2.partition(":")

    if hostport is None:
        parts = text.split(":")
        if text.startswith("["):
            hostport = _split_host_port(text)
        elif len(parts) == 2:
            hostport = _split_host_port(text)
        elif len(parts) >= 4 and _valid_port(parts[1]) and _looks_like_host(parts[0]):
            hostport = (parts[0], parts[1])                       # host:port:user:pass
            user, password = parts[2], ":".join(parts[3:])
        elif len(parts) == 4 and _valid_port(parts[3]) and _looks_like_host(parts[2]):
            hostport = (parts[2], parts[3])                       # user:pass:host:port
            user, password = parts[0], parts[1]

    if hostport is None:
        return ""
    host, port = hostport
    auth = ""
    if user:
        auth = quote(unquote(user), safe="") + ":" + quote(unquote(password or ""), safe="") + "@"
    return f"{scheme}://{auth}{host}:{port}"


def mask(url: str) -> str:
    """Display form of a proxy URL with the password hidden."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    user = f"{unquote(parts.username)}:***@" if parts.username else ""
    return f"{parts.scheme}://{user}{host}{port}"


def load_config() -> dict:
    stored = settings_store.get(SETTINGS_KEY, {}) or {}
    config = dict(DEFAULTS)
    if isinstance(stored, dict):
        config.update({k: v for k, v in stored.items() if k in DEFAULTS})
    if config["mode"] not in (MODE_GATEWAY, MODE_LIST):
        config["mode"] = MODE_GATEWAY
    try:
        config["rotate_minutes"] = max(1, int(config["rotate_minutes"]))
    except (TypeError, ValueError):
        config["rotate_minutes"] = DEFAULTS["rotate_minutes"]
    if not isinstance(config["proxies"], list):
        config["proxies"] = []
    return config


def save_config(config: dict) -> None:
    settings_store.save(SETTINGS_KEY, {k: config.get(k, v) for k, v in DEFAULTS.items()})


def resolved_proxies(config: dict) -> list[str]:
    scheme = config.get("default_scheme", "socks5h")
    if config.get("mode") == MODE_LIST:
        raw = config.get("proxies") or []
    else:
        raw = [config.get("gateway", "")]
    return [url for url in (normalize_proxy(line, scheme) for line in raw) if url]


def test_proxy(url: str | None, timeout: float = 12.0) -> tuple[bool, str, float]:
    """Fetch the public IP through ``url`` (or directly when None).

    Returns (ok, ip_or_error, seconds).
    """
    if url and url.startswith("socks") and not socks_available():
        return False, "SOCKS support is missing. Run: pip install \"requests[socks]\"", 0.0
    proxies = {"http": url, "https": url} if url else {"http": None, "https": None}
    started = time.monotonic()
    try:
        session = requests.Session()
        session.trust_env = False  # test exactly this proxy, not the active one
        response = session.get(_IP_CHECK_URL, proxies=proxies, timeout=timeout)
        response.raise_for_status()
        ip = response.json().get("ip", "?")
        return True, ip, time.monotonic() - started
    except requests.exceptions.ProxyError as exc:
        return False, f"Proxy refused the connection ({_short(exc)})", time.monotonic() - started
    except requests.exceptions.ConnectTimeout:
        return False, "Timed out connecting", time.monotonic() - started
    except requests.exceptions.RequestException as exc:
        return False, _short(exc), time.monotonic() - started
    except ValueError:
        return False, "Unexpected reply from IP check", time.monotonic() - started


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 160 else text[:157] + "..."


class ProxyManager:
    """Owns the active proxy, the environment variables and the rotation timer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config: dict = dict(DEFAULTS)
        self._proxies: list[str] = []
        self._index = -1
        self._current = ""
        self._last_rotation = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # public state -------------------------------------------------------
    @property
    def enabled(self) -> bool:
        with self._lock:
            return bool(self._config.get("enabled")) and bool(self._current)

    @property
    def current(self) -> str:
        with self._lock:
            return self._current if self._config.get("enabled") else ""

    def status(self) -> dict:
        with self._lock:
            rotating = (
                self._config.get("enabled")
                and self._config.get("mode") == MODE_LIST
                and len(self._proxies) > 1
            )
            next_in = None
            if rotating:
                interval = self._config["rotate_minutes"] * 60
                next_in = max(0, int(self._last_rotation + interval - time.monotonic()))
            return {
                "enabled": bool(self._config.get("enabled")) and bool(self._current),
                "current": mask(self._current),
                "count": len(self._proxies),
                "position": self._index + 1,
                "next_rotation_seconds": next_in,
                "mode": self._config.get("mode"),
            }

    # control ------------------------------------------------------------
    def apply(self, config: dict | None = None) -> None:
        """Apply a config (or the saved one) and (re)start rotation as needed."""
        if config is None:
            config = load_config()
        self._stop_thread()
        with self._lock:
            self._config = dict(config)
            self._proxies = resolved_proxies(self._config)
            if not self._config.get("enabled") or not self._proxies:
                self._index = -1
                self._set_current("")
                if self._config.get("enabled"):
                    print("[WARNING] Proxy is enabled but no valid proxy was entered; using direct connection")
                return
            if self._config.get("randomize") and len(self._proxies) > 1:
                self._index = random.randrange(len(self._proxies))
            else:
                self._index = 0
            self._set_current(self._proxies[self._index])
            if self._config.get("mode") == MODE_LIST and len(self._proxies) > 1:
                self._start_thread()

    def rotate_now(self) -> str:
        """Move to the next proxy in list mode. Returns the masked new proxy."""
        with self._lock:
            if not self._config.get("enabled") or not self._proxies:
                return ""
            if len(self._proxies) > 1:
                if self._config.get("randomize"):
                    choices = [i for i in range(len(self._proxies)) if i != self._index]
                    self._index = random.choice(choices)
                else:
                    self._index = (self._index + 1) % len(self._proxies)
            self._set_current(self._proxies[self._index])
            self._wake.set()  # restart the countdown
            return mask(self._current)

    def shutdown(self) -> None:
        self._stop_thread()

    # internals ----------------------------------------------------------
    def _set_current(self, url: str) -> None:
        previous = self._current
        self._current = url
        self._last_rotation = time.monotonic()
        if url:
            for key in _ENV_KEYS:
                os.environ[key] = url
            no_proxy = ",".join(
                filter(None, [_ORIGINAL_ENV.get("NO_PROXY") or _ORIGINAL_ENV.get("no_proxy"), *_NO_PROXY_HOSTS])
            )
            os.environ["NO_PROXY"] = os.environ["no_proxy"] = no_proxy
        else:
            for key, value in _ORIGINAL_ENV.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if url != previous:
            if url:
                print(f"[INFO] Proxy active: {mask(url)}")
            elif previous:
                print("[INFO] Proxy off, using direct connection")

    def _start_thread(self) -> None:
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._rotation_loop, daemon=True, name="ProxyRotation")
        self._thread.start()

    def _stop_thread(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        self._wake.set()
        thread.join(timeout=2)
        self._thread = None

    def _rotation_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                interval = self._config.get("rotate_minutes", 5) * 60
                wait = max(1.0, self._last_rotation + interval - time.monotonic())
            woke = self._wake.wait(wait)
            if self._stop.is_set():
                return
            if woke:
                self._wake.clear()
                continue  # manual rotation or reconfigure: recompute the wait
            self.rotate_now()
            self._wake.clear()


manager = ProxyManager()
