import unittest

from features import account_health as health
from features import proxy_manager as proxy


class ProxyFormatTests(unittest.TestCase):
    CASES = [
        ("1.2.3.4:1080", "socks5h://1.2.3.4:1080"),
        ("1.2.3.4:1080:user:pass", "socks5h://user:pass@1.2.3.4:1080"),
        ("1.2.3.4:1080:user:p@ss:w0rd", "socks5h://user:p%40ss%3Aw0rd@1.2.3.4:1080"),
        ("user:pass@1.2.3.4:1080", "socks5h://user:pass@1.2.3.4:1080"),
        ("1.2.3.4:1080@user:pass", "socks5h://user:pass@1.2.3.4:1080"),
        ("user:pass:1.2.3.4:1080", "socks5h://user:pass@1.2.3.4:1080"),
        ("socks5://user:pass@gate.example.com:7000", "socks5://user:pass@gate.example.com:7000"),
        ("socks://gate.example.com:7000", "socks5h://gate.example.com:7000"),
        ("socks4://1.2.3.4:4145", "socks4://1.2.3.4:4145"),
        ("http://user:pass@proxy.example.net:8080/", "http://user:pass@proxy.example.net:8080"),
        ("HTTPS://proxy.example.net:443", "https://proxy.example.net:443"),
        ("1.2.3.4 1080 user pass", "socks5h://user:pass@1.2.3.4:1080"),
        ("1.2.3.4\t1080", "socks5h://1.2.3.4:1080"),
        ("1.2.3.4:1080 user:pass", "socks5h://user:pass@1.2.3.4:1080"),
        ("user:pass 1.2.3.4:1080", "socks5h://user:pass@1.2.3.4:1080"),
        ("1.2.3.4,1080,user,pass", "socks5h://user:pass@1.2.3.4:1080"),
        ("[2001:db8::1]:1080", "socks5h://[2001:db8::1]:1080"),
        ("user:pass@[2001:db8::1]:1080", "socks5h://user:pass@[2001:db8::1]:1080"),
        ("localhost:9050", "socks5h://localhost:9050"),
        ("ftp://1.2.3.4:21", ""),
        ("garbage", ""),
        ("1.2.3.4:99999", ""),
        ("# comment", ""),
        ("", ""),
    ]

    def test_formats(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(proxy.normalize_proxy(text), expected)

    def test_default_scheme_applies_to_bare_entries(self):
        self.assertEqual(proxy.normalize_proxy("1.2.3.4:8080", "http"), "http://1.2.3.4:8080")

    def test_mask_hides_password(self):
        self.assertEqual(proxy.mask("socks5h://user:secret@h.example.com:1"), "socks5h://user:***@h.example.com:1")


class _Response:
    def __init__(self, code, payload=None):
        self.status_code = code
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, headers=None, timeout=None):
        for key, value in self.routes.items():
            if key in url:
                return value
        raise AssertionError(url)


class BanCheckTests(unittest.TestCase):
    def setUp(self):
        self._delay = health._RETRY_DELAY
        health._RETRY_DELAY = 0

    def tearDown(self):
        health._RETRY_DELAY = self._delay

    def check(self, routes):
        return health.check_account("acc", {"cookie": "x", "user_id": 5}, _Session(routes))

    def test_ok(self):
        r = self.check({"authenticated": _Response(200, {"id": 5}), "not-approved": _Response(200, {}),
                        "users/5": _Response(200, {"isBanned": False})})
        self.assertEqual(r.status, health.OK)

    def test_banned(self):
        r = self.check({"authenticated": _Response(200, {"id": 5}),
                        "not-approved": _Response(200, {"punishmentTypeDescription": "Ban 1 Day"}),
                        "users/5": _Response(200, {"isBanned": False})})
        self.assertEqual(r.status, health.BANNED)

    def test_locked(self):
        r = self.check({"authenticated": _Response(401), "users/5": _Response(200, {"isBanned": False})})
        self.assertEqual(r.status, health.LOCKED)

    def test_terminated(self):
        r = self.check({"authenticated": _Response(401), "users/5": _Response(200, {"isBanned": True})})
        self.assertEqual(r.status, health.TERMINATED)

    def test_security_check_flag_is_listed_but_not_a_delete_problem(self):
        r = health.check_account(
            "acc", {"cookie": "x", "user_id": 5, health.SECURITY_CHECK_KEY: "2026-10-19 02:00"},
            _Session({"authenticated": _Response(200, {"id": 5}), "not-approved": _Response(200, {}),
                      "users/5": _Response(200, {"isBanned": False})}))
        self.assertEqual(r.status, health.NEEDS_VERIFY)
        self.assertNotIn(r.status, health.PROBLEM_STATUSES)

    def test_ban_outranks_security_check(self):
        r = health.check_account(
            "acc", {"cookie": "x", "user_id": 5, health.SECURITY_CHECK_KEY: "x"},
            _Session({"authenticated": _Response(200, {"id": 5}),
                      "not-approved": _Response(200, {"punishmentTypeDescription": "Ban 1 Day"}),
                      "users/5": _Response(200, {"isBanned": False})}))
        self.assertEqual(r.status, health.BANNED)

    def test_server_error_is_unknown_not_deleted(self):
        r = self.check({"authenticated": _Response(500), "users/5": _Response(200, {"isBanned": False})})
        self.assertEqual(r.status, health.UNKNOWN)
        self.assertNotIn(r.status, health.PROBLEM_STATUSES)


class ParallelBanCheckTests(unittest.TestCase):
    def test_all_accounts_checked_with_retry_after_rate_limit(self):
        import threading
        from unittest.mock import patch

        calls = {"n": 0}
        lock = threading.Lock()

        def fake(username, data, session):
            with lock:
                calls["n"] += 1
                first_hit = calls["n"] == 5
            if first_hit:
                raise health._RateLimited()
            return health.HealthResult(username, health.OK)

        manager = type("M", (), {"accounts": {f"a{i}": {"cookie": "c"} for i in range(40)}})()
        done = threading.Event()
        summary = {}
        with patch.object(health, "check_account", side_effect=fake), \
                patch.object(health, "_RATE_LIMIT_PAUSE", 0.05):
            checker = health.AccountHealthChecker(
                manager, on_result=lambda *a: None,
                on_done=lambda s: (summary.update(s), done.set()),
                delay_sec=0.5, workers=6,
            )
            checker.start()
            self.assertTrue(done.wait(20))
        self.assertEqual(summary["checked"], 40)
        self.assertEqual(summary["counts"][health.OK], 40)


if __name__ == "__main__":
    unittest.main()
